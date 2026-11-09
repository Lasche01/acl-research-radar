import json
import socket
import sqlite3
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch, Mock
from aclbrief.collect import Fetcher, FetchError, certificate_context, collect_source
from aclbrief.diagnostics import classify_error, summarize_sources
from aclbrief.service import Service
from aclbrief.store import Store


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = Service(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_disabled_sources_are_not_failures(self):
        for s in self.svc.store.sources():
            if s['config']['enabled']:
                self.svc.store.source_result(s['id'], 'success', 1)
        summary = self.svc.state()['source_summary']
        self.assertEqual((summary['failed'], summary['enabled'], summary['disabled']), (0, 9, 11))
        self.assertEqual(summary['not_run'], 0)

    def test_select_all_and_clear_all_preserve_configs_and_history(self):
        self.svc.store.source_result('add', 'partial', 10, '처리 한도')
        before = {s['id']: s for s in self.svc.store.sources()}
        self.assertEqual(self.svc.store.set_sources_enabled(True), {'updated': 20, 'enabled': True})
        self.assertEqual(self.svc.state()['source_summary']['enabled'], 20)
        for s in self.svc.store.sources():
            self.assertEqual(s['config'], {**before[s['id']]['config'], 'enabled': True})
            self.assertEqual(s['status'], before[s['id']]['status'])
            self.assertEqual(s['checked_at'], before[s['id']]['checked_at'])
        self.svc.store.set_sources_enabled(False)
        self.assertEqual(self.svc.state()['source_summary']['enabled'], 0)
        self.assertEqual(self.svc.state()['source_summary']['disabled'], 20)

    def test_bulk_selection_requires_boolean(self):
        for value in ('false', 0, 1, None, []):
            with self.assertRaises(ValueError): self.svc.store.set_sources_enabled(value)
        self.assertEqual(self.svc.state()['source_summary']['enabled'], 9)

    def test_windows_localized_timeout_is_retryable(self):
        error = FetchError('robots.txt를 확인하지 못해 수집을 보류했습니다: <urlopen error [WinError 10060] 연결된 구성원으로부터 응답이 없어 연결하지 못했습니다>', 'unknown', 'robots')
        result = classify_error(error)
        self.assertEqual(result['code'], 'timeout')
        self.assertTrue(result['retryable'])

    def test_windows_socket_timeout_is_retried(self):
        f = Fetcher(attempts=2)
        response = object()
        f.opener = Mock()
        f.opener.open.side_effect = [urllib.error.URLError(OSError(10060, '응답이 없어 연결하지 못했습니다')), response]
        with patch('aclbrief.collect.time.sleep'):
            self.assertIs(f._open_with_retry(urllib.request.Request('https://example.org/')), response)
        self.assertEqual(f.opener.open.call_count, 2)

    def test_uploaded_log_error_categories_remain_distinct(self):
        data = {
            'iris': ('review', '정적 HTML에서 상세 링크를 찾지 못했습니다.'),
            'iitp': ('failed', 'robots.txt를 확인하지 못해 수집을 보류했습니다: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate'),
            'etri': ('failed', 'robots.txt를 확인하지 못해 수집을 보류했습니다: <urlopen error timed out>'),
            'koita': ('review', '정적 HTML에서 상세 링크를 찾지 못했습니다.'),
            'ntis': ('failed', 'robots.txt가 이 경로의 자동 수집을 허용하지 않습니다.'),
            'add': ('failed', 'robots.txt를 확인하지 못해 수집을 보류했습니다: certificate verify failed'),
            'nrf': ('failed', 'robots.txt가 이 경로의 자동 수집을 허용하지 않습니다.'),
            'nipa': ('review', '정적 HTML에서 상세 링크를 찾지 못했습니다.'),
            'kca': ('partial', '첫 목록에서 발견한 10건 중 4건 처리.')}
        for sid, (status, error) in data.items():
            self.svc.store.source_result(sid, status, 0, error)
        summary = self.svc.state()['source_summary']
        self.assertEqual({k: summary[k] for k in ('failed','blocked','adapter_needed','partial','not_run','disabled')},
                         {'failed':3,'blocked':2,'adapter_needed':3,'partial':1,'not_run':0,'disabled':11})
        sources = {s['id']:s for s in self.svc.store.sources()}
        self.assertEqual(sources['iitp']['diagnostic']['code'], 'certificate')
        self.assertEqual(sources['iitp']['diagnostic']['stage'], 'robots')
        self.assertEqual(sources['ntis']['status'], 'blocked')

    def test_general_review_note_does_not_mark_collection_partial(self):
        class F:
            def fetch(self, url):
                content = '<a href="/view/1">과제 연구 공고</a>' if url.endswith('/list') else '<html><body>' + '연구 목표와 채널 추정 성과물의 확인이 필요합니다. '*12 + '</body></html>'
                return content.encode(), {}, url
        r = collect_source({'id':'test','url':'https://example.org/list','detail_pattern':'/view/'}, F(), lambda p:{'saved':True})
        self.assertEqual(r['status'], 'success')
        self.assertEqual(r['error'], '')
        self.assertTrue(r['notes'])
        self.assertEqual(r['coverage'], 'first_page_only')

    def test_partial_with_saved_details_updates_last_success(self):
        self.svc.store.source_result('kca', 'partial', 10, '4/10', {'code':'coverage_limited','processed':4})
        self.assertIsNotNone(next(s for s in self.svc.store.sources() if s['id']=='kca')['last_success'])

    def test_diagnostics_omit_notice_body_and_feedback(self):
        self.svc.demo()
        d = self.svc.diagnostics()
        self.assertNotIn('profile', d)
        self.assertNotIn('notices', d)
        self.assertNotIn('feedback', d)
        self.assertTrue(d['network']['certificate_verification'])

    def test_certificates_are_always_verified(self):
        context, _ = certificate_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_timeout_retried_once(self):
        f = Fetcher(attempts=2)
        success = object()
        f.opener = Mock()
        f.opener.open.side_effect = [urllib.error.URLError(socket.timeout('timed out')), success]
        with patch('aclbrief.collect.time.sleep'):
            self.assertIs(f._open_with_retry(urllib.request.Request('https://example.org/')), success)
        self.assertEqual(f.opener.open.call_count, 2)

    def test_certificate_failure_is_not_retried(self):
        f = Fetcher(attempts=2)
        f.opener = Mock()
        f.opener.open.side_effect = urllib.error.URLError(ssl.SSLCertVerificationError('certificate verify failed'))
        with patch('aclbrief.collect.time.sleep') as sleep:
            with self.assertRaises(urllib.error.URLError):f._open_with_retry(urllib.request.Request('https://example.org/'))
        self.assertEqual(f.opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_robots_denial_is_classified_and_no_target_fetch_occurs(self):
        f = Fetcher(delay=0,respect_robots=True)
        parser = Mock()
        parser.can_fetch.return_value = False
        f.robots['https://example.org'] = parser
        f.opener = Mock()
        with patch('aclbrief.collect.public_url', side_effect=lambda u:u):
            with self.assertRaises(FetchError) as exc:f.fetch('https://example.org/private')
        self.assertEqual(classify_error(exc.exception)['code'], 'robots_disallowed')
        f.opener.open.assert_not_called()

    def test_v01_database_migration_preserves_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'briefing.sqlite3'
            with sqlite3.connect(p) as db:
                db.execute('CREATE TABLE sources(id TEXT PRIMARY KEY,config TEXT,status TEXT,checked_at TEXT,last_success TEXT,error TEXT,discovered INTEGER)')
                db.execute('INSERT INTO sources VALUES(?,?,?,?,?,?,?)', ('old',json.dumps({'id':'old','enabled':False}),'unchecked',None,None,'',0))
            store = Store(d)
            rows = store.sources()
            self.assertEqual(rows[0]['id'], 'old')
            self.assertEqual(rows[0]['diagnostic']['code'], 'unchecked')

    def test_server_errors_retry_but_forbidden_does_not(self):
        f = Fetcher(attempts=2)
        req = urllib.request.Request('https://example.org/')
        f.opener = Mock()
        f.opener.open.side_effect = urllib.error.HTTPError(req.full_url, 503, 'Service unavailable', {}, None)
        with patch('aclbrief.collect.time.sleep'):
            with self.assertRaises(urllib.error.HTTPError):f._open_with_retry(req)
        self.assertEqual(f.opener.open.call_count, 2)
        f.opener.reset_mock()
        f.opener.open.side_effect = urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {}, None)
        with self.assertRaises(urllib.error.HTTPError):f._open_with_retry(req)
        self.assertEqual(f.opener.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
