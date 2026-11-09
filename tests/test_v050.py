"""Deadline/title pipeline tests assert actual requested URLs, including no-request branches."""
import copy
import io
import tempfile
import unittest
import urllib.error
from datetime import datetime
from unittest.mock import Mock,patch
from aclbrief.collect import Fetcher,PageParser,list_candidates
from aclbrief.incremental import collect_incremental
from aclbrief.service import Service
from aclbrief.screening import KST
from aclbrief.triage import period_from_text,period_from_quote,title_screen,listing_period
from test_rules_queue import p,GOAL,QUAL,CALL,DATE

NOW=datetime(2026,11,6,12,tzinfo=KST)
def table(title='통신 채널 연구 모집',period='2099.11.01 ~ 2099.11.30',headers=('공고명','접수기간'),extra=''):
 return '<table><tr>'+''.join('<th>'+h+'</th>' for h in headers)+'</tr><tr><td><a href="/view/1">'+title+'</a></td><td>'+period+'</td></tr></table>'+extra

def body(text=None):return '<div class="view-cont">'+(text or p()['text'])+'</div><a href="/download?file=1">RFP.txt</a>'

class PeriodTests(unittest.TestCase):
 def test_short_end_uses_end_not_opening_date(self):
  r=period_from_text('접수기간: 2026.10.15 ~ 11.16',NOW);self.assertEqual(r['end_date'],'2026-11-16');self.assertEqual(r['state'],'open')
 def test_short_range_crossing_new_year(self):
  r=period_from_text('신청기간: 2026.12.28 ~ 01.12',NOW);self.assertEqual(r['end_date'],'2027-01-12');self.assertEqual(r['state'],'upcoming')
 def test_same_day_requires_actual_closing_time(self):
  self.assertEqual(period_from_text('접수마감일: 2026.11.06',NOW)['state'],'unknown')
  self.assertEqual(period_from_text('접수마감일: 2026.11.06 11:00',NOW)['state'],'expired')
  self.assertEqual(period_from_text('접수마감일: 2026.11.06 15시',NOW)['state'],'open')
 def test_program_period_and_publication_are_not_application_dates(self):
  self.assertEqual(period_from_text('게시일: 2020.01.01\n사업기간: 2020.01.01 ~ 2021.12.31',NOW)['state'],'unknown')
 def test_status_field_can_confirm_closed(self):
  self.assertEqual(listing_period({'fields':[{'label':'접수상태','text':'접수마감'}]},NOW)['state'],'expired')
 def test_title_word_closed_does_not_close_a_row(self):
  self.assertEqual(listing_period({'text':'통신과제 접수마감 연장 안내','fields':[]},NOW)['state'],'unknown')
 def test_separate_future_start_column_is_not_open(self):
  r=listing_period({'fields':[{'label':'접수시작일','text':'2099.11.01'},{'label':'접수마감일','text':'2099.11.30'}]},NOW)
  self.assertEqual(r['state'],'upcoming')
 def test_waveform_and_english_communications_titles_are_candidates(self):
  for title in ('비직교 전송 파형 설계 연구 모집','Wireless channel modeling research call','다중반송파 간섭 제거 연구'):
   with self.subTest(title=title):self.assertEqual(title_screen(title)['state'],'candidate')
 def test_invalid_full_range_is_unknown_not_expired(self):
  self.assertEqual(period_from_text('접수기간: 2026.12.28 ~ 2026.01.12',NOW)['state'],'unknown')
 def test_header_dates_are_attached_to_each_link_not_neighbour(self):
  parser=PageParser();parser.feed(table(period='2020.01.01 ~ 2020.02.01'))
  items,_=list_candidates(parser.result(),'https://e.org/list',{'id':'test'})
  self.assertEqual(items[0]['period']['state'],'expired');self.assertEqual(items[0]['title'],'통신 채널 연구 모집')

