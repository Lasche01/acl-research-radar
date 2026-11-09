"""Synthetic regressions for the failures identified in diagnostics (6).
The public diagnostic has counts, not complete real RFP bodies; these are not
claims of validated real-world precision, recall or application eligibility.
"""
import base64
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from aclbrief.collect import extract_file, page_payload
from aclbrief.documents import document_role, required_attachment, screening_warnings
from aclbrief.incremental import collect_incremental
from aclbrief.rules import RuleReviewer
from aclbrief.screening import fingerprint
from aclbrief.service import Service
from aclbrief.triage import title_screen, period_from_text
from test_rules_queue import p, GOAL, CALL, QUAL, DATE

PROFILE=json.loads((Path(__file__).resolve().parents[1]/'aclbrief/data/profile.json').read_text())

def assess(**kw):return RuleReviewer().assess(p(**kw),copy.deepcopy(PROFILE))

def archive(items):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        for name,content in items:z.writestr(name,content)
    return b.getvalue()

class WordingAndGraphTests(unittest.TestCase):
    def test_adding_this_announcement_does_not_erase_research_evidence(self):
        normal=assess();changed=assess(text=p()['text'].replace(GOAL,'본 공고의 '+GOAL))
        self.assertEqual(changed['decision'],'recommended')
        self.assertEqual(changed['score'],normal['score'])

    def test_common_new_support_call_wording_is_recognized(self):
        result=assess(text=p()['text'].replace(CALL,'2026년도 이동통신 빔 탐색 신규지원 대상과제 공고'))
        self.assertEqual(result['gates']['opportunity']['verdict'],'yes')
        self.assertEqual(result['decision'],'recommended')

    def test_technical_heading_and_table_values_can_span_lines(self):
        text=p()['text'].replace(GOAL,'연구 목표\n이동통신의 빔 탐색 및 CSI 피드백\n알고리즘을 개발하고 성능을 검증한다.')
        result=assess(text=text)
        self.assertEqual(result['decision'],'recommended')
        self.assertIn('\n',result['gates']['communications']['quote'])

    def test_korean_spacing_and_particles_do_not_erase_topic_matches(self):
        result=assess(text=p()['text'].replace(GOAL,'연구 목표: CSI를 추정하고 채널상태정보 피드백 알고리즘을 개발한다.'))
        self.assertEqual(result['decision'],'recommended')
        self.assertTrue(any(m['topic_id']=='csi' for m in result['topic_matches']))

    def test_propagation_modeling_is_relevant_but_not_invented_direct_expertise(self):
        result=assess(text=p()['text'].replace(GOAL,'연구 목표: 주파수 간섭분석 및 전파예측 모델을 개발하고 성능을 평가한다.'))
        self.assertEqual(result['gates']['communications']['verdict'],'yes')
        self.assertEqual(result['gates']['lab_fit']['verdict'],'unknown')
        self.assertTrue(result['related_candidate']);self.assertGreater(result['score'],0)
        self.assertEqual(result['decision'],'review')
        self.assertTrue(all(m['relation']=='related' for m in result['topic_matches']))

    def test_missing_eligibility_does_not_reset_research_priority(self):
        full=assess();held=assess(text=p()['text'].replace(QUAL,''))
        self.assertEqual(held['decision'],'review');self.assertEqual(held['score'],full['score'])
        self.assertTrue(held['related_candidate'])
        self.assertEqual(sum(held['score_breakdown'].values()),held['score'])

    def test_inactive_topics_are_not_reintroduced_by_related_rules(self):
        profile=copy.deepcopy(PROFILE)
        for node in profile['nodes']:
            if node['type']=='research_topic':node['enabled']=False
        result=RuleReviewer().assess(p(),profile)
        self.assertEqual(result['topic_matches'],[]);self.assertEqual(result['links'],[])
        self.assertEqual(result['decision'],'review')

    def test_matching_evidence_text_is_preferred_over_publication_recency(self):
        profile=copy.deepcopy(PROFILE)
        profile['nodes'].append({'id':'older_matching','type':'publication','label':'빔 탐색 연구','abstract':'빔 탐색 알고리즘의 설계와 검증','year':2000})
        profile['edges'].append({'from':'older_matching','to':'beam','relation':'topic_linked_by_user'})
        result=RuleReviewer().assess(p(),profile)
        match=next(m for m in result['topic_matches'] if m['topic_id']=='beam')
        self.assertEqual(match['evidence_id'],'older_matching')
        self.assertIn('빔 탐색',match['evidence_terms'])

    def test_agency_name_does_not_turn_translation_into_communications(self):
        title='[한국전자통신연구원] 자동번역을 위한 다국어 개체명 인식의 성능 개선에 관한 연구'
        self.assertEqual(title_screen(title)['state'],'unconfirmed')
        self.assertEqual(title_screen('[한국전자통신연구원] 수중 통신채널 모델 연구 공모')['state'],'candidate')
        self.assertEqual(title_screen('[6G] 무선통신 파형 연구 모집')['state'],'candidate')

    def test_non_call_document_does_not_pass_because_communications_word_present(self):
        self.assertEqual(title_screen('무선통신 사업 선정 결과')['state'],'excluded')

    def test_technical_call_title_does_not_replace_missing_research_content(self):
        result=assess(text='2026년도 이동통신 CSI 추정 연구개발 신규지원 대상과제 공고\n'+QUAL+'\n'+DATE)
        self.assertEqual(result['gates']['opportunity']['verdict'],'yes')
        self.assertEqual(result['gates']['communications']['verdict'],'unknown')
        self.assertEqual(result['decision'],'review')

