"""Regression fixtures from observed official link shapes; no network needed."""
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import Mock,patch
from aclbrief.collect import PageParser, BodyParser, list_links, next_pages, attachment_address, collect_source, extract_file, Fetcher
from aclbrief.notices import document_check, has_record_id
from aclbrief.service import Service, DEMO
from aclbrief.diagnostics import classify_error


def tiny_hwp(text, compressed=True, encrypted=False, corrupt_chain=False):
    """Valid small CFB container with mini streams for HWP's header and body."""
    end,free = 0xfffffffe,0xffffffff
    value = text.encode('utf-16le')
    record = struct.pack('<I',67 | (len(value)<<20)) + value
    body = zlib.compress(record)[2:-4] if compressed else record
    fh = b'HWP Document File'.ljust(32,b'\0') + b'\x01\x00\x00\x05' + struct.pack('<I',int(compressed)|int(encrypted)*2)
    # Header=64 bytes, body begins at mini sector 1.
    stream = fh.ljust(64,b'\0') + body
    body_sectors=(len(body)+63)//64
    stream=stream.ljust(512,b'\0')
    header=bytearray(512);header[:8]=bytes.fromhex('d0cf11e0a1b11ae1')
    struct.pack_into('<HHHH',header,24,0x3e,3,0xfffe,9);struct.pack_into('<H',header,32,6)
    struct.pack_into('<I',header,44,1);struct.pack_into('<I',header,48,1)
    struct.pack_into('<I',header,56,4096);struct.pack_into('<II',header,60,3,1)
    struct.pack_into('<II',header,68,end,0);struct.pack_into('<109I',header,76,0,*([free]*108))
    fat=[0xfffffffd,end,end,end]+[free]*124
    directory=bytearray(512)
    def entry(idx,name,kind,left=free,right=free,child=free,start=end,size=0):
        e=bytearray(128);n=(name+'\0').encode('utf-16le');e[:len(n)]=n;struct.pack_into('<H',e,64,len(n));e[66]=kind
        struct.pack_into('<III',e,68,left,right,child);struct.pack_into('<I',e,116,start);struct.pack_into('<Q',e,120,size)
        directory[idx*128:(idx+1)*128]=e
    entry(0,'Root Entry',5,child=1,start=2,size=len(stream))
    entry(1,'FileHeader',2,right=2,start=0,size=len(fh))
    entry(2,'BodyText',1,child=3)
    entry(3,'Section0',2,start=1,size=len(body))
    mf=[end]+[i+1 if i<body_sectors else end for i in range(1,body_sectors+1)]
    mf=mf+[free]*(128-len(mf))
    if corrupt_chain:mf[1]=1
    return bytes(header)+struct.pack('<128I',*fat)+directory+stream+struct.pack('<128I',*mf)