class PipelineTests(unittest.TestCase):
 def setUp(self):self.saved={};self.calls=[];self.used=[]
 def run_scan(self,html,detail=None,known=None):
  outer=self
  class F:
   def fetch(self,url):
    outer.calls.append(url)
    if '/list' in url:content=html
    elif '/download' in url:content='통신 연구 RFP의 필수 연구내용입니다.'
    elif detail is not None:content=detail
    else:raise AssertionError('detail must not be requested')
    return content.encode(),{},url
  return collect_incremental({'id':'test','url':'https://e.org/list'},F(),lambda payload:self.used.append(payload) or {'saved':True},
      self.saved,lambda c:setattr(self,'saved',copy.deepcopy(c)),known=known)
 def test_closed_listing_skips_detail_and_attachment_before_title(self):
  r=self.run_scan(table(title='조선 AX 모델하우스 신규 모집',period='2020.01.01 ~ 2020.01.31'))
  self.assertEqual(self.calls,['https://e.org/list']);self.assertEqual(self.used,[]);self.assertEqual(r['triage_counts']['expired'],1)
 def test_future_start_skips_detail(self):
  r=self.run_scan(table());self.assertEqual(self.calls,['https://e.org/list']);self.assertEqual(r['triage_counts']['upcoming'],1)
 def test_open_unknown_title_is_held_without_detail(self):
  r=self.run_scan(table(title='AI 산업 연구개발 신규과제 모집',period='2020.01.01 ~ 2099.12.31'))
  self.assertEqual(self.calls,['https://e.org/list']);self.assertEqual(self.used[0]['collection_state'],'title_metadata_only')
  self.assertEqual(r['triage_counts']['title_unconfirmed'],1)
 def test_unknown_period_unrelated_title_still_does_not_fetch_detail(self):
  self.run_scan('<a href="/view/1">조선 AX 모델하우스 신규과제 모집</a>')
  self.assertEqual(len(self.calls),1);self.assertEqual(self.used[0]['collection_triage']['title']['state'],'excluded')
 def test_unknown_period_probes_body_only_then_stops(self):
  r=self.run_scan('<a href="/view/1">통신 채널 연구과제 모집</a>',body(p()['text'].replace(DATE,'')))
  self.assertEqual(self.calls,['https://e.org/list','https://e.org/view/1']);self.assertEqual(r['triage_counts']['deadline_unknown'],1)
  self.assertEqual(self.used[0]['attachments'],[])
 def test_body_confirms_open_then_downloads_rfp(self):
  r=self.run_scan('<a href="/view/1">통신 채널 연구과제 모집</a>',body())
  self.assertEqual(len(self.calls),3);self.assertIn('/download?',self.calls[-1]);self.assertEqual(r['triage_counts']['detailed'],1)
 def test_open_listing_can_supply_period_missing_from_body(self):
  r=self.run_scan(table(period='2020.01.01 ~ 2099.12.31'),body(p()['text'].replace(DATE,'')))
  self.assertEqual(len(self.calls),3);self.assertIn('목록에서 확인한 접수정보',self.used[0]['text'])
 def test_body_closed_overrides_open_listing_and_is_not_revisited(self):
  html=table(period='2020.01.01 ~ 2099.12.31')
  self.run_scan(html,body(p()['text'].replace('2099년','2020년')))
  self.assertEqual(len(self.calls),2);self.assertEqual(self.used,[])
  self.calls=[];self.run_scan(html)
  self.assertEqual(self.calls,['https://e.org/list'])
 def test_extension_in_listing_reopens_cached_closed_url(self):
  self.run_scan(table(period='2020.01.01 ~ 2020.01.31'))
  self.calls=[];self.run_scan(table(period='2020.01.01 ~ 2099.12.31'),body())
  self.assertEqual(len(self.calls),3)
 def test_verified_stored_expired_notice_is_not_fetched_again(self):
  known={'https://e.org/view/1':{'title':'통신 채널 연구과제 모집','period':{'state':'expired','end_date':'2020-01-31'}}}
  self.run_scan('<a href="/view/1">통신 채널 연구과제 모집</a>',known=known)
  self.assertEqual(self.calls,['https://e.org/list'])
 def test_publication_date_does_not_cause_closed_skip(self):
  self.run_scan(table(period='2020.01.01',headers=('공고명','게시일')),body())
  self.assertEqual(len(self.calls),3)
 def test_old_unconfirmed_title_does_not_block_changed_communications_title(self):
  self.run_scan(table(title='AI 산업 신규과제 모집',period='2020.01.01 ~ 2099.12.31'))
  self.calls=[];self.run_scan(table(period='2020.01.01 ~ 2099.12.31'),body())
  self.assertEqual(len(self.calls),3)