class EligibilityScopeTests(unittest.TestCase):
    def test_enterprise_only_guarantee_exemption_is_not_application_exclusion(self):
        result=assess(text=p()['text']+'\n중소기업에 한정하여 보증보험 제출을 면제한다.')
        self.assertEqual(result['gates']['eligibility']['verdict'],'yes')
        self.assertEqual(result['decision'],'recommended')

    def test_enterprise_only_guarantee_without_university_proof_stays_unknown(self):
        result=assess(text=p()['text'].replace(QUAL,'중소기업에 한정하여 보증보험 제출을 면제한다.'))
        self.assertEqual(result['gates']['eligibility']['verdict'],'unknown')
        self.assertEqual(result['decision'],'review')

    def test_explicit_enterprise_only_application_is_excluded(self):
        for qual in ('신청 자격: 중소기업만 신청 가능합니다.','지원 대상은 중소기업에 한정합니다.','대학은 본 과제에 신청 불가합니다.'):
            with self.subTest(qual=qual):
                result=assess(text=p()['text'].replace(QUAL,qual))
                self.assertEqual(result['gates']['eligibility']['verdict'],'no')
                self.assertEqual(result['decision'],'excluded')

    def test_conflicting_permissions_require_scope_review_instead_of_overwrite(self):
        result=assess(text=p()['text']+'\n신청 자격: 중소기업만 신청 가능합니다.')
        self.assertEqual(result['gates']['eligibility']['verdict'],'unknown')
        self.assertEqual(result['decision'],'review')
        self.assertTrue(result['eligibility_checks']['positive']);self.assertTrue(result['eligibility_checks']['negative'])

    def test_enterprise_lead_and_mandatory_consortium_do_not_auto_qualify_university(self):
        for condition in ('주관기관은 기업 주관이 필수입니다.','기업과 대학의 컨소시엄 참여가 필수입니다.'):
            with self.subTest(condition=condition):
                result=assess(text=p()['text']+'\n'+condition)
                self.assertEqual(result['gates']['eligibility']['verdict'],'unknown')
                self.assertEqual(result['decision'],'review')

    def test_legislation_reference_alone_does_not_invent_university_eligibility(self):
        result=assess(text=p()['text'].replace(QUAL,'신청 자격은 관련 법률 제6조제1항에 따릅니다.'))
        self.assertEqual(result['gates']['eligibility']['verdict'],'unknown')

    def test_general_law_and_forms_do_not_override_the_actual_announcement(self):
        text=p()['text']+'\n[첨부 국가연구개발혁신법 시행령.hwp]\n신청 자격: 중소기업만 신청 가능합니다.\n[첨부 신청서 양식.txt]\n신청 자격: 기업만 신청 가능합니다.'
        result=assess(text=text)
        self.assertEqual(result['gates']['eligibility']['verdict'],'yes')
        self.assertEqual(result['decision'],'recommended')