class CollectionRegression(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.svc=Service(self.tmp.name)
        self.configs={s['id']:s['config'] for s in self.svc.store.sources()}
    def tearDown(self):self.tmp.cleanup()
    def parse(self,html):
        p=PageParser();p.feed(html);return p.result()
    def test_screenshot_menu_titles_excluded_even_with_research_words(self):
        for title in ['인사/채용','회의/세미나','전체선택됨','DTiMS 소개','사업설명회']:
            p={'title':title, 'text':DEMO[0]['text'],'scope':'notice'}
            self.assertTrue(document_check(p)['excluded'])
            self.assertEqual(self.svc.matcher().analyze(p)['mappings'],[])
    def test_real_titles_without_word_notice_are_preserved(self):
        for title in ['지능형 전자기 스펙트럼 관리기술 개발','수중 채널 추정 연구기관 모집','2027년 연구개발 사업설명회 개최 안내']:
            self.assertFalse(document_check({'title':title})['excluded'])
    def test_menu_ids_are_not_post_ids(self):
        for url in ['https://x.gov/selectDocList.do?menuSeq=3031&bbsSeq=443','https://x.gov/ViewIntro.do']:
            self.assertFalse(has_record_id(url))
        self.assertTrue(has_record_id('https://x.gov/selectDoc.do?docSeq=59420&menuSeq=3031'))
    def test_five_official_click_and_path_formats(self):
        fixtures={
          'iris':('<a href="" onclick="f_bsnsAncmBtinSituListForm_view(\'024717\',\'ancmIng\'); return false;">AX 실증 신규과제 통합 재공고</a>','ancmId=024717'),
          'koita':('<a href="javascript: page_move( \'commBoardGovRnDView\', { no: 67151 } )">공공기술 발굴·매칭 수요조사 공고</a>','no=67151'),
          'nipa':('<a href="/home/2-2/16982">운영기관 모집공고</a>','/home/2-2/16982'),
          'dapa':('<a href="selectDocList.do?menuSeq=1">전체선택됨</a><a href="#none" onclick="fn_selectDoc(\'59420\')">방산 연구개발기관 모집 공고</a>','docSeq=59420'),
          'dtims':('<table><tr onclick="fncCtPrjNotiInfo(\'CTRND20250122102528704\');"><td>24-017</td><td class="bbs_title" title="전자기 스펙트럼 관리기술 개발">전자기 스펙트럼 관리기술 개발</td><td>2025/02/22</td></tr></table>','ctRndIden=CTRND20250122102528704')}
        for sid,(html,expected) in fixtures.items():
            links,_=list_links(self.parse(html),self.configs[sid]['url'],self.configs[sid])
            self.assertEqual(len(links),1,sid);self.assertIn(expected,links[0][0])
            if sid=='dapa':self.assertIn('bbsSeq=443',links[0][0])
            if sid=='dtims':self.assertEqual(links[0][1],'전자기 스펙트럼 관리기술 개발')
    def test_unrecognized_js_is_not_executed_or_guessed(self):
        links,_=list_links(self.parse('<a href="javascript:unknownView(123)">새로운 연구과제 공고</a>'),self.configs['iris']['url'],self.configs['iris'])
        self.assertEqual(links,[])
    def test_three_pages_and_more_than_four_records_are_collected(self):
        root='https://example.org/list'
        pages={root:''.join(f'<a href="/view/{i}">통신 연구 공고 {i}</a>' for i in range(1,7))+'<a href="?page=2">2</a>',
               root+'?page=2':'<a href="/view/7">통신 연구 공고 7</a><a href="?page=3">3</a>',
               root+'?page=3':'<a href="/view/8">통신 연구 공고 8</a><a href="?page=4">4</a>'}
        for i in range(1,9):pages[f'https://example.org/view/{i}']='<p>'+DEMO[0]['text']+'</p>'
        class F:
            def fetch(self,url):return pages[url].encode(),{},url
        r=collect_source({'id':'test','url':root},F(),self.svc.ingest)
        self.assertEqual((r['pages_read'],r['processed']),(3,8));self.assertEqual(r['status'],'partial')
        self.assertIn('이후 페이지는 미확인',r['error'])
    def test_ignored_get_pagination_is_not_claimed_complete(self):
        class F:
            def fetch(self,url):
                html='<a href="/view/1">연구기관 모집 공고</a><a href="?page=2">2</a>' if '/list' in url else '<p>'+DEMO[0]['text']+'</p>'
                return html.encode(),{},url
        r=collect_source({'id':'test','url':'https://example.org/list'},F(),self.svc.ingest)
        self.assertEqual(r['processed'],1);self.assertEqual(r['status'],'partial')
    def test_only_menus_is_review_not_success(self):
        class F:
            def fetch(self,url):return '<a href="/view?menuSeq=1">전체선택됨</a><a href="/ViewIntro.do">DTiMS 소개</a>'.encode(),{},url
        r=collect_source(self.configs['dtims'],F(),self.svc.ingest)
        self.assertEqual(r['status'],'review');self.assertEqual(r['processed'],0)
    def test_article_body_omits_menu_research_terms(self):
        p=BodyParser();p.feed('<div>OTFS 채널 추정 메뉴</div><div class="view-cont"><p>'+'소자 제작 요구사항입니다. '*12+'</p></div>')
        self.assertNotIn('OTFS',p.result());self.assertIn('소자 제작',p.result())
    def test_click_attachments_have_real_http_addresses(self):
        for sid,action,expected in [('iris',"javascript:f_bsnsAncm_downloadAtchFile('A==','B+==','공고.hwp','123')",'atchDocId=A%3D%3D'),('dtims',"javascript:download('CTRND123','4')",'attcOrdr=4')]:
            u=attachment_address({'href':action},self.configs[sid]['url'],sid)
            self.assertTrue(u.startswith('https://'));self.assertIn(expected,u)
    def test_legacy_database_items_hidden_without_deleting_history(self):
        p={**DEMO[0],'title':'전체선택됨','url':'https://example.org/selectDocList.do?menuSeq=1'}
        # Simulate the old collector's raw database entry and misleading graph result.
        r=self.svc.store.upsert(p,self.svc.matcher().analyze(DEMO[0]))
        self.svc.store.feedback(r['id'],1,'irrelevant','공고 아님')
        upgraded=Service(self.tmp.name)
        state=upgraded.state()
        self.assertEqual(state['notices'],[]);self.assertEqual(len(state['excluded']),1)
        self.assertEqual(len(upgraded.store.versions(r['id'])),1)
        self.assertIsNotNone(upgraded.store.notices()[0]['feedback'])
        self.assertNotIn('## 전체선택됨',upgraded.briefing())
        self.assertEqual(len(upgraded.store.export()['notices']),1)
    def test_category_then_fit_order_and_rejected_last(self):
        data=[('review-rich','review',35,4,None),('direct','direct',75,1,None),('low','low',5,0,None),('candidate','candidate',49,2,None),('expand','expand',50,2,None),('review-weak','review',35,1,None),('rejected','direct',90,3,'ineligible')]
        for title,cat,score,count,elig in data:
            p={**DEMO[0],'title':title,'url':'https://example.org/view/'+title,'eligibility':elig or 'unknown'}
            a=self.svc.matcher().analyze(DEMO[0]);a.update(category=cat,priority=score,mappings=[{'topic_id':str(i),'topic':str(i)} for i in range(count)])
            self.svc.store.upsert(p,a)
        self.assertEqual([n['title'] for n in self.svc.state()['notices']],['direct','candidate','expand','review-rich','review-weak','low','rejected'])
    def test_connection_reset_during_body_read_is_retried(self):
        f=Fetcher(attempts=2);first=Mock();second=Mock()
        for obj in [first,second]:obj.__enter__=Mock(return_value=obj);obj.__exit__=Mock(return_value=False);obj.headers={}
        first.read.side_effect=ConnectionResetError(10054,'강제 종료');second.read.side_effect=[b'ok',b''];second.geturl.return_value='https://example.org/'
        f.opener=Mock();f.opener.open.side_effect=[first,second]
        with patch('aclbrief.collect.time.sleep'):
            result=f._open_with_retry(__import__('urllib.request',fromlist=['Request']).Request('https://example.org/'),read_body=True)
        self.assertEqual(result[0],b'ok');self.assertEqual(f.opener.open.call_count,2)
        self.assertEqual(classify_error(OSError('[WinError 10054] 현재 연결 강제 종료'))['code'],'connection_reset')
    def test_hwp_compressed_and_uncompressed_paragraphs(self):
        for compressed in [True,False]:
            text,warnings=extract_file(tiny_hwp('채널 추정 및 빔 탐색 연구개발기관 모집 공고문입니다.',compressed),'공고.hwp')
            self.assertIn('빔 탐색',text);self.assertTrue(warnings)
    def test_hwp_encrypted_and_corrupt_documents_fail_visibly(self):
        for kwargs in [{'encrypted':True},{'corrupt_chain':True}]:
            with self.assertRaises(ValueError):extract_file(tiny_hwp('연구개발기관 모집 공고문입니다.',**kwargs),'공고.hwp')
        with self.assertRaises(ValueError):extract_file(b'\xd0\xcf\x11\xe0','bad.hwp')
    def test_add_and_kca_existing_link_formats_still_work(self):
        for sid,href in [('add','/kps/publicNtis/ntisView?titleId=1764&menuId=MENU02201&titleId=0'),('kca','/boardView.do?boardId=NOTICE&seq=1127149&movePage=1')]:
            links,_=list_links(self.parse('<a href="'+href+'">연구개발기관 모집 공고</a>'),self.configs[sid]['url'],self.configs[sid])
            self.assertEqual(len(links),1,sid)
    def test_old_menu_only_success_is_invalidated(self):
        self.svc.store.source_result('dtims','success',1,'',{'code':'ok','notes':['DTiMS 소개: 본문·첨부 검토 필요'],'processed':1})
        before=next(s for s in self.svc.store.sources() if s['id']=='dtims')['checked_at']
        upgraded=Service(self.tmp.name)
        row=next(s for s in upgraded.store.sources() if s['id']=='dtims')
        self.assertEqual(row['status'],'review');self.assertIsNone(row['last_success']);self.assertEqual(row['checked_at'],before)
    def test_source_upgrade_keeps_selection_and_custom_url(self):
        old=self.configs['dratri'];self.svc.store.update_source('dratri',{**old,'url':'https://www.add.re.kr/dratri','enabled':True})
        self.svc.store.update_source('iris',{**self.configs['iris'],'url':'https://example.org/custom','enabled':False})
        upgraded=Service(self.tmp.name);sources={s['id']:s['config'] for s in upgraded.store.sources()}
        self.assertEqual(sources['dratri']['url'],'https://www.add.re.kr/menu?menuId=MENU03062');self.assertTrue(sources['dratri']['enabled'])
        self.assertEqual(sources['iris']['url'],'https://example.org/custom');self.assertFalse(sources['iris']['enabled'])

if __name__=='__main__':unittest.main()
