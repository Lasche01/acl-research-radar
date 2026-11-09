"""Opt-in runtime and privacy contracts. No live API requests or real keys."""
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from aclbrief.service import Service
from aclbrief.rules import RuleReviewer
from aclbrief.screening import Reviewer, API_REVISION
from test_screening import payload, positive, RESEARCH, ELIGIBILITY, DEADLINE


class GPTModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-not-a-key',
                                     'ACL_OPENAI_MODEL': 'synthetic-model', 'ACL_AI_MAX_CALLS': '20'})
        env.start(); self.addCleanup(env.stop)
        self.svc = Service(self.tmp.name, reviewer_mode='openai')
        self.profile = self.svc.store.get_setting('profile')
        self.result = positive(self.profile)
        request = patch.object(self.svc.reviewer, 'request', return_value=self.result)
        self.request = request.start(); self.addCleanup(request.stop)

    def record(self, p=None):
        n = self.svc.ingest(p or payload())
        return self.svc.notice_detail(n['id'])

    def test_normal_service_does_not_opt_in_from_legacy_environment(self):
        with patch.dict(os.environ, {'ACL_AI_PROVIDER': 'openai'}):
            other = Service(self.tmp.name)
            self.assertIsInstance(other.reviewer, RuleReviewer)
            other.ingest(payload())
        self.request.assert_not_called()

    def test_explicit_mode_is_real_reviewer_and_reports_actual_requests(self):
        self.assertIs(type(self.svc.reviewer), Reviewer)
        n = self.record()
        self.assertEqual(n['screening']['provider'], 'openai')
        self.assertEqual(n['screening']['reviewer_revision'], API_REVISION)
        self.assertEqual(n['analysis']['mode'], 'openai_screening')
        self.assertEqual(self.svc.state()['ai']['calls'], 1)
        self.assertEqual(n['screening']['gate_sources']['communications'], '공고 본문')

    def test_unknown_mode_rejected_before_creating_data(self):
        with self.assertRaises(ValueError):
            Service(self.tmp.name, reviewer_mode='made-up')

    def test_unconfigured_api_stays_review_without_request(self):
        self.svc.reviewer.key = ''
        self.assertEqual(self.record()['screening']['decision'], 'review')
        self.request.assert_not_called()

    def test_expired_call_stops_before_api(self):
        n = self.record(payload(text=payload()['text'].replace(DEADLINE, '신청 마감일은 2020년 1월 1일입니다.')))
        self.assertEqual(n['screening']['decision'], 'excluded')
        self.request.assert_not_called()

    def test_unknown_period_stops_before_api(self):
        self.assertEqual(self.record(payload(text=payload()['text'].replace(DEADLINE, '')))['screening']['decision'], 'review')
        self.request.assert_not_called()

    def test_upcoming_period_stops_before_api(self):
        text = payload()['text'].replace(DEADLINE, '신청 기간: 2099-01-01 ~ 2099-12-31')
        self.assertEqual(self.record(payload(text=text))['screening']['decision'], 'review')
        self.request.assert_not_called()

    def test_title_without_communications_stops_before_api(self):
        self.assertEqual(self.record(payload(title='AI 신산업 신규과제 모집'))['screening']['decision'], 'review')
        self.request.assert_not_called()

    def test_metadata_only_does_not_spend_api_calls(self):
        self.record(payload(collection_state='title_metadata_only'))
        self.request.assert_not_called()

    def test_optional_manual_failure_does_not_block_primary_rfp(self):
        p = payload(attachments=[{'name': '접수 매뉴얼.pdf', 'status': 'review'}])
        n = self.record(p)
        self.assertEqual(n['screening']['decision'], 'recommended')
        self.assertTrue(n['screening']['optional_issues'])
        self.request.assert_called_once()

    def test_required_rfp_failure_blocks_api(self):
        n = self.record(payload(attachments=[{'name': 'RFP.pdf', 'status': 'review'}]))
        self.assertEqual(n['screening']['decision'], 'review')
        self.assertTrue(n['screening']['blocking_issues'])
        self.request.assert_not_called()

    def test_optional_manual_is_not_sent_or_used_as_eligibility_proof(self):
        text = payload()['text'].replace(ELIGIBILITY, '') + '\n[첨부 접수 매뉴얼.txt]\n' + ELIGIBILITY
        n = self.record(payload(text=text))
        self.assertNotIn(ELIGIBILITY, self.request.call_args.args[0]['text'])
        self.assertEqual(n['screening']['decision'], 'review')
        self.assertNotIn('validated', n['screening'])

    def test_primary_rfp_quote_keeps_source_provenance(self):
        text = payload()['text'].replace(RESEARCH, '') + '\n[첨부 빔 탐색 RFP.txt]\n' + RESEARCH
        n = self.record(payload(text=text))
        self.assertEqual(n['screening']['decision'], 'recommended')
        self.assertEqual(n['screening']['gate_sources']['communications'], '빔 탐색 RFP.txt')

    def test_rules_cache_cannot_be_presented_as_gpt_result(self):
        old = RuleReviewer().assess(payload(), self.profile)
        self.svc.reviewer.assess(payload(), self.profile, old)
        self.request.assert_called_once()

    def test_prompt_revision_change_invalidates_cache(self):
        old = self.svc.reviewer.assess(payload(), self.profile)
        old['reviewer_revision'] = 'old-prompt'
        self.svc.reviewer.assess(payload(), self.profile, old)
        self.assertEqual(self.request.call_count, 2)

    def test_reading_model_change_holds_until_reanalysis(self):
        self.record(); self.svc.reviewer.model = 'different-model'
        self.assertEqual(self.svc.notice_records()[0]['screening']['decision'], 'review')
        self.assertEqual(self.request.call_count, 1)

    def test_reading_mode_change_does_not_reuse_old_recommendation(self):
        n = self.record()
        other = Service(self.tmp.name)
        self.assertEqual(other.notice_detail(n['id'])['screening']['decision'], 'review')

    def test_api_failure_never_falls_back_to_rules_recommendation(self):
        self.request.side_effect = RuntimeError('synthetic-not-a-key')
        n = self.record()
        self.assertEqual(n['screening']['decision'], 'review')
        self.assertFalse(n['screening'].get('validated'))
        for output in (self.svc.state(), self.svc.diagnostics(), self.svc.store.export()):
            self.assertNotIn('synthetic-not-a-key', json.dumps(output))

    def test_http_body_and_headers_are_not_persisted(self):
        self.request.side_effect = urllib.error.HTTPError('https://api.openai.com/v1/responses', 401,
            'synthetic-not-a-key', {'secret': 'synthetic-not-a-key'}, io.BytesIO(b'synthetic-not-a-key'))
        self.record()
        self.assertNotIn('synthetic-not-a-key', json.dumps(self.svc.store.export()))

    def test_unknown_eligibility_can_be_related_but_not_recommended(self):
        self.result['eligibility'].update(verdict='unknown', quote='', reason='적용 자격을 확인해야 합니다.')
        n = self.record()
        self.assertEqual(n['screening']['decision'], 'review')
        self.assertTrue(n['screening']['related_candidate'])

    def test_excluded_call_cannot_be_related_candidate(self):
        self.result['communications']['verdict'] = 'no'
        n = self.record()
        self.assertEqual(n['screening']['decision'], 'excluded')
        self.assertFalse(n['screening']['related_candidate'])

    def test_read_only_views_never_trigger_api_requests(self):
        self.record(); count = self.request.call_count
        self.svc.state(); self.svc.briefing(); self.svc.diagnostics(); self.svc.evaluate()
        self.assertEqual(self.request.call_count, count)

    def test_cli_missing_key_fails_without_printing_secrets(self):
        from aclbrief.__main__ import main
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}), patch('sys.argv', ['aclbrief', '--reviewer', 'openai', 'serve']), patch('sys.stderr', new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as exit:
                main()
        self.assertEqual(exit.exception.code, 2)
        self.assertIn('OPENAI_API_KEY', err.getvalue())
        self.assertNotIn('synthetic-not-a-key', err.getvalue())

    def test_launcher_selects_api_with_key_in_process_only(self):
        import start_gpt
        with patch('sys.argv', ['start_gpt.py', 'serve']), patch('builtins.print'), patch('builtins.input') as prompt, patch('getpass.getpass') as key:
            start_gpt.configure()
            import sys
            self.assertEqual(sys.argv[1:3], ['--reviewer', 'openai'])
            prompt.assert_not_called(); key.assert_not_called()


if __name__ == '__main__':
    unittest.main()