class DocumentRoleTests(unittest.TestCase):
    def test_optional_manual_cannot_supply_missing_eligibility_or_research_evidence(self):
        text=CALL+'\n'+DATE+'\n[첨부 IRIS 접수 매뉴얼.txt]\n'+GOAL+'\n'+QUAL
        result=assess(text=text)
        self.assertEqual(result['gates']['eligibility']['verdict'],'unknown')
        self.assertEqual(result['gates']['lab_fit']['verdict'],'unknown')
        self.assertEqual(result['decision'],'review')

    def test_manual_ocr_and_reference_zip_errors_do_not_block_valid_rfp(self):
        warnings=['IRIS 접수 매뉴얼.pdf 1쪽: OCR 미지원으로 수동 확인 필요',
                  'ZIP 미처리: 첨부자료/국가연구개발혁신법 시행령.hwp / 첨부 주소가 HTML을 반환했습니다.']
        result=assess(warnings=warnings,attachments=[{'name':'IRIS 접수 매뉴얼.pdf','status':'review','required_for_screening':True}])
        self.assertEqual(result['decision'],'recommended');self.assertEqual(result['blocking_issues'],[])
        self.assertTrue(result['optional_issues'])

    def test_real_rfp_unreadable_and_unknown_attachments_still_block(self):
        for name in ('RFP.pdf','참고자료/무선통신 RFP.pdf','정체불명.pdf'):
            with self.subTest(name=name):
                result=assess(attachments=[{'name':name,'status':'review','error':'unreadable'}])
                self.assertEqual(result['decision'],'review');self.assertTrue(result['blocking_issues'])

    def test_primary_rfp_wins_over_optional_directory_name(self):
        self.assertEqual(document_role('참고자료/RFP.pdf'),'rfp')
        self.assertTrue(required_attachment('참고자료/RFP.pdf'))
        self.assertFalse(required_attachment('RFP와 공고자료/IRIS 접수 매뉴얼.pdf'))

    def test_core_rfp_ocr_is_not_downgraded_to_optional_warning(self):
        result=assess(warnings=['RFP.pdf 1쪽: OCR 미지원으로 수동 확인 필요'])
        self.assertEqual(result['decision'],'review');self.assertTrue(result['blocking_issues'])

    def test_known_rfp_is_required_even_if_old_metadata_flag_says_optional(self):
        result=assess(attachments=[{'name':'RFP.pdf','status':'review','required_for_screening':False}])
        self.assertEqual(result['decision'],'review')

    def test_old_optional_manual_date_does_not_close_current_call(self):
        text=p()['text']+'\n[첨부 IRIS 접수 매뉴얼.pdf]\n접수마감일: 2018.01.01'
        self.assertEqual(period_from_text(text)['end_date'],'2099-12-31')
        self.assertEqual(assess(text=text)['decision'],'recommended')

    def test_zip_with_only_optional_files_succeeds_without_parsing_them(self):
        blob=archive([('IRIS 접수 매뉴얼.pdf',b'bad pdf'),('국가연구개발혁신법.hwp',b'bad hwp')])
        content,warnings=extract_file(blob,'첨부자료.zip')
        self.assertEqual(content,'');self.assertTrue(warnings)

    def test_missing_primary_file_in_zip_remains_an_error(self):
        with self.assertRaises(ValueError):extract_file(archive([('RFP.hwp',b'bad')]),'첨부자료.zip')

