import tempfile
import unittest
from pathlib import Path

from agentops.evidence import Evidence
from agentops.ledger import Ledger
from agentops.publisher import Publisher
from agentops.settlement import settle_reviews
from test_publisher import FakeGitHub, pr, result, MAIN


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.ledger, self.evidence = Ledger(root / 'ledger.db'), Evidence(root / 'evidence')
        self.current = {**pr(), 'updated_at': 'now', 'review_decision':'REVIEW_REQUIRED'}
        self.snapshot = {'repo': 'o/r', 'complete': True, 'main_sha': MAIN, 'prs': [self.current]}
        identity = self.ledger.save_snapshot(self.snapshot)
        run = self.ledger.create_run(identity, {})
        self.job = self.ledger.enqueue(run, self.current, MAIN)
        self.ledger.claim('worker', 1)
        self.saved = {**result(), 'outcome': 'held', 'publication_qualification': 'held',
                      'qualification_limits': ['Runtime qualification pending'],
                      'reviewed_pr_evidence': self.evidence.put(self.current)}
        self.ledger.finish(self.job, 'worker', 'held', self.saved)
        class GitHub(FakeGitHub):
            def request(self, method, endpoint, payload=None):
                if method == 'GET' and endpoint == 'repos/o/r/pulls/1':
                    return {'head': {'sha': self.current['head_sha']}, 'updated_at': self.current['updated_at']}
                return super().request(method, endpoint, payload)
        self.github = GitHub()
        self.github.current = self.current
        self.publisher = Publisher(self.github, self.ledger)

    def settle(self):
        return settle_reviews(self.ledger, self.github, self.evidence, self.snapshot,
                              {'repo': 'o/r', 'publish': True}, self.publisher)

    def test_comment_does_not_complete_a_required_review(self):
        report = self.settle()
        self.assertEqual(report['pending'][0]['reasons'], ['Approval qualification incomplete'])
        self.assertEqual(report['determined'], [])
        self.assertEqual(report['commented'][0]['number'], 1)
        self.assertEqual(self.github.posts[0]['event'], 'COMMENT')
        self.assertEqual(len(self.ledger.jobs(report['run_id'])), 1)
        self.assertEqual(self.ledger.jobs(report['run_id'])[0]['result']['review_task'], 'qualification')
        self.assertEqual(self.ledger.job(self.job)['state'], 'held')

    def test_own_pr_is_reported_as_requiring_an_independent_identity(self):
        self.current['author'] = 'maintainer'
        self.ledger.reclassify(self.job, 'held', {**self.saved,
            'reviewed_pr_evidence':self.evidence.put(self.current)})
        report = self.settle()
        self.assertEqual(report['pending'], [])
        self.assertEqual(report['independent_review_required'][0]['number'],1)

    def test_excluded_author_is_not_even_fetched_or_enqueued(self):
        from unittest.mock import patch
        with patch.object(self.github,'pull') as fetch:
            report=settle_reviews(self.ledger,self.github,self.evidence,self.snapshot,
                {'repo':'o/r','exclude_authors':['author']},self.publisher)
        fetch.assert_not_called()
        self.assertEqual(report['pending'],[])
        self.assertEqual(report['independent_review_required'][0]['number'],1)

    def test_new_and_changed_heads_are_the_only_jobs_enqueued(self):
        self.snapshot['prs'] = [{**self.current, 'head_sha': 'c'*40}, {**self.current, 'number': 2}]
        report = self.settle()
        self.assertEqual({j['number'] for j in self.ledger.jobs(report['run_id'])}, {1, 2})
        self.assertEqual(self.github.posts, [])

    def test_changed_author_discussion_requires_reasoning_before_publication(self):
        self.github.current = {**self.current, 'discussion_hash': 'changed'}
        report = self.settle()
        self.assertEqual(report['pending'][0]['number'], 1)
        self.assertEqual(self.github.posts, [])


if __name__ == '__main__':
    unittest.main()
