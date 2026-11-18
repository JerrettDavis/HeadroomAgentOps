import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.provider_usage import (codex_rpc, parse_codex, parse_claude, parse_copilot,
                                    parse_devin, apply_token_budget, read_usage,
                                    authentication_fingerprint, staged_fingerprint)


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

    def test_devin_weekly_quota_uses_remaining_percent_and_unix_reset(self):
        result = parse_devin({'userStatus': {'planStatus': {
            'weeklyQuotaRemainingPercent': 40, 'weeklyQuotaResetAtUnix': '2000000000',
            'dailyQuotaRemainingPercent': 90, 'dailyQuotaResetAtUnix': '1999999999'}}})
        self.assertEqual(result['windows'][0]['period'], 'weekly')
        self.assertEqual(result['windows'][0]['used_percent'], 60)
        self.assertEqual(result['windows'][0]['resets_at'], 2000000000)
        self.assertEqual(result['windows'][1]['used_percent'], 10)
        self.assertFalse(result['blocked'])

    def test_devin_exhausted_quota_and_limits_are_blocked(self):
        for status in ({'weeklyQuotaRemainingPercent': 0, 'weeklyQuotaResetAtUnix': '2000000000'},
                       {'acuConsumed': '100', 'acuLimit': '100', 'planEnd': '2033-06-01T00:00:00Z'},
                       {'dollarsConsumed': '50', 'dollarsLimit': '40', 'planEnd': '2033-06-01'}):
            with self.subTest(status=status):
                self.assertTrue(parse_devin({'userStatus': {'planStatus': status}})['blocked'])

    def test_devin_monthly_consumption_is_not_labeled_weekly(self):
        result = parse_devin({'userStatus': {'planStatus': {
            'acuConsumed': '25', 'acuLimit': '100', 'planEnd': '2033-06-01T00:00:00Z'}}})
        self.assertEqual(result['windows'][0]['period'], 'monthly')
        self.assertEqual(result['windows'][0]['used_percent'], 25)

    def test_devin_unlimited_plan_reports_no_windows(self):
        result = parse_devin({'userStatus': {'planStatus': {
            'availablePromptCredits': -1, 'planInfo': {'monthlyPromptCredits': -1}}}})
        self.assertTrue(result['unlimited'])
        with self.assertRaises(ValueError):
            parse_devin({'userStatus': {'planStatus': {}}})

    def test_devin_quota_plan_without_readings_is_not_unlimited(self):
        # Quota-billed accounts send credit fields as -1 sentinels, which do not
        # mean unlimited use; without real windows the reading is unavailable.
        with self.assertRaises(ValueError):
            parse_devin({'userStatus': {'planStatus': {
                'availablePromptCredits': -1, 'dailyQuotaResetAtUnix': '1794988800',
                'planInfo': {'monthlyPromptCredits': -1,
                             'billingStrategy': 'BILLING_STRATEGY_QUOTA'}}}})

    def test_devin_token_budget_builds_a_measured_weekly_window(self):
        status = {'userStatus': {'planStatus': {'weeklyQuotaResetAtUnix': '2000000000',
            'planInfo': {'billingStrategy': 'BILLING_STRATEGY_QUOTA'}}}}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'credentials.toml').write_text(
                'windsurf_api_key = "synthetic-account"\n', encoding='utf8')
            config = {'harness': 'devin', 'auth_root': directory,
                      'provider_limits': {'devin': {'token_budget': 2000}},
                      '_consumed_since': lambda since: 500}
            with patch('agentops.provider_usage.devin_status', return_value=status):
                result = read_usage(config)
        [window] = result['windows']
        self.assertEqual(window['period'], 'weekly')
        self.assertEqual(window['resets_at'], 2000000000)
        self.assertEqual(window['used_percent'], 25)
        self.assertFalse(result['blocked'])

    def test_devin_token_budget_blocks_at_full_consumption_and_fails_closed_without(self):
        status = {'userStatus': {'planStatus': {'weeklyQuotaResetAtUnix': '2000000000'}}}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'credentials.toml').write_text(
                'windsurf_api_key = "synthetic-account"\n', encoding='utf8')
            config = {'harness': 'devin', 'auth_root': directory,
                      'provider_limits': {'devin': {'token_budget': 2000}},
                      '_consumed_since': lambda since: 2500}
            with patch('agentops.provider_usage.devin_status', return_value=status):
                result = read_usage(config)
            self.assertTrue(result['blocked'])
            self.assertEqual(result['windows'][0]['used_percent'], 125)
            del config['_consumed_since'], config['provider_limits']
            with patch('agentops.provider_usage.devin_status', return_value=status):
                with self.assertRaises(ValueError):
                    read_usage(config)

    def test_token_budget_uses_the_providers_own_weekly_reset(self):
        windows = [{'id': 'w', 'label': 'w', 'period': 'weekly', 'window_minutes': 10080,
                    'used_percent': 10, 'resets_at': 2000000000}]
        config = {'provider_limits': {'codex': {'token_budget': 1000}},
                  '_consumed_since': lambda since: 500}
        result = apply_token_budget(config, 'codex', {'windows': list(windows), 'blocked': False})
        self.assertEqual(len(result['windows']), 2)
        self.assertEqual(result['windows'][1]['used_percent'], 50)
        self.assertEqual(result['windows'][1]['resets_at'], 2000000000)
        # Providers reporting only monthly or short windows cannot bound a weekly budget.
        monthly = [{'id': 'm', 'label': 'm', 'period': 'monthly', 'window_minutes': 43200,
                    'used_percent': 10, 'resets_at': 2000000000}]
        self.assertEqual(len(apply_token_budget(config, 'codex',
                                                {'windows': monthly})['windows']), 1)

    def test_devin_usage_reads_the_staged_credentials_account(self):
        from agentops.worker import auth_plan
        with tempfile.TemporaryDirectory() as directory:
            credentials = ('windsurf_api_key = "synthetic-account-A"\n'
                           'api_server_url = "https://synthetic.example.test"\n')
            (Path(directory) / 'credentials.toml').write_text(credentials, encoding='utf8')
            with patch('agentops.provider_usage.devin_status') as status:
                status.return_value = {'userStatus': {'planStatus': {
                    'weeklyQuotaRemainingPercent': 40, 'weeklyQuotaResetAtUnix': '2000000000'}}}
                result = read_usage({'harness': 'devin', 'auth_root': directory})
            self.assertEqual(status.call_args.args, ('https://synthetic.example.test', 'synthetic-account-A'))
            auth = auth_plan('devin', {}, directory)
            fingerprint = staged_fingerprint('devin',
                {name: Path(source).read_bytes() for name, source in auth['files'].items()}, auth['env'])
            self.assertEqual(result['auth_fingerprint'], fingerprint)
            self.assertEqual(result['auth_fingerprint'], authentication_fingerprint(
                {'harness': 'devin', 'auth_root': directory}))

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