class UpgradeAndTraceTests(unittest.TestCase):
    def test_reanalysis_preserves_raw_version_and_restores_the_fixed_case(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);payload=p(text=p()['text'].replace(GOAL,'본 공고의 '+GOAL));n=s.ingest(payload)
            analysis=s.store.notice(n['id'])['analysis'];analysis['screening']['policy']='acl-opportunity-5.0'
            s.store.set_analysis(n['id'],analysis)
            self.assertEqual(s.notice_detail(n['id'])['screening']['decision'],'review')
            s.reanalyze()
            self.assertEqual(s.notice_detail(n['id'])['screening']['decision'],'recommended')
            self.assertEqual(len(s.store.versions(n['id'])),1)
            self.assertEqual(s.notice_detail(n['id'])['payload']['text'],payload['text'])

    def test_manual_evidence_can_resolve_previous_metadata_hold(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);n=s.ingest(p(text=p()['text'].replace(DATE,''),collection_state='deadline_unknown_metadata_only'))
            r=s.import_capture({'notice_id':n['id'],'text':p()['text'],'files':[]})
            self.assertEqual(s.notice_detail(r['id'])['screening']['decision'],'recommended')
            self.assertNotIn('collection_state',s.notice_detail(r['id'])['payload'])

    def test_v051_title_cache_is_reconsidered_but_expired_cache_is_retained(self):
        config={'id':'test','url':'https://e.org/list'}
        identity=fingerprint({'url':config['url'],'pattern':None,'id':'test','queue_policy':'deadline-title-v2'})
        old={'identity':identity,'terminal':{'closed':{'kind':'expired'},'title':{'kind':'title_unconfirmed'}}}
        saved={}
        class F:
            def fetch(self,url):return b'<a href="?page=2">2</a>',{},url
        collect_incremental(config,F(),lambda p:None,old,lambda c:saved.update(c))
        self.assertEqual(saved['terminal'],{'closed':{'kind':'expired'}})

    def test_public_diagnostics_expose_literal_quote_file_and_score_components(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);payload=p(source_id='iris',text=CALL+'\n'+DATE+'\n[첨부 무선통신 RFP.txt]\n'+GOAL+'\n'+QUAL)
            s.ingest(payload);sample=s.diagnostics()['screening_samples']['detailed'][0]
            self.assertIn(GOAL,sample['gates']['communications']['quote'])
            self.assertEqual(sample['gates']['communications']['source'],'무선통신 RFP.txt')
            self.assertEqual(sum(sample['score_breakdown'].values()),sample['score'])
            self.assertNotIn(payload['text'],json.dumps(sample,ensure_ascii=False))

    def test_related_candidate_appears_in_briefing_without_claiming_recommendation(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);s.ingest(p(text=p()['text'].replace(QUAL,'')))
            report=s.briefing()
            self.assertIn('## 관련 후보 — 신청 조건·직접 실적 확인 필요',report)
            self.assertIn('### '+p()['title'],report)
            self.assertIn('추천 확정이 아닙니다',report)
            self.assertEqual(s.state()['screening_summary']['recommended'],0)

    def test_expired_related_candidate_is_removed_from_candidate_summary(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);n=s.ingest(p(text=p()['text'].replace(QUAL,'')))
            analysis=s.store.notice(n['id'])['analysis']
            analysis['screening']['deadline_date']='2020-01-01'
            s.store.set_analysis(n['id'],analysis)
            current=s.notice_detail(n['id'])['screening']
            self.assertEqual(current['decision'],'excluded');self.assertFalse(current['related_candidate'])
            self.assertEqual(s.state()['screening_summary']['related_candidates'],0)

    def test_optional_failed_file_does_not_remain_in_collection_retry_queue(self):
        saved={}
        class F:
            def fetch(self,url):
                if '/list' in url:return '<a href="/view/1">통신 연구과제 모집 공고</a>'.encode(),{},url
                return ('<div class="view-cont">'+p()['text']+'</div><a href="/download?file=1">IRIS 접수 매뉴얼.pdf</a>').encode(),{},url
        r=collect_incremental({'id':'test','url':'https://e.org/list'},F(),lambda p:None,None,lambda c:saved.update(c))
        self.assertEqual(r['pending_details'],0);self.assertEqual(r['attachment_errors'],[])

if __name__=='__main__':unittest.main()
