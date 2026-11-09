"""Regressions reproduced from the v0.5.0 collection diagnostics."""
import copy
import json
import tempfile
import unittest
from aclbrief.collect import PageParser, page_payload
from aclbrief.incremental import collect_incremental
from aclbrief.service import Service
from aclbrief.screening import fingerprint
from aclbrief.triage import metadata_payload, title_screen
from test_rules_queue import p, DATE, QUAL

ROOT='https://example.org'

class FakeFetcher:
    def __init__(self,pages):self.pages=pages;self.calls=[]
    def fetch(self,url):
        self.calls.append(url)
        return self.pages[url].encode(),{},url

class DiscoveryTests(unittest.TestCase):
    def scan(self,pages,checkpoint=None):
        self.saved={};f=FakeFetcher(pages)
        r=collect_incremental({'id':'test','url':ROOT+'/list'},f,lambda p: {'saved':True},checkpoint,
            lambda c:setattr(self,'saved',copy.deepcopy(c)),max_pages=100)
        return r,f.calls

    def test_empty_list_does_not_follow_thousands_of_numbered_pages(self):
        r,calls=self.scan({ROOT+'/list':'<a href="?page=2">2</a><a href="?page=7719">7719</a>'})
        self.assertEqual(calls,[ROOT+'/list'])
        self.assertEqual(r['pending_pages'],0)
        self.assertEqual(r['list_error'],'detail_links_missing')
        self.assertEqual(r['status'],'failed')

    def test_empty_later_page_also_clears_remaining_page_queue(self):
        pages={ROOT+'/list':'<a href="/view/1">AI 제조 신규과제 모집</a><a href="?page=2">2</a><a href="?page=3">3</a>',
               ROOT+'/list?page=2':'<a href="?page=3">3</a>'}
        r,calls=self.scan(pages)
        self.assertEqual(calls,[ROOT+'/list',ROOT+'/list?page=2'])
        self.assertEqual(r['status'],'partial');self.assertEqual(r['pending_pages'],0)

    def test_v050_empty_archive_queue_and_old_title_policy_cache_are_reset(self):
        identity=fingerprint({'url':ROOT+'/list','pattern':None,'id':'test','queue_policy':'deadline-title-v1'})
        cache={ROOT+'/view/9':{'kind':'title_unconfirmed','signature':'saved'}}
        checkpoint={'identity':identity,'pages':[[7719,ROOT+'/list?page=7719']],'terminal':cache}
        self.scan({ROOT+'/list':'<a href="?page=2">2</a>'},checkpoint)
        self.assertEqual(self.saved['terminal'],{});self.assertEqual(self.saved['pages'],[])

    def test_service_does_not_relabel_adapter_failure_as_partial_success(self):
        f=FakeFetcher({ROOT+'/list':'<a href="?page=2">2</a>'})
        with tempfile.TemporaryDirectory() as d:
            s=Service(d,f)
            for src in s.store.sources():
                s.store.update_source(src['id'],{**src['config'],'enabled':src['id']=='kca',
                    **({'url':ROOT+'/list'} if src['id']=='kca' else {})})
            r=s.collect();row=r['sources'][0]
            self.assertEqual(row['status'],'failed');self.assertEqual(row['diagnostic']['code'],'detail_links_missing')
            self.assertEqual(s.state()['collection_summary']['adapter_needed'],1)
            self.assertEqual(len(f.calls),1)

