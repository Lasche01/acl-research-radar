import base64
import io
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from aclbrief.service import Service
from aclbrief.collect import extract_file,page_payload
from aclbrief.documents import deadline_evidence,required_attachment
from aclbrief.routes import publishers,route_status
from test_rules_queue import p,QUAL,DATE

def file(name,text):return {'name':name,'base64':base64.b64encode(text.encode()).decode()}
class CaptureTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_new_browser_capture_never_fetches_site(self):
  with patch.object(self.s.fetcher,'fetch',side_effect=AssertionError('network')):
   r=self.s.import_capture({'title':'통신 위탁연구 모집','url':'https://example.org/view/1','text':p()['text'],'scope':'rfp','files':[]})
  n=self.s.notice_detail(r['id']);self.assertEqual(n['screening']['decision'],'recommended');self.assertEqual(n['payload']['captures'][0]['source_url'],'https://example.org/view/1')
 def test_supplement_keeps_original_and_identifier(self):
  old=self.s.ingest(p(url='https://example.org/view/1',text=p()['text'].replace(QUAL,'')))
  r=self.s.import_capture({'notice_id':old['id'],'files':[file('공고문.txt',QUAL)]})
  self.assertEqual(r['id'],old['id']);self.assertEqual(r['version'],2);self.assertEqual(len(self.s.store.versions(r['id'])),2)
  self.assertIn(QUAL,self.s.notice_detail(r['id'])['payload']['text']);self.assertEqual(self.s.notice_detail(r['id'])['screening']['decision'],'recommended')
 def test_missing_core_file_remains_until_explicitly_replaced(self):
  old=self.s.ingest(p(url='https://example.org/view/1',attachments=[{'name':'rfp.pdf','status':'review','error':'failed'}]))
  r=self.s.import_capture({'notice_id':old['id'],'files':[file('new-rfp.txt',p()['text'])]})
  self.assertEqual(self.s.notice_detail(r['id'])['screening']['decision'],'review')
  r=self.s.import_capture({'notice_id':old['id'],'files':[file('new-rfp.txt',p()['text'])],'replace_missing':True})
  self.assertEqual(self.s.notice_detail(r['id'])['screening']['decision'],'recommended')
 def test_invalid_file_is_recorded_and_not_promoted(self):
  r=self.s.import_capture({'title':'통신 위탁연구 모집','text':p()['text'],'files':[file('공고문.hwp','<html>error</html>')],'scope':'rfp'})
  n=self.s.notice_detail(r['id']);self.assertEqual(n['screening']['decision'],'review');self.assertEqual(n['payload']['attachments'][0]['status'],'review')
 def test_automatic_refresh_preserves_human_supplement(self):
  oldp=p(url='https://example.org/view/1',text=p()['text'].replace(QUAL,''));old=self.s.ingest(oldp)
  self.s.import_capture({'notice_id':old['id'],'files':[file('공고문.txt',QUAL)]})
  self.s.ingest(oldp);n=self.s.notice_detail(old['id']);self.assertIn(QUAL,n['payload']['text']);self.assertEqual(n['screening']['decision'],'recommended')
  self.s.ingest({**oldp,'text':oldp['text']+'\n공식 내용 변경'});n=self.s.notice_detail(old['id']);self.assertIn(QUAL,n['payload']['text']);self.assertEqual(n['screening']['decision'],'review');self.assertIn('source_refresh',n['payload'])
 def test_light_list_omits_raw_text_detail_preserves_it(self):
  r=self.s.ingest(p());self.assertNotIn('text',self.s.state()['notices'][0]['payload']);self.assertEqual(self.s.notice_detail(r['id'])['payload']['text'],p()['text'])
 def test_unit_change_cannot_overwrite_another_rfp(self):
  r=self.s.ingest(p(unit='one'))
  with self.assertRaises(ValueError):self.s.import_capture({'notice_id':r['id'],'text':p()['text'],'unit':'two'})
