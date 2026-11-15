import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.provider_usage import (codex_rpc, parse_codex, parse_claude, parse_copilot,
                                    read_usage, authentication_fingerprint, staged_fingerprint)


class ProviderUsageTests(unittest.TestCase):
    def test_codex_quota_response_must_belong_to_the_staged_subscription_account(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'auth.json').write_text(json.dumps({
                'auth_mode': 'chatgpt', 'tokens': {'account_id': 'synthetic-account-A'}}), encoding='utf8')
            payload = {'accountId': 'synthetic-account-B', 'rateLimits': {'primary': {
                'usedPercent': 10, 'windowDurationMins': 10080, 'resetsAt': 2000000000}}}
            with patch('agentops.provider_usage.codex_command', return_value=['fake']), \
                    patch('agentops.provider_usage.codex_rpc', return_value=payload), \
                    patch.dict('os.environ', {}, clear=True):
                with self.assertRaises(ValueError):
                    read_usage({'harness': 'codex', 'auth_root': directory})
                payload['accountId'] = 'synthetic-account-A'
                result = read_usage({'harness': 'codex', 'auth_root': directory})
                self.assertEqual(result['auth_fingerprint'], authentication_fingerprint(
                    {'harness': 'codex', 'auth_root': directory}))

    def test_claude_usage_reads_the_same_file_account_as_worker_authentication(self):
        from agentops.worker import auth_plan
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.credentials.json'
            path.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'synthetic-account-A'}}), encoding='utf8')
            with patch.dict('os.environ', {'CLAUDE_CODE_OAUTH_TOKEN': 'synthetic-account-B'}, clear=True), \
                    patch('agentops.provider_usage.account_json') as api:
                api.return_value = {'seven_day': {'utilization': 60, 'resets_at': '2033-05-18T03:33:20Z'}}
                read_usage({'harness': 'claude', 'auth_root': directory})
                self.assertEqual(api.call_args.args[1]['Authorization'], 'Bearer synthetic-account-A')
                auth = auth_plan('claude', {}, directory)
                fingerprint = staged_fingerprint('claude',
                    {name: Path(source).read_bytes() for name, source in auth['files'].items()}, auth['env'])
                self.assertEqual(authentication_fingerprint({'harness': 'claude', 'auth_root': directory}), fingerprint)

    def test_ambient_api_key_cannot_authorize_subscription_usage_for_an_api_worker(self):
        for provider, name in (('codex', 'OPENAI_API_KEY'), ('claude', 'ANTHROPIC_API_KEY')):
            with self.subTest(provider=provider), patch.dict('os.environ', {name: 'synthetic-key'}, clear=True), \
                    patch('agentops.provider_usage.codex_rpc') as rpc, \
                    patch('agentops.provider_usage.account_json') as api, self.assertRaises(ValueError):
                read_usage({'harness': provider})
            rpc.assert_not_called()
            api.assert_not_called()

    def test_codex_weekly_window_is_identified_by_duration_not_position(self):
        result = parse_codex({'rateLimits': {'primary': {
            'usedPercent': 60, 'windowDurationMins': 10080, 'resetsAt': 2000000000}}})
        self.assertEqual(result['windows'][0]['period'], 'weekly')
        self.assertEqual(result['windows'][0]['used_percent'], 60)
        self.assertEqual(result['windows'][0]['resets_at'], 2000000000)

    def test_codex_preserves_all_buckets_and_does_not_double_count_legacy_view(self):
        bucket = {'primary': {'usedPercent': 20, 'windowDurationMins': 300,
                              'resetsAt': 2000000000}, 'secondary': {
            'usedPercent': 81, 'windowDurationMins': 10080, 'resetsAt': 2000000001}}
        result = parse_codex({'rateLimits': bucket, 'rateLimitsByLimitId': {'codex': bucket}})
        self.assertEqual(len(result['windows']), 2)
        self.assertEqual([x['used_percent'] for x in result['windows']], [20, 81])

    def test_codex_explicit_denial_is_retained_even_below_threshold(self):
        result = parse_codex({'ordinaryUsageAllowed': False, 'rateLimits': {'primary': {
            'usedPercent': 20, 'windowDurationMins': 10080, 'resetsAt': 2000000000}}})
        self.assertTrue(result['blocked'])

    def test_missing_or_malformed_windows_never_become_zero(self):
        for raw in ({}, {'rateLimits': {}}, {'rateLimits': {'primary': {
                'usedPercent': None, 'windowDurationMins': 10080, 'resetsAt': 2000000000}}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_codex(raw)

    def test_claude_weekly_and_model_windows_use_real_account_utilization(self):
        result = parse_claude({'five_hour': {'utilization': 14, 'resets_at': '2033-05-18T03:33:20Z'},
                              'seven_day': {'utilization': 79.5, 'resets_at': '2033-05-18T03:33:20+00:00'},
                              'seven_day_opus': {'utilization': 82, 'resets_at': '2033-05-18T03:33:20Z'}})
        self.assertEqual([w['period'] for w in result['windows']], ['short', 'weekly', 'weekly'])
        self.assertEqual([w['used_percent'] for w in result['windows']], [14, 79.5, 82])

    def test_copilot_monthly_allowance_is_not_mislabeled_as_weekly(self):
        result = parse_copilot({'quota_reset_date': '2033-06-01T00:00:00Z', 'quota_snapshots': {
            'premium_interactions': {'entitlement': 300, 'remaining': 60,
                                     'percent_remaining': 20, 'unlimited': False}}})
        self.assertEqual(result['windows'][0]['period'], 'monthly')
        self.assertEqual(result['windows'][0]['used_percent'], 80)

    def test_copilot_unknown_balance_is_not_zero_or_unlimited(self):
        with self.assertRaises(ValueError):
            parse_copilot({'quota_snapshots': {'premium_interactions': {'remaining': None}}})

    def test_codex_protocol_initializes_and_reads_without_creating_a_thread(self):
        script = '''import json,sys
first=json.loads(input())
assert first['method']=='initialize'
print(json.dumps({'id':first['id'],'result':{}}),flush=True)
assert json.loads(input())['method']=='initialized'
last=json.loads(input())
assert last['method']=='account/rateLimits/read'
print(json.dumps({'id':last['id'],'result':{'rateLimits':{'primary':{
'usedPercent':60,'windowDurationMins':10080,'resetsAt':2000000000}}}}),flush=True)
'''
        result = codex_rpc([sys.executable, '-c', script], {}, timeout=3)
        self.assertEqual(parse_codex(result)['windows'][0]['used_percent'], 60)

    def test_codex_protocol_timeout_does_not_leave_probe_running(self):
        with self.assertRaises(TimeoutError):
            codex_rpc([sys.executable, '-c', 'import time;time.sleep(20)'], {}, timeout=0.1)


if __name__ == '__main__':
    unittest.main()
