import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from aclbrief.robots import RobotsPolicy
from aclbrief.collect import page_payload
from aclbrief.service import Service
from aclbrief.diagnostics import classify_error
from aclbrief.incremental import collect_incremental
from test_rules_queue import p, QUAL
class PolicyTests(unittest.TestCase):
 def test_longest_allow_and_allow_tie(self):
  r=RobotsPolicy('User-agent: *\nDisallow: /\nAllow: /public/\nDisallow: /public/private\nAllow: /same\nDisallow: /same')
  for path,result in [('/public/x',True),('/private',False),('/public/private',False),('/same',True)]:self.assertEqual(r.can_fetch('ACLBriefing','https://e.org'+path),result)
 def test_nrf_exact_root_and_wildcard_end(self):
  r=RobotsPolicy('User-agent: *\nDisallow: /\nAllow: /$');self.assertTrue(r.can_fetch('ACLBriefing','https://e.org/'));self.assertFalse(r.can_fetch('ACLBriefing','https://e.org/page/362'))
  r=RobotsPolicy('User-agent: *\nDisallow: /*.pdf$');self.assertFalse(r.can_fetch('ACLBriefing','https://e.org/a.pdf'));self.assertTrue(r.can_fetch('ACLBriefing','https://e.org/a.pdf?x=1'))
 def test_merge_agent_groups_and_encoding(self):
  r=RobotsPolicy('User-agent: ACLBriefing\nDisallow: /a\nUser-agent: ACLBriefing\nDisallow: /b\nUser-agent: *\nDisallow: /')
  self.assertFalse(r.can_fetch('ACLBriefing','https://e.org/b'));self.assertTrue(r.can_fetch('ACLBriefing','https://e.org/c'))
  r=RobotsPolicy('User-agent: *\nDisallow: /가\nDisallow: /baz\nDisallow: /a%2Fb')
  for path in ['/%EA%B0%80','/%62%61%7A','/a%2fb']:self.assertFalse(r.can_fetch('ACLBriefing','https://e.org'+path))
  self.assertTrue(r.can_fetch('ACLBriefing','https://e.org/a/b'))
 def test_bad_robots_html_not_allow_all(self):
  with self.assertRaises(ValueError):RobotsPolicy('<html><body>error</body></html>')
 def test_missing_links_classified_as_adapter(self):self.assertEqual(classify_error('상세 링크를 확인하지 못했습니다.')['code'],'detail_links_missing')
class ExtractionTests(unittest.TestCase):
 def fetch(self,html):
  class F:
   def fetch(self,url):return html.encode(),{},url
  return page_payload('https://e.org/view/1',F(),title=p()['title'])
 def test_global_logo_does_not_hold_valid_body(self):
  result=self.fetch('<header><img src="logo.png"></header><div class="view-cont">'+p()['text']+'</div><footer><img src="icon.png"></footer>')
  self.assertFalse(any('자동 판독하지' in x for x in result['warnings']))
 def test_content_image_still_requires_review(self):
  result=self.fetch('<div class="view-cont">'+p()['text']+'<img src="rfp.png"></div>')
  self.assertTrue(any('자동 판독하지' in x for x in result['warnings']))
 def test_field_heading_value_preserves_exact_quote(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d);result=s.analyze(p(text=p()['text'].replace(QUAL,'신청 자격\n국내 대학 및 산학협력단과 정부출연연구기관')))['screening']
   self.assertEqual(result['decision'],'recommended');self.assertIn('\n',result['gates']['eligibility']['quote'])
 def test_optional_form_does_not_hide_verified_body_but_rfp_does(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d)
   for required,decision in [(False,'recommended'),(True,'review')]:
    r=s.analyze(p(attachments=[{'name':'첨부','status':'review','required_for_screening':required}]))['screening'];self.assertEqual(r['decision'],decision)
 def test_diagnostic_contains_counts_but_no_text(self):
  with tempfile.TemporaryDirectory() as d:
   s=Service(d);s.ingest(p());r=s.diagnostics();self.assertEqual(r['screening_summary']['counts']['recommended'],1);self.assertNotIn(p()['text'],json.dumps(r,ensure_ascii=False))
 def test_failed_attachment_persists_and_reports_actual_error(self):
  saved={};used=[]
  class F:
   def fetch(self,url):return '<a href="/view/1">통신 연구 모집 공고</a>'.encode(),{},url
  payload=p(attachments=[{'name':'RFP.zip','url':'https://e.org/a.zip','status':'review','error':'unsupported ZIP'}])
  with patch('aclbrief.incremental.page_payload',return_value=payload):
   r=collect_incremental({'id':'test','url':'https://e.org/list'},F(),lambda p:used.append(p),None,lambda c:saved.update(c))
  self.assertEqual(r['pending_details'],1);self.assertEqual(r['attachment_errors'][0]['error'],'unsupported ZIP')