class RankingAndSchedulingTests(unittest.TestCase):
 def test_unknown_title_cannot_be_promoted_by_positive_body(self):
  with tempfile.TemporaryDirectory() as d:
   analysis=Service(d).analyze(p(title='산업 혁신 신규과제 모집'));self.assertEqual(analysis['mode'],'rule_screening');a=analysis['screening']
   self.assertEqual(a['decision'],'review');self.assertIn('제목',a['reason'])
 def test_title_candidate_alone_never_recommends_unrelated_body(self):
  with tempfile.TemporaryDirectory() as d:
   a=Service(d).analyze(p(text='\n'.join([CALL,'조선 현장 AI 모델하우스를 구축하고 제조 데이터를 분석한다.',QUAL,DATE])))['screening']
   self.assertNotEqual(a['decision'],'recommended')
 def test_metadata_holds_and_expired_documents_do_not_run_detailed_matcher(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d)
   with patch('aclbrief.matcher.Matcher.analyze',side_effect=AssertionError('expensive detail matcher')):
    for payload in (p(title='산업 혁신 신규과제 모집'),p(text=p()['text'].replace(DATE,''),collection_state='deadline_unknown_metadata_only'),p(text=p()['text'].replace('2099년','2020년'))):
     self.assertNotEqual(s.analyze(payload)['screening']['decision'],'recommended')
 def test_same_title_priority_comes_from_detailed_topic_links(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d);high=s.analyze(p())['screening'];low=s.analyze(p(text=p()['text'].replace(GOAL,'연구 목표: 이동통신의 빔 탐색 알고리즘을 개발하고 연구 성능을 평가한다.')))['screening']
   self.assertEqual(high['decision'],'recommended');self.assertEqual(low['decision'],'recommended');self.assertGreater(high['score'],low['score'])
 def test_sources_are_interleaved_before_archive_next_page(self):
  calls=[]
  class F:
   def fetch(self,url):
    calls.append(url)
    if '/list' in url:
     name='a' if '/a/' in url else 'b'
     content='<a href="/view/'+name+'-1">통신 연구과제 모집</a>'
     if name=='a' and '?page=' not in url:content+='<a href="?page=2">2</a>'
    else:content='<main>'+p()['text']+'</main>'
    return content.encode(),{},url
  with tempfile.TemporaryDirectory() as d:
   s=Service(d,fetcher=F())
   for src in s.store.sources():s.store.update_source(src['id'],{**src['config'],'enabled':src['id'] in ('iris','nipa'),**({'url':'https://e.org/'+('a' if src['id']=='iris' else 'b')+'/list','detail_pattern':'/view/'} if src['id'] in ('iris','nipa') else {})})
   s.collect()
  self.assertLess(calls.index('https://e.org/b/list'),calls.index('https://e.org/a/list?page=2'))
 def test_repeated_listing_is_detected_across_one_page_turns(self):
  class F:
   def fetch(self,url):
    content='<a href="/view/1">통신 연구과제 모집</a><a href="?page=2">2</a>' if '/list' in url else '<main>'+p()['text']+'</main>'
    return content.encode(),{},url
  with tempfile.TemporaryDirectory() as d:
   s=Service(d,fetcher=F())
   for src in s.store.sources():s.store.update_source(src['id'],{**src['config'],'enabled':src['id']=='kca',**({'url':'https://e.org/list'} if src['id']=='kca' else {})})
   r=s.collect()
   self.assertEqual(r['status'],'needs_review');self.assertIn('동일 게시물',r['sources'][0]['error'])

class DirectRequestTests(unittest.TestCase):
 def test_default_requests_target_directly_with_browser_user_agent(self):
  class Response(io.BytesIO):
   headers={}
   def geturl(self):return 'https://e.org/list'
  f=Fetcher(delay=0);f.opener=Mock();f.opener.open.side_effect=lambda req,timeout:Response(b'page')
  with patch('aclbrief.collect.public_url',side_effect=lambda u:u):
   f.fetch('https://e.org/list');file,h,u=f.download('https://e.org/RFP')
  file.close()
  reqs=[c.args[0] for c in f.opener.open.call_args_list]
  self.assertEqual([r.full_url for r in reqs],['https://e.org/list','https://e.org/RFP'])
  self.assertTrue(all(r.get_header('User-agent').startswith('Mozilla/5.0') for r in reqs));self.assertFalse(f.respect_robots)
 def test_explicit_user_agent_override_and_header_validation(self):
  with patch.dict('os.environ',{'ACL_USER_AGENT':'ResearchClient/1'}):self.assertEqual(Fetcher().user_agent,'ResearchClient/1')
  with patch.dict('os.environ',{'ACL_USER_AGENT':'bad\r\nInjected: value'}):
   with self.assertRaises(ValueError):Fetcher()
 def test_real_http_denial_is_not_reported_as_success_or_retried_forever(self):
  f=Fetcher(delay=0);f.opener=Mock();f.opener.open.side_effect=urllib.error.HTTPError('https://e.org/list',403,'Forbidden',{},None)
  with patch('aclbrief.collect.public_url',side_effect=lambda u:u):
   with self.assertRaises(urllib.error.HTTPError):f.fetch('https://e.org/list')
  self.assertEqual(f.opener.open.call_count,1)
