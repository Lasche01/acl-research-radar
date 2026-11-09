"""Synthetic regression cases. Tests contract/gates, NOT live model accuracy."""
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from aclbrief.service import Service, DEMO
from aclbrief.screening import Reviewer, GATES, SCHEMA, POLICY, KST

RESEARCH = '다중 안테나 이동통신의 빔 탐색 및 CSI 피드백 알고리즘을 연구하고 성능 평가 시뮬레이터를 개발한다.'
ELIGIBILITY = '국내 대학 및 산학협력단은 본 위탁연구의 수행기관으로 직접 신청할 수 있습니다.'
SOLICITATION = '본 사업은 이동통신 연구개발 신규과제의 수행기관을 공개 모집합니다.'
DEADLINE = '본 과제의 신청 접수 마감일은 2099년 12월 31일입니다.'


def payload(**kwargs):
    return {'title': '이동통신 빔 탐색 위탁연구 모집', 'url': 'https://example.org/view?id=1',
            'text': '\n'.join([SOLICITATION, RESEARCH, ELIGIBILITY, DEADLINE]), 'scope': 'rfp', **kwargs}


def positive(profile, score=87):
    edge = next(e for e in profile['edges'] if e['to'] == 'beam')
    return {**{k: {'verdict': 'yes', 'quote': ELIGIBILITY if k == 'eligibility' else SOLICITATION if k == 'opportunity' else RESEARCH,
                   'reason': '공고 원문에서 해당 연구 요구와 조건을 확인했습니다.'} for k in GATES},
            'deadline_date': '2099-12-31', 'deadline_quote': DEADLINE, 'fit_score': score,
            'summary': '빔 탐색 연구 요구가 연구실의 관련 실적과 대응합니다. 현재 수행 역량은 별도 확인하세요.',
            'links': [{'evidence_id': edge['from'], 'topic_id': 'beam', 'quote': RESEARCH,
                       'reason': '빔 탐색 방법과 직접 대응하는 주제입니다.', 'gap': '공개 제목 수준의 실적이며 현재 인력·장비는 미확인입니다.'}]}


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'ACL_AI_PROVIDER': 'openai', 'OPENAI_API_KEY': 'synthetic-test-key',
                                         'ACL_OPENAI_MODEL': 'test-model', 'ACL_AI_MAX_CALLS': '20'})
        self.env.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = Service(self.tmp.name)
        self.svc.reviewer = Reviewer()
        self.profile = self.svc.store.get_setting('profile')
        self.response = positive(self.profile)
        self.mock = patch.object(self.svc.reviewer, 'request', return_value=self.response).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def record(self, p=None):
        self.svc.ingest(p or payload())
        return self.svc.notice_records()[0]

    def test_positive_recommendation_has_validated_graph_and_quotes(self):
        n = self.record()
        self.assertEqual(n['screening']['decision'], 'recommended')
        self.assertEqual(n['analysis']['mode'], 'openai_screening')
        self.assertIn(n['title'], self.svc.briefing())
        self.assertIn(ELIGIBILITY, self.svc.briefing())
        self.assertEqual(n['analysis']['mappings'][0]['quote'], RESEARCH)

    def test_offline_does_not_promote_even_keyword_rich_rfp(self):
        self.svc.reviewer.enabled = False
        n = self.record()
        self.assertEqual(n['screening']['decision'], 'review')
        self.assertNotIn('## ' + n['title'], self.svc.briefing())
        self.mock.assert_not_called()

    def test_administrative_safety_results_rejected_before_ai(self):
        n = self.record(payload(title='2025년도 공공기관 안전관리등급 심사 결과'))
        self.assertEqual(n['screening']['decision'], 'excluded')
        self.mock.assert_not_called()

    def test_industrial_ai_and_communications_background_are_excluded(self):
        for title in ['2026년 조선 AX 특화 AI 모델하우스 구축·실증사업 신규과제 모집공고', '통신망을 활용한 제조 데이터 AI 분석 구축']:
            with self.subTest(title=title):
                r = copy.deepcopy(self.response)
                r['communications'] = {'verdict': 'no', 'quote': RESEARCH, 'reason': '통신 연구가 아니라 산업 AI 구축이 핵심입니다.'}
                self.mock.return_value = r
                result = self.svc.reviewer.assess(payload(title=title), self.profile)
                self.assertEqual(result['decision'], 'excluded')

    def test_each_gate_can_block_recommendation(self):
        for gate in GATES:
            for verdict, expected in [('no', 'excluded'), ('unknown', 'review')]:
                with self.subTest(gate=gate, verdict=verdict):
                    r = copy.deepcopy(self.response);r[gate]['verdict'] = verdict
                    self.mock.return_value = r
                    self.assertEqual(self.svc.reviewer.assess(payload(), self.profile)['decision'], expected)

    def test_cannot_invent_university_eligibility_from_metadata(self):
        r = copy.deepcopy(self.response);r['eligibility']['quote'] = RESEARCH
        self.mock.return_value = r
        self.assertEqual(self.record(payload(eligibility='eligible', eligibility_note='내가 가능하다고 생각함'))['screening']['decision'], 'review')

    def test_missing_qualification_reference_does_not_promote(self):
        self.response['eligibility'].update(verdict='unknown', quote='', reason='법령 조문만 있고 적용 대상은 미확인')
        self.assertEqual(self.record()['screening']['decision'], 'review')

    def test_forged_quote_and_forged_graph_path_fail_closed(self):
        for key in ['quote', 'evidence_id', 'topic_id']:
            with self.subTest(key=key):
                r=copy.deepcopy(self.response);r['links'][0][key]='fabricated'
                self.mock.return_value=r
                result=self.svc.reviewer.assess(payload(),self.profile)
                self.assertEqual(result['decision'],'review')
                self.assertNotIn('validated',result)

    def test_disabled_profile_topic_invalidates_semantic_path(self):
        next(n for n in self.profile['nodes'] if n['id']=='beam')['enabled']=False
        result=self.svc.reviewer.assess(payload(),self.profile)
        self.assertEqual(result['decision'],'review')

    def test_missing_or_invented_deadline_never_recommended(self):
        for value in ['', '2099-12-30']:
            r=copy.deepcopy(self.response);r['deadline_date']=value;self.mock.return_value=r
            self.assertEqual(self.svc.reviewer.assess(payload(),self.profile)['decision'],'review')

    def test_expired_and_same_day_calls_are_not_recommended(self):
        for day, decision in [(datetime.now(KST).date()-timedelta(days=1),'excluded'),(datetime.now(KST).date(),'review')]:
            quote=f'본 과제의 신청 접수 마감일은 {day.isoformat()}입니다.'
            r=copy.deepcopy(self.response);r.update(deadline_date=day.isoformat(),deadline_quote=quote);self.mock.return_value=r
            self.assertEqual(self.svc.reviewer.assess(payload(text=payload()['text']+'\n'+quote),self.profile)['decision'],decision)

    def test_unread_attachments_truncation_images_do_not_call_api(self):
        cases=[payload(attachments=[{'status':'review'}]), payload(warnings=['페이지 이미지의 상세 내용은 자동 판독하지 않았습니다.']),
               payload(warnings=['첨부파일 8개 중 6개만 처리했습니다.']),payload(text='본문'*60000)]
        for p in cases:self.assertEqual(self.svc.reviewer.assess(p,self.profile)['decision'],'review')
        self.mock.assert_not_called()

    def test_api_failure_and_secret_never_leak_or_fallback_to_recommendation(self):
        self.mock.side_effect=RuntimeError('synthetic-test-key private details')
        n=self.record()
        self.assertEqual(n['screening']['decision'],'review')
        self.assertNotIn('synthetic-test-key', json.dumps(self.svc.store.export()))
        self.assertNotIn('synthetic-test-key', json.dumps(self.svc.state()))

    def test_http_limit_error_is_actionable(self):
        self.mock.side_effect=urllib.error.HTTPError('https://api.openai.com/',429,'rate limit',{},None)
        self.assertIn('한도 또는 잔액',self.record()['screening']['reason'])

    def test_identical_inputs_reuse_analysis_without_version_increment(self):
        self.record(payload(text='  '+payload()['text']+'  '))
        self.record()
        self.svc.reanalyze()
        self.assertEqual(self.mock.call_count,1)
        self.assertEqual(len(self.svc.store.notices()),1)
        self.assertEqual(self.svc.store.notices()[0]['version'],1)

    def test_budget_resume_preserves_success_and_continues_pending(self):
        self.svc.reviewer.limit=1
        self.svc.ingest(payload())
        self.svc.ingest(payload(url='https://example.org/view?id=2'))
        self.assertEqual([n['screening']['decision'] for n in self.svc.notice_records()],['recommended','review'])
        self.svc.reanalyze()
        self.assertEqual([n['screening']['decision'] for n in self.svc.notice_records()],['recommended','recommended'])
        self.assertEqual(self.mock.call_count,2)

    def test_profile_change_invalidates_old_positive_immediately(self):
        self.record();self.profile['scope']+=' 변경';self.svc.save_profile(self.profile)
        self.assertEqual(self.svc.notice_records()[0]['screening']['decision'],'review')

    def test_budget_pending_is_not_starved_by_repeated_failure(self):
        self.svc.reviewer.limit=1
        self.mock.side_effect=RuntimeError('temporary error')
        self.svc.ingest(payload())
        self.svc.ingest(payload(url='https://example.org/view?id=2'))
        self.mock.side_effect=None
        self.svc.reanalyze()
        recommended=[n for n in self.svc.notice_records() if n['screening']['decision']=='recommended']
        self.assertEqual(len(recommended),1)
        self.assertTrue(recommended[0]['url'].endswith('id=2'))

    def test_old_graph_candidate_requires_new_screening(self):
        self.svc.store.upsert(payload(),self.svc.matcher().analyze(payload()))
        self.assertEqual(self.svc.notice_records()[0]['screening']['decision'],'review')
        self.assertNotIn('## '+payload()['title'],self.svc.briefing())

    def test_feedback_excludes_but_does_not_leak_label_into_evaluation(self):
        n=self.record()
        self.svc.store.feedback(n['id'],n['version'],'irrelevant','실제로는 무관','test')
        self.assertEqual(self.svc.notice_records()[0]['screening']['decision'],'excluded')
        self.assertEqual(self.svc.evaluate()['methods']['graph']['fp'],1)

    def test_score_sorting_and_briefing_limit_are_separate_from_storage(self):
        for i in range(22):
            self.svc.reviewer.reset_budget()
            self.mock.return_value=positive(self.profile,70+i)
            self.svc.ingest(payload(title=f'통신 검증 공고 {i}',url=f'https://example.org/view?id={i}'))
        rows=self.svc.notice_records()
        self.assertEqual(len(rows),22)
        self.assertEqual(rows[0]['screening']['score'],91)
        self.assertEqual(self.svc.briefing().count('## 통신 검증 공고'),20)

    def test_api_wire_format_and_refusal_incomplete_handling(self):
        reviewer=Reviewer()
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(self.response)}]}]}).encode()
        opener=Mock();opener.open.return_value=response
        with patch('aclbrief.screening.urllib.request.build_opener',return_value=opener):
            self.assertEqual(reviewer.request(payload(),self.profile),self.response)
            request=opener.open.call_args.args[0];data=json.loads(request.data)
            self.assertEqual(request.full_url,'https://api.openai.com/v1/responses')
            self.assertFalse(data['store']);self.assertTrue(data['text']['format']['strict'])
            self.assertEqual(data['text']['format']['schema'],SCHEMA)
            response.read.return_value=b'{"status":"incomplete","output":[]}'
            with self.assertRaises(ValueError):reviewer.request(payload(),self.profile)
            response.read.return_value=b'{"status":"completed","output":[{"type":"message","content":[{"type":"refusal","refusal":"no"}]}]}'
            with self.assertRaises(ValueError):reviewer.request(payload(),self.profile)


if __name__=='__main__':unittest.main()
