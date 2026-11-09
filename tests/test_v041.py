"""Regression tests for large real-world calls and continuous resumable scans."""
import io
import json
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
try:
 from pypdf import PdfWriter
except ImportError:PdfWriter=None
from aclbrief.collect import Fetcher, extract_file, page_payload, check_expansion
from aclbrief.server import AppServer
from aclbrief.service import Service
from aclbrief.uploads import UploadStore
from test_rules_queue import p

class LargeDocuments(unittest.TestCase):
 def test_hwpx_larger_than_32mb_reads_body_without_loading_images(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'RFP.hwpx'
   with zipfile.ZipFile(path,'w',zipfile.ZIP_STORED) as z:
    z.writestr('Contents/section0.xml','<root><p>'+p()['text']+'</p></root>')
    with z.open('BinData/image.bin','w') as image:
     for _ in range(34):image.write(b'x'*(1024*1024))
   self.assertGreater(path.stat().st_size,32*1024*1024)
   text,w=extract_file(path,path.name);self.assertIn('CSI',text)
 @unittest.skipIf(PdfWriter is None,'PDF 선택 의존성 미설치')
 def test_pdf_all_151_pages_are_examined(self):
  b=io.BytesIO();writer=PdfWriter()
  for _ in range(151):writer.add_blank_page(width=100,height=100)
  writer.write(b)
  text,w=extract_file(b.getvalue(),'long.pdf');self.assertIn('페이지 151',text);self.assertEqual(len(w),151)
 def test_zip_all_101_documents_are_examined(self):
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:
   for i in range(101):z.writestr('공고문-'+str(i)+'.txt','통신 연구내용 문서 '+str(i))
  text,w=extract_file(b.getvalue(),'자료.zip');self.assertIn('문서 100',text)
 def test_network_large_document_is_spooled_and_complete(self):
  class Response(io.BytesIO):
   headers={}
   def geturl(self):return 'https://example.org/large'
  f=Fetcher();f.opener=Mock();f.opener.open.return_value=Response(b'a'*(33*1024*1024))
  stream,h,u=f._open_with_retry(urllib.request.Request('https://example.org/large'),read_body=True,to_file=True)
  try:
   self.assertTrue(stream._rolled);stream.seek(0,2);self.assertEqual(stream.tell(),33*1024*1024)
  finally:stream.close()
 def test_incomplete_download_is_not_reported_as_complete(self):
  class Response(io.BytesIO):
   headers={'Content-Length':'100'}
   def geturl(self):return 'https://example.org/truncated'
  f=Fetcher(attempts=1);f.opener=Mock();f.opener.open.return_value=Response(b'partial')
  with self.assertRaisesRegex(ValueError,'전송이 완료되지'):
   f._open_with_retry(urllib.request.Request('https://example.org/truncated'),read_body=True,to_file=True)
 def test_more_than_six_required_attachments_are_fetched(self):
  calls=[]
  class F:
   def fetch(self,url):
    calls.append(url)
    if '/download' in url:return '통신 연구 첨부 본문'.encode(),{},url
    return ('<main>'+p()['text']+'</main>'+''.join('<a href="/download?id='+str(i)+'">공고문'+str(i)+'.txt</a>' for i in range(8))).encode(),{},url
  n=page_payload('https://example.org/view/1',F());self.assertEqual(len(n['attachments']),8);self.assertEqual(len(calls),9)
 def test_body_beyond_250k_and_180_paragraphs_is_not_discarded(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d);payload=p(text=('일반 안내 문장입니다.\n'*22000)+p()['text'])
   n=s.ingest(payload);detail=s.notice_detail(n['id'])
   self.assertGreater(len(detail['payload']['text']),250000)
   self.assertEqual(detail['screening']['decision'],'recommended');self.assertTrue(detail['analysis']['mappings'])
 def test_ingest_does_not_load_every_saved_body(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d)
   with patch.object(s.store,'notices',side_effect=AssertionError('full corpus scan')):
    a=s.ingest(p(url='https://example.org/view/1'));b=s.ingest(p(url='https://example.org/view/1'))
   self.assertEqual(a['id'],b['id']);self.assertEqual(b['change'],'unchanged')
 def test_zip_expansion_protection_is_preserved(self):
  with self.assertRaisesRegex(ValueError,'압축 해제 비율'):check_expansion(100*1024*1024,100)

class ContinuousScan(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();outer=self
  self.detail_calls=[];self.cancel_after=None
  class F:
   def fetch(self,url):
    if '/list' in url:
     page=int(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('page',['1'])[0])
     content=''.join(f'<a href="/view/{(page-1)*11+i}">통신 연구 모집 공고 {(page-1)*11+i}</a>' for i in range(1,12))
     if page<7:content+=f'<a href="?page={page+1}">{page+1}</a>'
    else:
     outer.detail_calls.append(url)
     if len(outer.detail_calls)==outer.cancel_after:outer.s.cancel_collection()
     content='<main>'+p()['text']+'</main>'
    return content.encode(),{},url
  self.s=Service(self.tmp.name,fetcher=F())
  for src in self.s.store.sources():self.s.store.update_source(src['id'],{**src['config'],'enabled':src['id']=='kca',**({'url':'https://example.org/list','detail_pattern':'/view/'} if src['id']=='kca' else {})})
 def tearDown(self):self.tmp.cleanup()
 def test_one_run_drains_beyond_five_pages_and_thirty_details(self):
  r=self.s.collect(['kca']);self.assertEqual(r['status'],'completed',r)
  src=r['sources'][0];self.assertEqual(src['pages_read'],7);self.assertEqual(src['processed'],77);self.assertEqual(src['pending_details'],0)
  self.assertEqual(len(set(self.detail_calls)),77)
 def test_stop_keeps_queue_and_next_run_resumes(self):
  self.cancel_after=3;r=self.s.collect(['kca']);self.assertEqual(r['status'],'stopped');self.assertGreater(r['sources'][0]['pending_details'],0)
  self.assertEqual(r['sources'][0]['diagnostic']['code'],'stopped');self.assertFalse(self.s.cancel_event.is_set())
  self.cancel_after=None;r=self.s.collect(['kca']);self.assertEqual(r['status'],'completed',r)
  self.assertEqual(len(self.s.store.notices()),77);self.assertEqual(len(self.detail_calls),77)
 def test_failed_detail_attempted_once_per_scan_not_forever(self):
  original=self.s.fetcher.fetch
  def fetch(url):
   if url.endswith('/view/2'):
    self.detail_calls.append(url);raise OSError('fixture offline')
   return original(url)
  self.s.fetcher.fetch=fetch
  r=self.s.collect(['kca']);self.assertEqual(r['status'],'needs_review');self.assertEqual(r['sources'][0]['pending_details'],1)
  self.assertEqual(self.detail_calls.count('https://example.org/view/2'),1);self.assertEqual(len(self.detail_calls),77)

class UploadTests(unittest.TestCase):
 def test_truncated_transfer_and_unknown_token_are_rejected(self):
  store=UploadStore()
  try:
   with self.assertRaisesRegex(ValueError,'중단'):store.add(io.BytesIO(b'a'),2,'rfp.txt')
   self.assertEqual(list(Path(store.directory.name).iterdir()),[])
   with self.assertRaises(ValueError):
    with store.claim([{'upload_id':'/etc/passwd'}]):pass
  finally:store.close()
 def test_http_binary_upload_supplements_and_releases_staged_file(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d);server=AppServer(0,s);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   base=f'http://127.0.0.1:{server.server_port}'
   def post(path,body,ctype='application/json',headers=None):
    return urllib.request.urlopen(urllib.request.Request(base+path,data=body,headers={'Content-Type':ctype,**(headers or {})}))
   try:
    with post('/api/upload?name=RFP.txt',p()['text'].encode(),'application/octet-stream') as r:uploaded=json.load(r)
    self.assertEqual(len(server.uploads.items),1)
    with post('/api/import-capture',json.dumps({'title':p()['title'],'scope':'rfp','files':[{'upload_id':uploaded['upload_id']}]}).encode()) as r:created=json.load(r)
    self.assertEqual(s.notice_detail(created['id'])['screening']['decision'],'recommended')
    self.assertEqual(server.uploads.items,{});self.assertEqual(list(Path(server.uploads.directory.name).iterdir()),[])
    with self.assertRaises(urllib.error.HTTPError) as error:post('/api/upload?name=x',b'x','application/octet-stream',{'Origin':'https://attacker.example'})
    self.assertEqual(error.exception.code,403)
   finally:server.shutdown();server.server_close();thread.join()
 def test_upload_file_count_is_not_twelve(self):
  store=UploadStore()
  try:
   files=[store.add(io.BytesIO(b'valid document text'),19,'공고문.txt') for _ in range(13)]
   with store.claim(files) as local:self.assertEqual(len(local),13)
   self.assertEqual(list(Path(store.directory.name).iterdir()),[])
  finally:store.close()
