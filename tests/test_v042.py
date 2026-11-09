"""Automatic official republications use the normal collection path without extra clicks."""
import tempfile
import unittest
from collections import Counter
from unittest.mock import patch
from aclbrief.collect import FetchError
from aclbrief.routes import collection_plan
from aclbrief.service import Service

class AutomaticRepublication(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=Service(self.tmp.name);self.calls=[]
 def tearDown(self):self.tmp.cleanup()
 def selected(self,ids):
  for src in self.s.store.sources():self.s.store.update_source(src['id'],{**src['config'],'enabled':src['id'] in ids})
 def batch(self,config,*args,**kw):
  self.calls.append(config['id'])
  return {'status':'success','discovered':0,'processed':0,'pages_read':1,'skipped_navigation':0,'changes':[],
          'pending_details':0,'pending_pages':0,'remaining_unattempted':0,'notes':[],'attachment_errors':[],'error':''}
 def test_normal_collect_includes_unchecked_providers_and_retains_blocked_original(self):
  self.selected({'iitp'})
  before={s['id']:s['config'] for s in self.s.store.sources()}
  def collect(config,*args,**kw):
   if config['id']=='iitp':
    self.calls.append('iitp');raise FetchError('robots.txt가 이 경로의 자동 수집을 허용하지 않습니다.','robots_disallowed','robots')
   return self.batch(config,*args,**kw)
  with patch('aclbrief.service.collect_incremental',side_effect=collect):r=self.s.collect()
  self.assertEqual(set(self.calls),{'iitp','iris','koita'})
  self.assertLess(self.calls.index('iris'),self.calls.index('iitp'));self.assertLess(self.calls.index('koita'),self.calls.index('iitp'))
  self.assertEqual(r['automatic_sources'],{'iris':['iitp'],'koita':['iitp']})
  states={s['id']:s for s in self.s.store.sources()}
  self.assertEqual(states['iitp']['status'],'blocked');self.assertEqual(states['iris']['status'],'success')
  self.assertEqual({s['id']:s['config'] for s in self.s.store.sources()},before)
 def test_individual_collect_also_includes_disabled_provider(self):
  self.selected(set())
  with patch('aclbrief.service.collect_incremental',side_effect=self.batch):r=self.s.collect(['etri'])
  self.assertEqual(self.calls,['koita','etri'])
  self.assertEqual(r['sources'][0]['diagnostic']['automatic_for'],['etri'])
  self.assertFalse(any(s['config']['enabled'] for s in self.s.store.sources()))
 def test_shared_providers_are_executed_once(self):
  self.selected({'iitp','nrf','ntis','etri','iris','koita'})
  with patch('aclbrief.service.collect_incremental',side_effect=self.batch):self.s.collect()
  self.assertEqual(set(self.calls),{'iitp','nrf','ntis','etri','iris','koita'})
  self.assertEqual(Counter(self.calls)['iris'],1);self.assertEqual(Counter(self.calls)['koita'],1)
 def test_explicit_scope_does_not_expand_unselected_institutions(self):
  self.selected({'iitp','nrf'})
  with patch('aclbrief.service.collect_incremental',side_effect=self.batch):self.s.collect(['kca'])
  self.assertEqual(self.calls,['kca'])
 def test_nothing_selected_makes_no_requests(self):
  self.selected(set())
  with patch('aclbrief.service.collect_incremental',side_effect=self.batch):self.s.collect()
  self.assertEqual(self.calls,[])
 def test_dashboard_distinguishes_direct_and_auto_selection(self):
  self.selected({'iitp'})
  state=self.s.state();sources={s['id']:s for s in state['sources']}
  self.assertEqual(state['source_summary']['enabled'],1)
  self.assertEqual(state['collection_summary']['enabled'],3);self.assertEqual(state['collection_summary']['automatic_added'],2)
  self.assertFalse(sources['koita']['config']['enabled']);self.assertTrue(sources['koita']['effective_enabled'])
  self.assertEqual(sources['koita']['automatic_for'],['iitp'])
  self.assertFalse(sources['d2b']['effective_enabled'])
 def test_existing_defense_routes_are_also_automatic(self):
  self.selected({'future','rapid','krit'})
  with patch('aclbrief.service.collect_incremental',side_effect=self.batch):self.s.collect()
  self.assertEqual(set(self.calls),{'future','rapid','krit','dapa','dtims'});self.assertEqual(self.calls.count('dapa'),1)
 def test_unconfigured_or_unknown_providers_are_not_invented(self):
  self.assertEqual(collection_plan([{'id':'iitp','config':{'enabled':True}}])['automatic'],{})
  with self.assertRaises(ValueError):collection_plan(self.s.store.sources(),['unknown'])