class MetadataTests(unittest.TestCase):
    def fetch_payload(self,metadata,article=None):
        article=article or p()['text'].replace(DATE,'').replace(QUAL,'')
        html=metadata+'<div class="view-cont">'+article+'</div><a href="/download?file=1">RFP.txt</a>'
        f=FakeFetcher({ROOT+'/view/1':html,ROOT+'/download?file=1':'추출한 필수 연구 내용입니다.'})
        return page_payload(ROOT+'/view/1',f,title=p()['title'],staged=True),f.calls

    def test_definition_period_outside_selected_article_unlocks_attachment(self):
        result,calls=self.fetch_payload('<dl><dt>접수기간</dt><dd>2020.01.01 ~ 2099.12.31</dd></dl>')
        self.assertEqual(result['collection_triage']['period']['state'],'open')
        self.assertEqual(len(calls),2)
        self.assertIn('접수기간\n2020.01.01 ~ 2099.12.31',result['text'])
        self.assertGreater(result['attachments'][0]['extracted_chars'],0)

    def test_table_metadata_preserves_period_and_university_proof(self):
        metadata='<table><tr><th>접수기간</th><td>2020.01.01 ~ 2099.12.31</td><th>신청 자격</th><td>국내 대학 및 산학협력단은 수행기관으로 직접 신청할 수 있습니다.</td></tr></table>'
        result,calls=self.fetch_payload(metadata)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(Service(d).analyze(result)['screening']['decision'],'recommended')

    def test_expired_metadata_outside_article_never_downloads_attachment(self):
        result,calls=self.fetch_payload('<dl><dt>접수기간</dt><dd>2020.01.01 ~ 2020.12.31</dd></dl>')
        self.assertEqual(result['collection_state'],'expired_metadata_only');self.assertEqual(len(calls),1)

    def test_publication_and_business_period_do_not_unlock_attachments(self):
        result,calls=self.fetch_payload('<dl><dt>게시일</dt><dd>2026.11.07</dd><dt>사업기간</dt><dd>2026.01.01 ~ 2099.12.31</dd></dl>')
        self.assertEqual(result['collection_state'],'deadline_unknown_metadata_only');self.assertEqual(len(calls),1)

    def test_definition_labels_do_not_leak_across_definition_lists(self):
        parser=PageParser();parser.feed('<dl><dt>접수기간</dt></dl><dl><dd>2099.12.31</dd></dl>')
        self.assertEqual(parser.result()['metadata'],[])

class DiagnosticAndInboxTests(unittest.TestCase):
    def test_title_holds_are_separate_without_deleting_stored_history(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);title='AI 산업 연구개발 신규과제 모집'
            held=metadata_payload(ROOT+'/view/1',title,'kca',{'state':'unknown'},title_screen(title),'title_metadata_only')
            a=s.ingest(held);s.ingest(p(url=ROOT+'/view/2',source_id='kca',text=p()['text'].replace(DATE,''),collection_state='deadline_unknown_metadata_only'))
            s.ingest(p(url=ROOT+'/view/3',source_id='kca'))
            state=s.state();summary=state['screening_summary']
            self.assertEqual((summary['title_pending'],summary['review'],summary['recommended']),(1,1,1))
            self.assertEqual(len(state['notices']),3);self.assertEqual(len(s.store.versions(a['id'])),1)
            self.assertEqual(next(n for n in state['notices'] if n['id']==a['id'])['screening_stage'],'title')

    def test_diagnostics_include_per_gate_outcomes_and_file_extraction_counts(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);s.ingest(p(url=ROOT+'/view/1',source_id='kca',attachments=[{'name':'RFP.txt','status':'extracted','extracted_chars':400}]))
            diag=s.diagnostics();sample=diag['screening_samples']['detailed'][0]
            self.assertEqual(sample['gates']['lab_fit']['verdict'],'yes')
            self.assertEqual(sample['attachments'][0]['extracted_chars'],400)
            self.assertNotIn(p()['text'],json.dumps(diag,ensure_ascii=False))
            self.assertLessEqual(len(sample['gates']['lab_fit']['quote']),700)

    def test_manual_input_and_feedback_are_not_exported_as_public_samples(self):
        with tempfile.TemporaryDirectory() as d:
            s=Service(d);s.ingest(p(url=ROOT+'/view/1',source_id='manual'))
            n=s.ingest(p(url=ROOT+'/view/2',source_id='kca'))
            s.store.feedback(n['id'],n['version'],'uncertain','private judgement','train')
            self.assertEqual(s.diagnostics()['screening_samples']['detailed'],[])

if __name__=='__main__':unittest.main()