class DocumentTests(unittest.TestCase):
 def test_optional_spreadsheet_vs_primary(self):
  self.assertFalse(required_attachment('붙임4. 참여인력현황.xlsx'));self.assertTrue(required_attachment('RFP_참여인력현황.hwpx'))
 def test_explicit_deadline_not_program_period(self):
  self.assertEqual(deadline_evidence('접수마감일\n2027/02/21\n사업기간 2027-03-01 ~ 2029-12-31')[0],'2027-02-21')
 def test_short_range_end_does_not_expire_from_opening_date(self):
  self.assertEqual(deadline_evidence('신청기간: 2026.10.15 ~ 11.16')[0],'2026-11-16')
 def test_expired_detail_skips_attachment_network(self):
  calls=[]
  class F:
   def fetch(self,url):
    calls.append(url);return ('<div class="view-cont">'+p()['text'].replace('2099년','2020년')+'</div><a href="/download?file=1">공고문.hwp</a>').encode(),{},url
  r=page_payload('https://example.org/view/1',F());self.assertEqual(len(calls),1);self.assertEqual(r['collection_state'],'expired_metadata_only')
 def test_optional_form_not_downloaded(self):
  calls=[]
  class F:
   def fetch(self,url):
    calls.append(url);return ('<div class="view-cont">'+p()['text']+'</div><a href="/download?file=1">참여인력현황양식.xlsx</a>').encode(),{},url
  r=page_payload('https://example.org/view/1',F());self.assertEqual(len(calls),1);self.assertEqual(r['attachments'][0]['status'],'optional_not_fetched')
 def test_saved_html_prefers_article(self):
  text,w=extract_file(('<header><img src="logo.png">메뉴</header><div class="view-cont">'+p()['text']+'</div><script>secret()</script>').encode(),'saved.html')
  self.assertNotIn('secret()',text);self.assertNotIn('메뉴',text);self.assertEqual(w,[])
 def test_download_html_is_not_a_pdf_or_hwp(self):
  with self.assertRaisesRegex(ValueError,'HTML'):extract_file(b'<html>404</html>','rfp.pdf')
 def test_bounded_zip_and_multiple_rfp_review_warning(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr('RFP-one.txt',p()['text']);z.writestr('RFP-two.txt',p()['text']);z.writestr('양식.xlsx','binary')
  text,w=extract_file(b.getvalue(),'documents.zip');self.assertIn(QUAL,text);self.assertTrue(any('복수 RFP' in x for x in w))
 def test_large_hwpx_images_are_not_loaded_as_xml(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
   z.writestr('Contents/section0.xml','<root><p>'+p()['text']+'</p></root>');z.writestr('BinData/image.png',b'0'*(25*1024*1024))
  text,w=extract_file(b.getvalue(),'document.hwpx');self.assertIn(QUAL,text)
class RouteTests(unittest.TestCase):
 def test_publisher_match_requires_named_evidence(self):
  self.assertEqual(publishers({'title':'[한국전자통신연구원] 위탁연구 공모'}),['etri'])
  self.assertEqual(publishers({'text':'전문기관\n한국연구재단\n공고번호'}),['nrf'])
  self.assertEqual(publishers({'text':'관련기관 목록\n한국연구재단\n정보통신기획평가원'}),[])
 def test_republication_does_not_change_original_blocked_status(self):
  sources=[{'id':'iitp','config':{'name':'IITP','url':'https://blocked','enabled':True},'status':'blocked','checked_at':'now'},
           {'id':'iris','config':{'name':'IRIS','url':'https://allowed','enabled':True},'status':'success','checked_at':'now'}]
  r=route_status(sources,[{'source_id':'iris','payload':{'text':'전문기관\n정보통신기획평가원'}}]);self.assertEqual(r[0]['status'],'blocked');self.assertEqual(r[0]['alternative_routes'][0]['publisher_matches'],1)
 def test_collect_alternatives_uses_provider_ids(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d)
   with patch.object(s,'collect',return_value={'ok':True}) as mocked:s.collect_alternatives('nrf');mocked.assert_called_once_with(['iris','koita'])
   with self.assertRaises(ValueError):s.collect_alternatives('d2b')
