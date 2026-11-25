import concurrent.futures
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from agentops.allowances import Allowances, validate_settings
from agentops.provider_usage import authentication_fingerprint
from agentops.ledger import Ledger


def observation(used=60, reset=2000):
    return {'source': 'test account', 'blocked': False, 'auth_fingerprint': 'synthetic', 'windows': [
        {'id': 'weekly', 'label': 'Weekly', 'period': 'weekly', 'window_minutes': 10080,
         'used_percent': used, 'resets_at': reset}]}


class AllowanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Ledger(Path(self.temp.name) / 'ledger.db')
        self.now = 1000
        self.used = 60
        self.calls = 0
        self.config = {'harness': 'codex', 'auth_root': self.temp.name}
        self.limits = Allowances(self.ledger, self.config, reader=self.read,
                                 clock=lambda: self.now, identity_reader=lambda config: 'synthetic')

    def read(self, config):
        self.calls += 1
        return observation(self.used)

    def test_threshold_is_inclusive_and_settings_survive_new_instances(self):
        self.assertTrue(self.limits.check()['allowed'])
        self.limits.configure({'stop_percent': 60})
        self.assertFalse(self.limits.check()['allowed'])
        second = Allowances(self.ledger, self.config, reader=self.read, clock=lambda: self.now,
                            identity_reader=lambda config: 'synthetic')
        self.assertEqual(second.status()['stop_percent'], 60)
        second.configure({'stop_percent': 61})
        self.assertTrue(self.limits.check()['allowed'])
        self.assertEqual(self.calls, 1)

    def test_unknown_and_failed_refresh_pause_with_no_secret_error_output(self):
        self.assertFalse(self.limits.status()['allowed'])
        def broken(config):
            raise RuntimeError('Authorization: Bearer secret-value')
        self.limits.reader = broken
        status = self.limits.check()
        self.assertFalse(status['allowed'])
        self.assertEqual(status['state'], 'unavailable')
        self.assertNotIn('secret-value', str(status))
        self.assertIsNone(status['observed_at'])

    def test_failed_refresh_is_cooled_down_for_same_account_but_not_new_account(self):
        identity = ['first']
        calls = []
        self.limits.identity_reader = lambda config: identity[0]
        def unavailable(config):
            calls.append(True)
            raise OSError('unavailable')
        self.limits.reader = unavailable
        self.limits.check()
        self.limits.check()
        self.assertEqual(len(calls), 1)
        identity[0] = 'second'
        self.limits.check()
        self.assertEqual(len(calls), 2)

    def test_stale_usage_is_not_used_to_authorize_work(self):
        self.limits.check()
        self.now += 61
        self.assertFalse(self.limits.status()['allowed'])
        self.used = 82
        self.assertFalse(self.limits.check()['allowed'])
        self.assertEqual(self.calls, 2)

    def test_reset_requires_fresh_read_instead_of_assuming_zero(self):
        self.used = 80
        self.assertFalse(self.limits.check()['allowed'])
        self.now = 2001
        self.assertFalse(self.limits.status()['allowed'])
        self.limits.reader = lambda config: observation(1, reset=3000)
        self.assertTrue(self.limits.check()['allowed'])

    def test_provider_still_reporting_expired_reset_is_unavailable(self):
        self.now = 2001
        self.assertFalse(self.limits.check()['allowed'])

    def test_explicit_override_is_persisted_and_visibly_distinct_from_available(self):
        self.limits.configure({'enabled': False})
        status = self.limits.check()
        self.assertTrue(status['allowed'])
        self.assertEqual(status['state'], 'disabled')
        self.assertEqual(self.calls, 0)

    def test_settings_reject_invalid_percentages_and_unknown_fields(self):
        for payload in ({'stop_percent': 0}, {'stop_percent': 101}, {'stop_percent': True},
                        {'stop_percent': float('nan')}, {'enabled': 1}, {'bogus': 1}, {}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.limits.configure(payload)

    def test_any_applicable_window_can_stop_dispatch(self):
        self.limits.reader = lambda config: {**observation(10), 'windows': [
            *observation(10)['windows'], {**observation(80)['windows'][0], 'id': 'short'}]}
        self.assertFalse(self.limits.check()['allowed'])

    def test_monthly_reading_does_not_authorize_unknown_weekly_usage(self):
        monthly = observation(10)
        monthly['windows'][0]['period'] = 'monthly'
        monthly['windows'][0]['window_minutes'] = 0
        self.limits.reader = lambda config: monthly
        status = self.limits.check()
        self.assertFalse(status['allowed'])
        self.assertEqual(status['state'], 'partial')
        self.assertIn('Weekly', status['reason'])

    def test_configured_provider_threshold_is_used_until_explicitly_changed(self):
        config = {**self.config, 'provider_limits': {'codex': {'stop_percent': 50}}}
        limits = Allowances(self.ledger, config, reader=self.read, clock=lambda: self.now,
                            identity_reader=lambda config: 'synthetic')
        self.assertFalse(limits.check()['allowed'])

    def test_different_providers_and_accounts_do_not_share_usage(self):
        self.limits.check()
        for change in ({'harness': 'claude'}, {'auth_root': self.temp.name + '/other'}):
            other = Allowances(self.ledger, {**self.config, **change}, reader=self.read,
                               clock=lambda: self.now, identity_reader=lambda config: 'synthetic')
            self.assertIsNone(other.status()['observed_at'])
            other.check()
        self.assertEqual(self.calls, 3)

    def test_same_credential_path_with_new_account_invalidates_cache(self):
        path = Path(self.temp.name) / 'auth.json'
        path.write_text('{"account":"first"}', encoding='utf8')
        self.limits.check()
        path.write_text('{"account":"second"}', encoding='utf8')
        self.assertIsNone(self.limits.status()['observed_at'])

    def test_staged_account_must_match_final_allowance_account(self):
        path = Path(self.temp.name) / 'auth.json'
        path.write_text('{"account":"first"}', encoding='utf8')
        self.limits.identity_reader = authentication_fingerprint
        staged = {'auth_fingerprint': authentication_fingerprint(self.config)}
        self.assertTrue(self.limits.matches_prepared(staged))
        path.write_text('{"account":"second"}', encoding='utf8')
        self.assertFalse(self.limits.matches_prepared(staged))
        self.assertFalse(self.limits.matches_prepared({}))

    def test_cached_usage_for_different_credential_fingerprint_never_authorizes(self):
        identity = ['first']
        self.limits.identity_reader = lambda config: identity[0]
        self.limits.reader = lambda config: {**observation(10 if identity[0] == 'first' else 90),
                                            'auth_fingerprint': identity[0]}
        self.assertTrue(self.limits.check()['allowed'])
        identity[0] = 'second'
        self.assertFalse(self.limits.check()['allowed'])

    def test_identity_changed_between_scope_selection_and_collection_is_rejected(self):
        self.limits.identity_reader = lambda config: 'first'
        self.limits.reader = lambda config: {**observation(10), 'auth_fingerprint': 'second'}
        self.assertFalse(self.limits.check()['allowed'])

    def test_staged_identity_must_match_admitted_reading_and_current_credentials(self):
        self.limits.identity_reader = lambda config: 'second'
        self.assertFalse(self.limits.matches_prepared({'auth_fingerprint': 'second'},
                         {'enabled': True, '_auth_fingerprint': 'first'}))

    def test_codex_api_billing_is_not_enabled_by_disabling_allowance_enforcement(self):
        self.limits.configure({'enabled': False})
        with patch.dict('os.environ', {'OPENAI_API_KEY': 'synthetic-key'}):
            self.assertFalse(self.limits.check()['allowed'])
            self.assertFalse(self.limits.status()['allowed'])
        path = Path(self.temp.name) / 'auth.json'
        path.write_text('{"OPENAI_API_KEY":"synthetic-key"}', encoding='utf8')
        self.assertFalse(self.limits.check()['allowed'])

    def test_concurrent_coordinators_share_one_refresh_and_wait_safely(self):
        entered, release = threading.Event(), threading.Event()
        def slow(config):
            self.calls += 1
            entered.set()
            release.wait(3)
            return observation()
        self.limits.reader = slow
        other = Allowances(self.ledger, self.config, reader=slow, clock=lambda: self.now,
                           identity_reader=lambda config: 'synthetic')
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(self.limits.check)
            self.assertTrue(entered.wait(2))
            waiting = other.check()
            self.assertFalse(waiting['allowed'])
            self.assertTrue(waiting['refreshing'])
            release.set()
            self.assertTrue(future.result(3)['allowed'])
        self.assertEqual(self.calls, 1)

    def test_token_budget_setting_is_validated(self):
        validate_settings({'token_budget': 1000})
        for bad in (0, -1, '1000', float('nan')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_settings({'token_budget': bad})

    def _finished_job(self, harness, usage, heartbeat=None):
        snapshot = self.ledger.save_snapshot({'repo': 'a/b', 'complete': True, 'prs': []})
        run = self.ledger.create_run(snapshot, {'harness': harness})
        identity = self.ledger.enqueue(run, {'number': 1, 'head_sha': 'a' * 40}, 'b' * 40)
        job = self.ledger.claim('owner', 1)
        self.ledger.finish(identity, 'owner', 'reviewed',
                           {'execution': {'harness': harness, 'usage': usage}})
        if heartbeat is not None:
            with self.ledger.connect(immediate=True) as db:
                db.execute('UPDATE jobs SET heartbeat=? WHERE id=?', (heartbeat, identity))
        return identity

    def test_consumed_since_sums_only_this_provider_within_the_window(self):
        self._finished_job('devin', {'input_tokens': 1000, 'output_tokens': 100})
        self._finished_job('devin', {'input_tokens': 2000, 'output_tokens': 200})
        self._finished_job('codex', {'input_tokens': 9999, 'output_tokens': 999})
        self._finished_job('devin', {'input_tokens': 5, 'output_tokens': 5}, heartbeat=100)
        self._finished_job('devin', {'input_tokens': 7})  # reused/partial usage rows tolerate gaps
        limits = Allowances(self.ledger, {'harness': 'devin', 'auth_root': self.temp.name},
                            reader=self.read, clock=lambda: self.now,
                            identity_reader=lambda config: 'synthetic')
        self.assertEqual(limits._consumed_since(200), 3307)


if __name__ == '__main__':
    unittest.main()
