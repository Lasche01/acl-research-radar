import copy
import os
import tempfile
import unittest
from unittest.mock import patch
from aclbrief.service import Service
from aclbrief.incremental import collect_incremental
GOAL='연구 목표: 다중 안테나 이동통신의 빔 탐색 및 CSI 피드백 알고리즘을 연구하고 성능 평가 시뮬레이터를 개발한다.'
QUAL='신청 자격: 국내 대학 및 산학협력단은 본 위탁연구의 수행기관으로 직접 신청할 수 있습니다.'
CALL='본 사업은 이동통신 연구개발 신규과제의 수행기관을 공개 모집합니다.'
DATE='본 과제의 신청 접수 마감일은 2099년 12월 31일입니다.'
def p(**kw):return {'title':'이동통신 빔 탐색 위탁연구 모집','scope':'rfp','text':'\n'.join([CALL,GOAL,QUAL,DATE]),**kw}
class RuleTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def result(self,payload):return self.s.analyze(payload)['screening']
 def test_positive_without_any_ai_network(self):
  with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('AI must not run')),patch.dict(os.environ,{'ACL_AI_PROVIDER':'openai','OPENAI_API_KEY':'synthetic'}):
   s=Service(self.tmp.name);a=s.analyze(p());self.assertEqual(a['screening']['decision'],'recommended');self.assertEqual(a['mode'],'rule_screening')
 def test_missing_each_proof(self):
  for part in [CALL,GOAL,QUAL,DATE]:
   with self.subTest(part=part):self.assertNotEqual(self.result(p(text=p()['text'].replace(part,'')))['decision'],'recommended')
 def test_real_title_hard_negatives_with_synthetic_bodies(self):
  self.assertEqual(self.result(p(title='2025년도 공공기관 안전관리등급 심사 결과'))['decision'],'excluded')
  r=self.result(p(title='2026년 조선 AX 특화 AI 모델하우스 구축·실증사업 신규과제 모집공고',text='\n'.join(['본 조선 AX 신규과제 모집공고는 산업 AI 모델하우스 구축 사업입니다.','조선 현장 데이터를 수집하고 AI 분석 모델을 개발한다.',QUAL,DATE])))
  self.assertEqual(r['decision'],'excluded')
 def test_background_negation_and_qualification_restrictions(self):
  for goal in ['통신망을 활용하여 제조 데이터를 수집하고 AI 모델을 개발한다.','빔 탐색 연구는 본 사업에서 제외하며 제조 모델하우스를 개발한다.']:
   self.assertNotEqual(self.result(p(text=p()['text'].replace(GOAL,goal)))['decision'],'recommended')
  for q in ['대학은 향후 재위탁 연구에 참여할 수 있습니다.','지원 대상: 중소기업만 신청 가능합니다.','참여 자격: 성균관대학교만 지원 가능합니다.','신청 자격은 관련 법률 제6조제1항에 따릅니다.','신청 자격: 대학과 기업의 컨소시엄 참여가 필수입니다.']:
   self.assertNotEqual(self.result(p(text=p()['text'].replace(QUAL,q)))['decision'],'recommended',q)
 def test_mixed_unread_expired_disabled(self):
  for payload in [p(text=p()['text']+'\n세부 과제별 신청 자격과 마감은 상이합니다.'),p(attachments=[{'status':'review'}]),p(text=p()['text'].replace('2099년','2020년'))]:self.assertNotEqual(self.result(payload)['decision'],'recommended')
  profile=self.s.store.get_setting('profile')
  for n in profile['nodes']:
   if n['type']=='research_topic':n['enabled']=False
  self.s.save_profile(profile);self.assertEqual(self.result(p())['decision'],'review')
 def test_briefing_history(self):
  r=self.s.ingest(p(url='https://example.org/view?id=1'));self.s.reanalyze();self.assertIn('## '+p()['title'],self.s.briefing());self.assertEqual(len(self.s.store.versions(r['id'])),1)
class QueueTests(unittest.TestCase):
 def setUp(self):self.saved={};self.urls=[];self.used=[]
 def save(self,c):self.saved=copy.deepcopy(c)
 def runqueue(self,pages,limit=2,max_pages=2):
  outer=self
  class F:
   def fetch(self,url):
    outer.urls.append(url)
    if isinstance(pages[url],Exception):raise pages[url]
    return pages[url].encode(),{},url
  return collect_incremental({'id':'test','url':'https://example.org/list'},F(),lambda p: self.used.append(p['url']) or {'saved':True},self.saved,self.save,limit=limit,max_pages=max_pages)
 def pages(self):
  root='https://example.org';d={}
  for i in range(1,7):
   d[root+'/list'+('' if i==1 else '?page='+str(i))]=f'<a href="/view/{i}">통신 연구기관 모집 공고 {i}</a>'+ (f'<a href="?page={i+1}">{i+1}</a>' if i<6 else '')
   d[root+f'/view/{i}']='<div class="view-cont">'+p()['text']+'</div>'
  return d
 def test_independent_limits_and_resume(self):
  pages=self.pages();r=self.runqueue(pages,limit=1,max_pages=3);self.assertEqual((r['pages_read'],r['processed'],r['pending_details']),(3,1,2));self.assertGreater(r['pending_pages'],0)
  for i in range(6):self.runqueue(pages,limit=2,max_pages=3)
  self.assertEqual(len(self.used),6);self.assertEqual(len(set(self.used)),6);self.assertIn('https://example.org/list?page=6',self.urls)
 def test_failed_detail_rotates_and_retries(self):
  pages=self.pages();pages['https://example.org/view/1']=OSError('failure');self.runqueue(pages,limit=1);self.assertIn('https://example.org/view/1',dict(self.saved['pending']))
  self.runqueue(pages,limit=1);self.assertIn('https://example.org/view/2',self.used)
  pages['https://example.org/view/1']='<div>'+p()['text']+'</div>'
  for i in range(5):self.runqueue(pages)
  self.assertIn('https://example.org/view/1',self.used)
 def test_config_change_ignored_pages_and_cache_expiry(self):
  self.saved={'identity':'old','pending':[['https://unrelated.invalid/view/1','x']]};pages=self.pages();self.runqueue(pages);self.assertNotIn('https://unrelated.invalid/view/1',self.urls)
  self.saved['done']={u:0 for u in self.saved['done']};self.runqueue(pages,limit=10);self.assertGreater(self.used.count('https://example.org/view/1'),1)
  self.saved={};pages['https://example.org/list?page=2']=pages['https://example.org/list'];self.assertEqual(self.runqueue(pages)['status'],'partial')
