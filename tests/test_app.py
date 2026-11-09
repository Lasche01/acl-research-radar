import base64
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch
from aclbrief.collect import collect_source, extract_file, PageParser, public_url, page_payload
from aclbrief.matcher import Matcher, LocalAI, cosine, term_in, validate_profile
from aclbrief.service import Service, DEMO
from aclbrief.server import AppServer
from aclbrief.store import canonical_url


class FixtureFetcher:
    def __init__(self, pages): self.pages=pages
    def fetch(self, url, **kwargs):
        value=self.pages[url]
        if isinstance(value,Exception): raise value
        return value.encode() if isinstance(value,str) else value, {'Content-Type':'text/html; charset=utf-8'}, url


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=Service(self.temp.name)

    def tearDown(self): self.temp.cleanup()

    def payload(self,**overrides):
        return {**DEMO[0], 'url':'https://example.org/view?id=1', **overrides}

    def test_generic_ai_optics_is_not_wireless_evidence(self):
        a=self.service.matcher().analyze(DEMO[1])
        self.assertEqual(a['category'],'low')
        self.assertEqual(a['mappings'],[])

    def test_specific_beam_requirements_have_traceable_paths(self):
        a=self.service.matcher().analyze(DEMO[0])
        self.assertEqual(a['category'],'candidate')
        self.assertEqual(a['mode'],'graph_rules')
        for m in a['mappings']:
            self.assertIn(m['quote'],DEMO[0]['text'])
            self.assertIn(m['evidence_id'],self.service.matcher().evidence)
            self.assertTrue(m['url'].startswith('https://'))

    def test_underwater_transfer_is_not_direct(self):
        self.assertEqual(self.service.matcher().analyze(DEMO[3])['category'],'expand')

    def test_unsplit_announcement_is_review(self):
        self.assertEqual(self.service.matcher().analyze(DEMO[4])['category'],'review')

    def test_missing_detail_is_review_not_absence(self):
        self.assertEqual(self.service.matcher().analyze({'text':'사업공고 내용 확인이 필요합니다.','scope':'notice'})['category'],'review')

    def test_acronyms_do_not_match_inside_words(self):
        self.assertFalse(term_in('ai','paid repair'))
        self.assertFalse(term_in('ftn','softnetwork'))
        self.assertTrue(term_in('csi','CSI의 피드백'))

    def test_disabled_topic_removes_path(self):
        p=self.service.store.get_setting('profile')
        for n in p['nodes']:
            if n['type']=='research_topic': n['enabled']=False
        self.assertEqual(Matcher(p).analyze(DEMO[0])['mappings'],[])

    def test_profile_rejects_dangling_edges(self):
        p=self.service.store.get_setting('profile');p['edges'].append({'from':'missing','to':'beam'})
        with self.assertRaises(ValueError):validate_profile(p)

    def test_unchanged_ingest_preserves_version(self):
        a=self.service.ingest(self.payload())
        b=self.service.ingest(self.payload())
        self.assertEqual(b['change'],'unchanged')
        self.assertEqual(len(self.service.store.versions(a['id'])),1)

    def test_changed_deadline_and_text_preserve_prior_snapshot(self):
        old=self.payload(deadline='2026-11-10T14:00:00+09:00')
        first=self.service.ingest(old)
        second=self.service.ingest({**old,'text':old['text']+' 추가 성능 검증이 필요하다.','deadline':'2026-11-12T14:00:00+09:00'})
        self.assertEqual(first['id'],second['id']);self.assertEqual(second['version'],2)
        self.assertEqual(self.service.store.versions(first['id'])[1]['payload']['text'],old['text'])

    def test_rfp_units_at_same_url_stay_separate(self):
        a=self.service.ingest(self.payload(unit='RFP-1'))
        b=self.service.ingest(self.payload(unit='RFP-2'))
        self.assertNotEqual(a['id'],b['id'])

    def test_reanalysis_does_not_change_source_version(self):
        r=self.service.ingest(self.payload());self.service.reanalyze()
        self.assertEqual(self.service.store.notice(r['id'])['version'],1)

    def test_stale_feedback_cannot_overwrite_new_version(self):
        r=self.service.ingest(self.payload())
        self.service.ingest(self.payload(text=DEMO[0]['text']+' 추가 요구사항'))
        with self.assertRaises(ValueError):self.service.store.feedback(r['id'],1,'relevant','이전 버전')

    def test_feedback_does_not_modify_prediction_or_leak_into_eval(self):
        r=self.service.ingest(self.payload());before=self.service.store.notice(r['id'])['analysis']
        self.service.store.feedback(r['id'],1,'irrelevant','지원 실험 조건이 맞지 않음','train')
        self.assertEqual(before,self.service.store.notice(r['id'])['analysis'])
        self.assertEqual(self.service.evaluate()['methods']['graph']['n'],0)

    def test_test_split_metrics_and_old_version_exclusion(self):
        r=self.service.ingest(self.payload())
        self.service.store.feedback(r['id'],1,'relevant','빔 탐색 직접 활용','test')
        # Offline keyword candidates are not recommendations in v0.2.
        self.assertEqual(self.service.evaluate()['methods']['graph']['fn'],1)
        self.service.ingest(self.payload(text=DEMO[0]['text']+' 추가 변경'))
        self.assertEqual(self.service.evaluate()['methods']['graph']['n'],0)

    def test_demo_is_excluded_from_briefing_and_metrics(self):
        self.service.demo()
        n=self.service.store.notices()[0]
        self.service.store.feedback(n['id'],1,'relevant','가상 예제','test')
        self.assertNotIn('[예제]',self.service.briefing())
        self.assertEqual(self.service.evaluate()['methods']['graph']['n'],0)

    def test_eligibility_requires_explicit_evidence(self):
        with self.assertRaises(ValueError):self.service.ingest(self.payload(eligibility='eligible'))

    def test_url_normalization_preserves_notice_identifier(self):
        self.assertEqual(canonical_url('https://example.org/view?id=3&utm_source=x#top'),'https://example.org/view?id=3')
        with self.assertRaises(ValueError):canonical_url('file:///etc/passwd')

    def test_public_fetch_rejects_private_network(self):
        with self.assertRaises(ValueError):public_url('http://127.0.0.1:8765/api/export')

    def test_dynamic_empty_list_is_not_zero_success(self):
        config={'url':'https://example.org/list','id':'test','detail_pattern':'/view'}
        result=collect_source(config,FixtureFetcher({config['url']:'<html><body><div id="app"></div></body></html>'}),lambda p:None)
        self.assertEqual(result['status'],'review')
        self.assertIn(' 신규 0건',result['error'])

    def test_collection_limits_and_attachment_failure_are_visible(self):
        root='https://example.org';page='<html><body><main>'+'이동통신 채널 추정 요구사항. '*15+'<a href="/download/test.hwp">과제.hwp</a></main></body></html>'
        f=FixtureFetcher({root+'/list':'<a href="/view/1">공고 제목 일</a><a href="/view/2">공고 제목 이</a>',root+'/view/1':page,root+'/download/test.hwp':b'\xd0\xcf\x11\xe0'})
        result=collect_source({'url':root+'/list','id':'test','detail_pattern':'/view/'},f,self.service.ingest,limit=1)
        self.assertEqual(result['status'],'partial');self.assertEqual(result['processed'],1)
        self.assertTrue(self.service.store.notices()[0]['analysis']['warnings'])

    def test_hwpx_extraction_preserves_paragraphs(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('Contents/section0.xml','<root xmlns:p="urn:p"><p:p><p:t>빔 탐색 연구</p:t></p:p><p:p><p:t>연구 성과물</p:t></p:p></root>')
        text,w=extract_file(b.getvalue(),'test.hwpx')
        self.assertIn('빔 탐색 연구\n연구 성과물',text)

    def test_binary_hwp_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(ValueError,'HWP 바이너리'):extract_file(b'\xd0\xcf\x11\xe0','test.hwp')

    def test_unreadable_pdf_pages_do_not_mean_irrelevant(self):
        p={**DEMO[1],'warnings':['RFP.pdf 1쪽: 이미지/빈 페이지 가능. OCR 미지원으로 수동 확인 필요']}
        self.assertEqual(self.service.matcher().analyze(p)['category'],'review')

    def test_source_failure_keeps_history_and_records_failure(self):
        for s in self.service.store.sources():
            self.service.store.update_source(s['id'],{**s['config'],'enabled':s['id']=='add'})
        self.service.fetcher=FixtureFetcher({'https://www.add.re.kr/kps/publicNtis/ntisList?menuId=MENU02201':RuntimeError('fixture offline')})
        r=self.service.collect()
        self.assertEqual(r['status'],'needs_review')
        self.assertEqual(next(s for s in self.service.store.sources() if s['id']=='add')['status'],'failed')

    def test_ai_unverifiable_quotes_are_rejected(self):
        class AI:
            def status(self):return {'configured':True}
            def embed(self,texts):return [[1.,0.] for t in texts]
            def judge(self,text,candidates,topics):return {'mappings':[{'quote':'fabricated quote never in source','evidence_id':'c2025beam','topic_id':'beam','strength':'direct','reason':'fake','gap':''}]}
        a=Matcher(self.service.store.get_setting('profile'),AI()).analyze(DEMO[0])
        self.assertEqual(a['mappings'],[])
        self.assertTrue(any('제외' in w for w in a['warnings']))
        self.assertEqual(a['category'],'review')

    def test_ai_failure_falls_back_with_visible_warning(self):
        class AI:
            def status(self):return {'configured':True}
            def embed(self,texts):raise RuntimeError('model offline')
        a=Matcher(self.service.store.get_setting('profile'),AI()).analyze(DEMO[0])
        self.assertEqual(a['mode'],'graph_rules')
        self.assertTrue(any('실패' in w for w in a['warnings']))

    def test_ai_positive_requires_real_graph_edge_and_quote(self):
        quote=DEMO[0]['text'].split('.')[0]+'.'
        class AI:
            def status(self):return {'configured':True}
            def embed(self,texts):return [[1.,0.] for t in texts]
            def judge(self,text,candidates,topics):return {'mappings':[{'quote':quote,'evidence_id':'c2025beam','topic_id':'beam','strength':'direct','reason':'빔 코드북 대응','gap':'실험 환경 검토'}]}
        a=Matcher(self.service.store.get_setting('profile'),AI()).analyze(DEMO[0])
        self.assertEqual(a['mode'],'local_semantic_graph');self.assertEqual(a['category'],'direct')

    def test_new_project_requires_role_and_can_join_graph(self):
        with self.assertRaises(ValueError):self.service.add_evidence({'label':'과제','type':'project','topics':['beam']})
        result=self.service.add_evidence({'label':'빔 탐색 실험 과제','type':'project','topics':['beam'],'sponsor':'시험기관','role':'위탁 알고리즘 개발','year':2025})
        p=self.service.store.get_setting('profile')
        self.assertTrue(any(e['from']==result['id'] and e['to']=='beam' for e in p['edges']))

    def test_sqlite_backup_is_readable(self):
        import sqlite3
        self.service.ingest(self.payload())
        path=Path(self.temp.name)/'copy.sqlite3';self.service.store.backup(path)
        with sqlite3.connect(path) as db:self.assertEqual(db.execute('select count(*) from notices').fetchone()[0],1)

    def test_parallel_services_cannot_run_collectors_in_same_directory(self):
        other=Service(self.temp.name)
        self.assertTrue(self.service.lock.acquire())
        try:
            with self.assertRaisesRegex(ValueError,'진행 중'):other.collect(['add'])
        finally:
            self.service.lock.release()
        self.assertTrue(other.lock.acquire())
        other.lock.release()


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.svc=Service(cls.temp.name);cls.server=AppServer(0,cls.svc)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def request(self,path,data=None,headers=None):
        h={'Content-Type':'application/json',**(headers or {})}
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
        return urllib.request.urlopen(req)

    def test_static_and_api_boot(self):
        with self.request('/') as r:self.assertIn('ACL Research Radar',r.read().decode())
        with self.request('/api/state') as r:self.assertEqual(len(json.load(r)['profile']['nodes']),23)

    def test_cross_origin_write_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/demo',{}, {'Origin':'https://attacker.example'})
        self.assertEqual(e.exception.code,403)

    def test_wrong_host_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.request('/api/export',headers={'Host':'attacker.example'})
        self.assertEqual(e.exception.code,403)

    def test_bulk_selection_api_and_persistence(self):
        try:
            with self.request('/api/sources/enabled', {'enabled': True}) as r:
                self.assertEqual(json.load(r)['updated'], 20)
            with self.request('/api/state') as r:
                self.assertEqual(json.load(r)['source_summary']['enabled'], 20)
            with self.request('/api/sources/enabled', {'enabled': False}) as r:
                self.assertFalse(json.load(r)['enabled'])
            self.assertEqual(Service(self.temp.name).state()['source_summary']['enabled'], 0)
        finally:
            for s in self.svc.store.sources():
                self.svc.store.update_source(s['id'], {**s['config'], 'enabled': s['id'] in ('iris','iitp','etri','koita','ntis','add','nrf','nipa','kca')})

    def test_bulk_selection_api_rejects_string(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request('/api/sources/enabled', {'enabled': 'false'})
        self.assertEqual(e.exception.code, 400)

    def test_end_to_end_ingest_feedback_export(self):
        with self.request('/api/notices',{**DEMO[0],'url':'https://example.org/notice'}) as r:created=json.load(r)
        with self.request('/api/feedback',{'id':created['id'],'version':1,'verdict':'relevant','reason':'실적 연결 확인','split':'test'}) as r:self.assertEqual(r.status,200)
        with self.request('/api/export') as r:
            self.assertIn('attachment',r.headers['Content-Disposition'])
            exported=json.load(r);self.assertTrue(exported['versions']);self.assertTrue(exported['feedback'])
        with self.request('/api/evaluation') as r:self.assertEqual(json.load(r)['methods']['graph']['fn'],1)


if __name__=='__main__': unittest.main()
