import copy
import tempfile
import threading
import unittest
from pathlib import Path

from agentops.stack_service import StackService
from test_stacking import inventory


class FakeGitHub:
    repo = 'owner/repo'
    owner = 'owner'
    name = 'repo'

    def __init__(self):
        self.data = inventory()
        self.refs, self.prs, self.writes = [], [], []
        self.fail_create = False

    def authenticated_user(self):
        return {'login': 'human'}

    def pages(self, endpoint):
        return self.prs

    def request(self, method, endpoint, payload=None):
        if method == 'GET':
            if '/git/matching-refs/' in endpoint:
                return self.refs
            if endpoint == 'repos/owner/repo':
                return {'permissions': {'push': True}}
            if '/pulls/' in endpoint:
                n = int(endpoint.rsplit('/', 1)[1])
                return next((p for p in self.prs if p['number'] == n),
                            {'number': n, 'state': 'open', 'merged': False})
            raise AssertionError(endpoint)
        self.writes.append((method, endpoint, payload))
        if method != 'POST' or endpoint != 'repos/owner/repo/pulls':
            raise AssertionError('Forbidden external write: ' + endpoint)
        pr = {'number': 90, 'html_url': 'https://github.com/owner/repo/pull/90',
              'body': payload['body'], 'draft': True, 'state': 'open', 'merged': False,
              'head': {'sha': 'd' * 40, 'ref': payload['head'], 'repo': {'full_name': 'owner/repo'}},
              'base': {'ref': 'main', 'repo': {'full_name': 'owner/repo'}}}
        self.prs.append(pr)
        if self.fail_create:
            self.fail_create = False
            raise RuntimeError('Response lost after creating PR')
        return pr


