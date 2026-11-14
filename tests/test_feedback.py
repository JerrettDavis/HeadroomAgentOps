import tempfile
import unittest
from pathlib import Path

from agentops.evidence import Evidence
from agentops.ledger import Ledger
from agentops.feedback import replay_feedback


class FeedbackReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ledger = Ledger(root / 'ledger.db')
        self.evidence = Evidence(root / 'evidence')
        self.pr = {'number': 1, 'head_sha': 'b' * 40}
        snap = self.ledger.save_snapshot({'repo': 'o/r', 'prs': [self.pr]})
        self.run = self.ledger.create_run(snap, {})
        self.job = self.ledger.enqueue(self.run, self.pr, 'a' * 40)
        self.ledger.claim('worker', 1)
        self.result = {'outcome': 'held', 'qualification_limits': ['Tests missing'],
                       'reviewed_pr_evidence': self.evidence.put(self.pr),
                       'actions': [{'kind': 'comment', 'body': 'Verified bug'},
                                   {'kind': 'push', 'candidate_sha': 'c' * 40}],
                       'findings': []}
        self.ledger.finish(self.job, 'worker', 'held', self.result)
        self.calls = []
        calls = self.calls
        class Publisher:
            def publish(self, job, original, result):
                calls.append((job, original, result))
                return {'sent': [{'id': 10}], 'held': [],
                        'own_discussion_entries': [{'kind': 'comment', 'id': 10}]}
        self.publisher = Publisher()

    def test_held_feedback_replays_without_changing_qualification_or_attempt(self):
        report = replay_feedback(self.ledger, self.evidence, self.publisher, 'o/r', self.run)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2]['actions'], self.result['actions'][:1])
        job = self.ledger.job(self.job)
        self.assertEqual(job['state'], 'held')
        self.assertEqual(job['attempt'], 1)
        self.assertEqual(job['result']['qualification_limits'], ['Tests missing'])
        self.assertEqual(job['result']['actions'], self.result['actions'])
        self.assertEqual(job['result']['own_discussion_entries'][0]['id'], 10)
        self.assertEqual(report['additional_model_sessions'], 0)

    def test_preview_never_calls_publisher(self):
        report = replay_feedback(self.ledger, self.evidence, None, 'o/r', self.run)
        self.assertEqual(self.calls, [])
        self.assertEqual(report['eligible'], [1])

    def test_stale_evidence_is_not_replayed(self):
        self.ledger.reclassify(self.job, 'stale', self.result)
        replay_feedback(self.ledger, self.evidence, self.publisher, 'o/r', self.run)
        self.assertEqual(self.calls, [])

    def test_corrupt_identity_is_reported_without_publication(self):
        self.result['reviewed_pr_evidence'] = self.evidence.put({**self.pr, 'head_sha': 'x'})
        self.ledger.reclassify(self.job, 'held', self.result)
        report = replay_feedback(self.ledger, self.evidence, self.publisher, 'o/r', self.run)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(report['errors']), 1)

    def test_latest_review_supersedes_old_feedback_across_runs(self):
        snap = self.ledger.save_snapshot({'repo': 'o/r', 'prs': [self.pr], 'new': True})
        run = self.ledger.create_run(snap, {})
        new_job = self.ledger.enqueue(run, self.pr, 'a' * 40)
        self.ledger.claim('worker2', 1)
        self.ledger.finish(new_job, 'worker2', 'reviewed',
                           {**self.result, 'actions': [], 'findings': []})
        replay_feedback(self.ledger, self.evidence, self.publisher, 'o/r')
        self.assertEqual(self.calls, [])

    def test_retained_finding_keeps_exact_prior_comment_after_coverage_followup(self):
        self.ledger.retry(self.job)
        self.ledger.claim('coverage-worker', 1)
        self.ledger.finish(self.job, 'coverage-worker', 'held', {
            **self.result, 'actions': [],
            'findings': [{'description': 'Verified bug', 'novel': False}],
        })
        replay_feedback(self.ledger, self.evidence, self.publisher, 'o/r', self.run)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2]['actions'], self.result['actions'][:1])


if __name__ == '__main__':
    unittest.main()
