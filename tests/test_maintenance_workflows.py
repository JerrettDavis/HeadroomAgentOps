import tempfile
import unittest
from pathlib import Path

from agentops.maintenance_store import MaintenanceStore


class MaintenanceWorkflowTests(unittest.TestCase):
    def test_delayed_workflow_then_unchanged_source_is_approved_once(self):
        from agentops.maintenance_workflows import continue_approvals
        from agentops.model import digest
        with tempfile.TemporaryDirectory() as temp:
            store = MaintenanceStore(Path(temp) / 'ledger', 'o/r')
            context = {'source_repo': 'o/r', 'event': 'pull_request', 'number': 1,
                       'path': '.github/workflows/ci.yml@refs/pull/1/merge', 'head_ref': 'topic', 'base_ref': 'main'}
            row = {'key': 'update', 'number': 1, 'new_head': 'new', 'main_sha': 'main',
                   'head_ref': 'topic', 'head_repo': 'fork/r', 'approval_baseline': [
                {'workflow_id': 10, 'context': context, 'path': '.github/workflows/ci.yml',
                 'manifest_hash': 'manifest', 'source_review': {'manifest_hash': 'manifest', 'verdict': 'clear',
                    'rationale': 'Reviewed workflow and execution dependencies', 'evidence': ['source audit']},
                 'execution_manifest': digest([{'path': '.github/workflows/ci.yml', 'sha': 'source', 'type': 'blob', 'mode': '100644'}])}]}
            class Github:
                repo = 'o/r'
                runs = []
                sent = []
                source = 'source'
                def pull(self, number):
                    return {'head_sha': 'new', 'workflow_manifest_hash': 'manifest',
                            'state': 'open', 'head_ref': 'topic', 'head_repo': 'fork/r', 'base_ref': 'main',
                            'workflow_manifest': [{'path': '.github/workflows/ci.yml'}], 'workflows': self.runs}
                def request(self, method, endpoint, payload=None):
                    if method == 'GET':
                        if '/git/commits/' in endpoint:
                            return {'tree': {'sha': 'tree'}}
                        return {'truncated': False, 'tree': [{'path': '.github/workflows/ci.yml',
                            'sha': self.source, 'type': 'blob', 'mode': '100644'}]}
                    self.sent.append(endpoint)
                    return {'ok': True}
            gh = Github()
            continue_approvals(store, gh, row)
            self.assertFalse(gh.sent)
            gh.runs = [{'id': 20, 'workflow_id': 10, 'path': '.github/workflows/ci.yml',
                        'head_sha': 'new', 'status': 'waiting', 'source_repo': 'o/r', 'event': 'pull_request',
                        'head_branch': 'topic', 'pull_requests': [{'number': 1, 'head': {'sha': 'new', 'ref': 'topic'},
                            'base': {'ref': 'main', 'sha': 'main'}}]}]
            gh.runs[0]['path'] = context['path']
            continue_approvals(store, gh, row)
            continue_approvals(store, gh, row)
            self.assertEqual(gh.sent, ['repos/o/r/actions/runs/20/approve'])
            gh.source = 'changed-local-action'
            gh.runs[0]['id'] = 21
            continue_approvals(store, gh, row)
            self.assertEqual(len(gh.sent), 1)
            row['approval_baseline'][0]['manifest_hash'] = 'changed'
            gh.runs[0]['id'] = 21
            continue_approvals(store, gh, row)
            self.assertEqual(len(gh.sent), 1)

    def test_completed_action_required_is_an_approval_wait_not_a_success(self):
        from agentops.maintenance_workflows import awaiting_approval
        self.assertTrue(awaiting_approval({'status': 'completed', 'conclusion': 'action_required'}))
        self.assertFalse(awaiting_approval({'status': 'completed', 'conclusion': 'success'}))

    def test_ambiguous_or_wrong_event_workflow_cannot_match_pr(self):
        from agentops.maintenance_workflows import run_context
        row = {'number': 1, 'head_ref': 'topic', 'head_repo': 'fork/r', 'main_sha': 'main'}
        run = {'head_sha': 'new', 'head_branch': 'topic', 'source_repo': 'o/r', 'event': 'push',
               'path': '.github/workflows/ci.yml@refs/heads/topic', 'pull_requests': []}
        self.assertIsNone(run_context(run, row, 'new', 'o/r'))
        run['event'] = 'pull_request'
        run['pull_requests'] = [{'number': 2, 'head': {'sha': 'new', 'ref': 'topic'}, 'base': {'ref': 'main'}}]
        self.assertIsNone(run_context(run, row, 'new', 'o/r'))

    def test_mechanical_receipt_only_suppresses_exact_published_heads(self):
        from agentops.maintenance_workflows import mechanical_update
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ledger'
            store = MaintenanceStore(path, 'o/r')
            store.put('update', '1', {'number': 1, 'head_sha': 'old', 'new_head': 'new', 'main_sha': 'main',
                                     'state': 'updated', 'equivalent': True, 'contribution': 'a' * 64,
                                     'receipt': {'new_head': 'new'}})
            self.assertTrue(mechanical_update(path, 'o/r', {'number': 1, 'head_sha': 'new'}, 'old', 'main'))
            self.assertFalse(mechanical_update(path, 'o/r', {'number': 1, 'head_sha': 'author'}, 'old', 'main'))
            self.assertFalse(mechanical_update(path, 'o/r', {'number': 1, 'head_sha': 'new'}, 'old', 'newmain'))
            store.update('update', '1', {'equivalent': False})
            self.assertFalse(mechanical_update(path, 'o/r', {'number': 1, 'head_sha': 'new'}, 'old', 'main'))

    def test_automation_reuses_rebased_review_but_keeps_new_author_evidence(self):
        from test_automation import AutomationTests
        from agentops.evidence import Evidence
        with tempfile.TemporaryDirectory() as temp:
            auto, launcher = AutomationTests().setup_auto(temp)
            evidence = Evidence(Path(temp) / 'evidence')
            previous = {'number': 2, 'head_sha': 'old', 'base_ref': 'main', 'base_sha': 'base',
                        'head_repo': 'o/r', 'updated_at': 'old', 'files': [], 'comments': []}
            proof = {'number': 2, 'head_sha': 'old', 'main_sha': 'base',
                     'reviewed_pr_evidence': evidence.put(previous), 'actions': []}
            prior = [{'id': 'analysis', 'head_sha': 'old', 'main_sha': 'base', 'state': 'reviewed',
                      'result': {'evidence_record': evidence.put(proof)}}]
            store = MaintenanceStore(Path(temp) / 'ledger.sqlite3', auto.data['repo'])
            store.put('update', '2', {'number': 2, 'head_sha': 'old', 'new_head': 'new', 'main_sha': 'main',
                                     'state': 'updated', 'equivalent': True, 'contribution': 'b' * 64,
                                     'receipt': {'new_head': 'new'}})
            current = {**previous, 'head_sha': 'new', 'updated_at': 'new'}
            launcher.github.pull.return_value = current
            self.assertFalse(auto.material_update(current, prior, 'main')['material'])
            proof['actions'] = [{'kind': 'review', 'event': 'APPROVE'}]
            prior[0]['result']['evidence_record'] = evidence.put(proof)
            self.assertFalse(auto.material_update(current, prior, 'main')['material'])
            launcher.github.pull.return_value = {**current, 'comments': [{'id': 9, 'body': 'New concern'}]}
            self.assertTrue(auto.material_update(current, prior, 'main')['material'])

    def test_linked_mechanical_receipts_span_multiple_main_revisions(self):
        from agentops.maintenance_workflows import mechanical_update
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ledger'
            store = MaintenanceStore(path, 'o/r')
            for old, new, main in [('original', 'first', 'main1'), ('first', 'second', 'main2')]:
                store.put('update', new, {'number': 1, 'head_sha': old, 'new_head': new, 'main_sha': main,
                    'state': 'updated', 'equivalent': True, 'contribution': 'a' * 64, 'receipt': {'new_head': new}})
            self.assertTrue(mechanical_update(path, 'o/r', {'number': 1, 'head_sha': 'second'}, 'original', 'main2'))
            self.assertFalse(mechanical_update(path, 'o/r', {'number': 1, 'head_sha': 'second'}, 'original', 'main3'))

    def test_publication_receipt_race_does_not_start_extra_review(self):
        from agentops.maintenance_workflows import mechanical_update
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'ledger'
            store = MaintenanceStore(path, 'o/r')
            store.put('update', '1', {'number': 1, 'head_sha': 'old', 'new_head': 'new', 'main_sha': 'main',
                                     'state': 'publishing', 'equivalent': True, 'contribution': 'a' * 64,
                                     'prepared': {'new_head': 'new', 'contribution': 'a' * 64}})
            self.assertTrue(mechanical_update(path, 'o/r', {'number': 1, 'head_sha': 'new'}, 'old', 'main'))
