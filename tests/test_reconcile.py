import tempfile
import unittest
from pathlib import Path

from agentops.evidence import Evidence
from agentops.ledger import Ledger
from agentops.reconcile import reconcile_jobs


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db, self.evidence = Ledger(root / 'ledger.db'), Evidence(root / 'evidence')
        self.pr = {'number': 1, 'head_sha': 'b' * 40, 'main_sha': 'a' * 40, 'base_ref': 'main',
            'head_ref': 'fix', 'head_repo': 'o/r', 'state': 'open', 'draft': False,
            'mergeable': 'MERGEABLE', 'discussion_hash': 'd', 'checks_hash': 'now-green',
            'files': [{'filename': 'a.py'}], 'threads': [], 'checks': [], 'statuses': [],
            'workflow_manifest_hash': 'manifest', 'expected_workflows_verified': True,
            'expected_workflows': ['.github/workflows/ci.yml'],
            'workflows': [{'id': 1, 'head_sha': 'b' * 40, 'status': 'completed', 'conclusion': 'success',
                           'path': '.github/workflows/ci.yml'}]}
        snapshot = self.db.save_snapshot({'repo': 'o/r', 'main_sha': 'a' * 40, 'prs': [self.pr]})
        self.run = self.db.create_run(snapshot, {})
        self.job = self.db.enqueue(self.run, self.pr, 'a' * 40)
        self.db.claim('previous-worker', 1)
        result = {'outcome': 'ready', 'head_sha': 'b' * 40, 'main_sha': 'a' * 40,
                  'reviewed_base_ref': 'main', 'reviewed_head_ref': 'fix', 'reviewed_head_repo': 'o/r',
                  'reviewed_discussion_hash': 'd', 'reviewed_checks_hash': 'pending',
                  'reviewed_notice_ids': [], 'coverage': {'files': ['a.py'], 'threads': []},
                  'findings': [], 'actions': [], 'evidence': [{'kind': 'test', 'status': 'passed'}],
                  'source_review': {'manifest_hash': 'manifest', 'verdict': 'clear',
                                    'rationale': 'Reviewed source', 'evidence': ['source:ci.yml']}}
        self.db.finish(self.job, 'previous-worker', 'awaiting_ci', result)
        current = self.pr
        class GitHub:
            repo = 'o/r'
            def pull(self, number):
                return current
            def main_sha(self):
                return 'a' * 40
        self.github = GitHub()

    def test_green_ci_refresh_does_not_launch_or_count_a_new_reasoning_session(self):
        report = reconcile_jobs(self.db, self.github, self.evidence, self.run)
        self.assertEqual(self.db.job(self.job)['state'], 'ready')
        self.assertEqual(self.db.job(self.job)['attempt'], 1)
        self.assertEqual(report['ready'], [1])

    def test_reconciliation_retains_causal_publication_receipts(self):
        entries = [{'kind': 'comment', 'id': 123, 'body_hash': 'a' * 64}]
        class Publisher:
            def publish(self, *args):
                return {'sent': [], 'held': [], 'own_discussion_entries': entries}
        reconcile_jobs(self.db, self.github, self.evidence, self.run, publisher=Publisher())
        self.assertEqual(self.db.job(self.job)['result']['own_discussion_entries'], entries)

    def test_new_author_discussion_requires_a_fresh_review_not_auto_approval(self):
        self.pr['discussion_hash'] = 'new-author-evidence'
        report = reconcile_jobs(self.db, self.github, self.evidence, self.run, retry_changed=True)
        self.assertEqual(self.db.job(self.job)['state'], 'queued')
        self.assertEqual(report['queued'], [1])
        self.assertEqual(self.db.job(self.job)['attempt'], 1)

    def test_unchanged_held_work_is_not_retried_without_new_evidence(self):
        self.pr['checks_hash'] = 'pending'
        self.pr['workflows'][0]['status'] = 'in_progress'
        self.pr['workflows'][0]['conclusion'] = None
        report = reconcile_jobs(self.db, self.github, self.evidence, self.run, retry_changed=True)
        self.assertEqual(report['queued'], [])
        self.assertEqual(self.db.job(self.job)['state'], 'awaiting_ci')

    def test_pending_progress_does_not_start_a_new_reasoning_session(self):
        self.pr['checks_hash'] = 'different-progress-metadata'
        self.pr['workflows'][0]['status'] = 'in_progress'
        self.pr['workflows'][0]['conclusion'] = None
        report = reconcile_jobs(self.db, self.github, self.evidence, self.run, retry_changed=True)
        self.assertEqual(report['queued'], [])
        self.assertEqual(self.db.job(self.job)['state'], 'awaiting_ci')


if __name__ == '__main__':
    unittest.main()
