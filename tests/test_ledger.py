import concurrent.futures
import contextlib
import json
import tempfile
import unittest
from pathlib import Path

from agentops.ledger import Ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.db = Ledger(self.path)
        self.snapshot = {'repo': 'o/r', 'main_sha': 'a' * 40, 'prs': []}
        self.snapshot_id = self.db.save_snapshot(self.snapshot)
        self.run = self.db.create_run(self.snapshot_id, {'concurrency': 2})

    def enqueue(self, number=1, head='b' * 40):
        return self.db.enqueue(self.run, {'number': number, 'head_sha': head}, 'a' * 40)

    def test_snapshot_identity_is_content_addressed_and_immutable(self):
        self.assertEqual(self.db.save_snapshot(dict(self.snapshot)), self.snapshot_id)
        changed = {**self.snapshot, 'main_sha': 'c' * 40}
        self.assertNotEqual(self.db.save_snapshot(changed), self.snapshot_id)
        self.assertEqual(self.db.get_snapshot(self.snapshot_id)['main_sha'], 'a' * 40)

    def test_duplicate_revision_enqueue_reuses_job(self):
        first = self.enqueue()
        self.assertEqual(self.enqueue(), first)
        self.assertNotEqual(self.enqueue(head='c' * 40), first)

    def test_competing_connections_honor_global_concurrency(self):
        for number in range(1, 9):
            self.enqueue(number)
        def claim(i):
            return Ledger(self.path).claim(f'worker-{i}', 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claimed = [j for j in pool.map(claim, range(8)) if j]
        self.assertEqual(len(claimed), 2)
        self.assertEqual(len({j['id'] for j in claimed}), 2)

    def test_same_revision_not_concurrently_claimed_across_runs(self):
        self.enqueue()
        second = self.db.create_run(self.snapshot_id, {'concurrency': 2})
        self.db.enqueue(second, {'number': 1, 'head_sha': 'b' * 40}, 'a' * 40)
        self.assertIsNotNone(self.db.claim('one', 2))
        self.assertIsNone(self.db.claim('two', 2))

    def test_finish_requires_owner_and_preserves_result(self):
        job = self.enqueue()
        self.db.claim('one', 2)
        with self.assertRaises(ValueError):
            self.db.finish(job, 'other', 'held', {'why': 'proof'})
        self.db.finish(job, 'one', 'held', {'why': 'proof'})
        self.assertEqual(self.db.job(job)['result'], {'why': 'proof'})
        self.assertEqual(self.db.status()['states'], {'held': 1})

    def test_adoption_requires_verified_dead_coordinator_and_unchanged_claim(self):
        job = self.enqueue()
        self.db.claim('old', 2)
        self.assertFalse(self.db.adopt(job, 'new', lambda owner: 'live'))
        self.assertFalse(self.db.adopt(job, 'new', lambda owner: 'unknown'))
        self.assertTrue(self.db.adopt(job, 'new', lambda owner: 'missing'))
        self.assertEqual(self.db.job(job)['owner'], 'new')
        self.assertEqual(self.db.job(job)['attempt'], 1)

    def test_main_invalidation_does_not_erase_live_handle(self):
        job = self.enqueue()
        self.db.claim('one', 2)
        self.db.attach_handle(job, 'one', {'container': 'owned-job'})
        self.db.invalidate('o/r', 'c' * 40, {1: 'b' * 40})
        self.assertTrue(self.db.job(job)['stale'])
        self.assertEqual(self.db.job(job)['state'], 'running')
        self.assertEqual(self.db.job(job)['handle']['container'], 'owned-job')
        with self.assertRaises(ValueError):
            self.db.finish(job, 'one', 'ready', {})
        self.db.finish(job, 'one', 'stale', {})

    def test_scoped_inventory_does_not_invalidate_unobserved_current_job(self):
        old = self.enqueue(1)
        current = self.db.enqueue(self.run, {'number': 2, 'head_sha': 'b'*40}, 'c'*40)
        self.db.invalidate('o/r', 'c'*40, {1: 'b'*40}, scope=[1])
        self.assertTrue(self.db.job(old)['stale'])
        self.assertFalse(self.db.job(current)['stale'])
        self.assertEqual(self.db.job(current)['state'], 'queued')

    def test_recovery_never_restarts_live_or_unknown_handle(self):
        job = self.enqueue()
        self.db.claim('dead-coordinator', 2)
        self.db.attach_handle(job, 'dead-coordinator', {'container': 'c'})
        self.assertFalse(self.db.recover(job, lambda h: 'live'))
        self.assertFalse(self.db.recover(job, lambda h: 'unknown'))
        self.assertEqual(self.db.job(job)['state'], 'running')
        self.assertTrue(self.db.recover(job, lambda h: 'missing'))
        self.assertEqual(self.db.job(job)['state'], 'interrupted')

    def test_recovery_without_handle_requires_owner_liveness_check(self):
        job = self.enqueue()
        self.db.claim('owner', 2)
        self.assertFalse(self.db.recover(job, lambda h: 'unknown'))
        self.assertTrue(self.db.recover(job, lambda h: 'missing'))

    def test_retry_stops_at_revision_budget(self):
        job = self.enqueue()
        for attempt in range(3):
            claimed = self.db.claim('one', 2)
            self.assertEqual(claimed['attempt'], attempt + 1)
            self.db.finish(job, 'one', 'failed', {})
            if attempt < 2:
                self.db.retry(job, 3)
        with self.assertRaises(ValueError):
            self.db.retry(job, 3)
        self.assertEqual(self.db.job(job)['state'], 'failed')

    def test_retry_retains_prior_attempt_evidence(self):
        job = self.enqueue()
        self.db.claim('one', 2)
        self.db.finish(job, 'one', 'failed', {'proof': 'first-session'})
        self.db.retry(job)
        self.db.claim('two', 2)
        self.db.finish(job, 'two', 'held', {'proof': 'second-session'})
        self.assertEqual([a['result']['proof'] for a in self.db.attempts(job)],
                         ['first-session', 'second-session'])

    def test_mechanical_reclassification_does_not_start_another_session(self):
        job = self.enqueue()
        self.db.claim('one', 2)
        self.db.finish(job, 'one', 'awaiting_ci', {'proof': 'source'})
        self.db.reclassify(job, 'ready', {'proof': 'source', 'ci': 'now green'})
        self.assertEqual(self.db.job(job)['state'], 'ready')
        self.assertEqual(self.db.job(job)['attempt'], 1)
        self.assertEqual(len(self.db.attempts(job)), 1)

    def test_notices_retain_provenance_and_scoped_delivery(self):
        notice = self.db.notice('o/r', 'baseline', 'inherited failure',
                                {'job': 'source'}, numbers=[1])
        self.assertEqual([n['id'] for n in self.db.notices('o/r', 1)], [notice])
        self.assertEqual(self.db.notices('o/r', 2), [])
        self.db.ack_notice(notice, 'session-one')
        self.db.ack_notice(notice, 'session-one')
        self.db.supersede_notice(notice, 'fixed upstream', {'commit': 'c' * 40})
        self.assertEqual(self.db.notices('o/r', 1), [])

    def test_action_journal_requires_reconciliation_after_ambiguous_write(self):
        job = self.enqueue()
        action = {'kind': 'comment', 'body': 'finding'}
        first = self.db.intent(job, action)
        self.assertEqual(self.db.intent(job, action), first)
        self.db.action_state(first, 'sending')
        self.assertEqual(self.db.action(first)['state'], 'sending')
        self.db.action_state(first, 'sent', {'url': 'https://example.test/comment/1'})
        self.assertEqual(self.db.action(first)['receipt']['url'], 'https://example.test/comment/1')

    def test_stopping_run_prevents_new_claims(self):
        self.enqueue()
        self.db.stop(self.run)
        self.assertIsNone(self.db.claim('one', 2))

    def test_unknown_schema_version_is_rejected(self):
        import sqlite3
        with contextlib.closing(sqlite3.connect(self.path)) as connection:
            connection.execute('PRAGMA user_version=999')
        with self.assertRaises(ValueError):
            Ledger(self.path)


if __name__ == '__main__':
    unittest.main()