class Engine:
    def __init__(self, github):
        self.github, self.prepares, self.pushes = github, 0, 0
        self.gate = None

    def prepare(self, record, progress):
        self.prepares += 1
        progress({'phase': 'Integrating #1'})
        if self.gate:
            self.gate.wait(5)
        return {'state': 'prepared', 'revision': 'd' * 40, 'checkout': '/private/checkout',
                'validation': {'state': 'unconfigured', 'commands': []}}

    def publish_branch(self, record):
        self.pushes += 1
        branch = 'agentops/stacks/' + record['id']
        self.github.refs = [{'ref': 'refs/heads/' + branch, 'object': {'sha': record['revision']}}]
        return branch


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.github = FakeGitHub()
        self.engine = Engine(self.github)
        self.path = Path(self.tmp.name) / 'ledger.sqlite3'
        self.config = {'repo': 'owner/repo', 'publish': True, 'image': 'test'}
        self.collect = lambda: copy.deepcopy(self.github.data)
        self.service = StackService(self.path, self.config, self.github,
                                    collect=self.collect, engine=self.engine)
        self.addCleanup(self.service.close)

    def prepared(self, numbers=None):
        record = self.service.prepare({'numbers': numbers or [1, 2]})
        self.service.close()
        return self.service.store.get(record['id'])

    def test_preparation_never_writes_and_same_inputs_reuse(self):
        record = self.prepared()
        self.assertEqual(record['state'], 'prepared')
        self.assertEqual(self.github.writes, [])
        self.assertEqual(self.engine.pushes, 0)
        again = self.service.prepare({'numbers': [2, 1]})
        self.assertEqual(again['id'], record['id'])
        self.assertEqual(self.engine.prepares, 1)
        public = self.service.status()['stacks'][0]
        self.assertNotIn('checkout', public)
        self.assertNotIn('owner', public)

    def test_bounded_work_and_blocked_selection(self):
        self.engine.gate = threading.Event()
        self.addCleanup(self.engine.gate.set)
        record = self.service.prepare({'numbers': [1]})
        with self.assertRaisesRegex(ValueError, 'operation'):
            self.service.prepare({'numbers': [2]})
        self.engine.gate.set()
        self.service.close()
        self.github.data['prs'][0]['ci'] = 'pending'
        blocked = self.service.prepare({'numbers': [3]})
        self.assertEqual(blocked['state'], 'blocked')
        self.assertEqual(self.engine.prepares, 1)

    def test_human_publication_is_draft_and_idempotent(self):
        record = self.prepared()
        published = self.service.publish({'id': record['id']})
        self.assertEqual(published['state'], 'published')
        self.assertEqual(self.engine.pushes, 1)
        self.assertTrue(self.github.writes[0][2]['draft'])
        self.assertIn('#1', self.github.writes[0][2]['body'])
        self.assertEqual(self.service.publish({'id': record['id']})['pr_number'], 90)
        self.assertEqual(len(self.github.writes), 1)

    def test_stale_revisions_and_gates_reject_before_writes(self):
        for change in ['head', 'target', 'approval', 'ci', 'offline']:
            with self.subTest(change=change):
                self.github.data = inventory()
                record = self.prepared()
                if change == 'head':
                    self.github.data['prs'][2]['head_sha'] = 'b' * 40
                elif change == 'target':
                    self.github.data['default_sha'] = 'b' * 40
                elif change == 'approval':
                    self.github.data['prs'][2]['review_decision'] = 'REVIEW_REQUIRED'
                elif change == 'ci':
                    self.github.data['prs'][2]['ci'] = 'pending'
                else:
                    def offline():
                        raise RuntimeError('offline')
                    self.service.collect = offline
                with self.assertRaises((ValueError, RuntimeError)):
                    self.service.publish({'id': record['id']})
                self.assertEqual(self.engine.pushes, 0)
                self.assertEqual(self.github.writes, [])
                self.service.collect = self.collect

    def test_ambiguous_create_reconciles_without_duplicate_pr_or_push(self):
        record = self.prepared()
        self.github.fail_create = True
        with self.assertRaises(RuntimeError):
            self.service.publish({'id': record['id']})
        self.assertEqual(self.service.store.get(record['id'])['state'], 'publication_unknown')
        self.assertEqual(self.service.publish({'id': record['id']})['pr_number'], 90)
        self.assertEqual(len(self.github.writes), 1)
        self.assertEqual(self.engine.pushes, 1)

    def test_uncertain_publication_recheck_recovers_even_after_target_moves(self):
        record = self.prepared()
        self.github.fail_create = True
        with self.assertRaises(RuntimeError):
            self.service.publish({'id': record['id']})
        self.github.data['default_sha'] = 'e' * 40
        self.assertEqual(self.service.refresh({'id': record['id']})['pr_number'], 90)
        self.assertEqual(len(self.github.writes), 1)

    def test_dead_preparation_owner_is_recoverable_but_live_owner_is_not_stolen(self):
        from unittest.mock import patch
        record = self.prepared()
        self.service.store.update(record['id'], {'state': 'preparing', 'owner': 'dead owner'})
        with patch('agentops.stack_service.owner_state', return_value='missing'):
            self.service.recover()
        self.assertEqual(self.service.store.get(record['id'])['state'], 'interrupted')
        self.service.store.update(record['id'], {'state': 'publishing', 'owner': 'live owner'})
        with patch('agentops.stack_service.owner_state', return_value='unknown'):
            self.service.recover()
        self.assertEqual(self.service.store.get(record['id'])['state'], 'publishing')

    def test_unrelated_branch_and_disabled_policy_block_publication(self):
        record = self.prepared()
        self.github.refs = [{'ref': 'refs/heads/agentops/stacks/' + record['id'], 'object': {'sha': 'e' * 40}}]
        with self.assertRaises(ValueError):
            self.service.publish({'id': record['id']})
        self.assertEqual(self.engine.pushes, 0)
        self.config['publish'] = False
        self.service.config['publish'] = False
        with self.assertRaises(ValueError):
            self.service.publish({'id': record['id']})

    def test_refresh_reports_aggregate_and_source_cleanup(self):
        record = self.prepared()
        self.service.publish({'id': record['id']})
        self.github.prs[0].update(state='closed', merged=True)
        refreshed = self.service.refresh({'id': record['id']})
        self.assertTrue(refreshed['aggregate']['merged'])
        self.assertEqual(refreshed['source_cleanup'], [1, 2])
        self.assertEqual(len(self.github.writes), 1)


if __name__ == '__main__':
    unittest.main()
