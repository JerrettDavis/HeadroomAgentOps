import tempfile
import unittest
from pathlib import Path

from agentops.maintenance_store import MaintenanceStore


class MaintenanceWorkflowTests(unittest.TestCase):
    def test_delayed_workflow_then_unchanged_source_is_approved_once(self):
        from agentops.maintenance_workflows import continue_approvals
        with tempfile.TemporaryDirectory() as temp:
            store = MaintenanceStore(Path(temp) / 'ledger', 'o/r')
            row = {'key': 'update', 'number': 1, 'new_head': 'new', 'approval_baseline': [
                {'workflow_id': 10, 'path': '.github/workflows/ci.yml', 'manifest_hash': 'manifest'}]}
            class Github:
                repo = 'o/r'
                runs = []
                sent = []
                def pull(self, number):
                    return {'head_sha': 'new', 'workflow_manifest_hash': 'manifest',
                            'workflow_manifest': [{'path': '.github/workflows/ci.yml'}], 'workflows': self.runs}
                def request(self, method, endpoint, payload=None):
                    self.sent.append(endpoint)
                    return {'ok': True}
            gh = Github()
            continue_approvals(store, gh, row)
            self.assertFalse(gh.sent)
            gh.runs = [{'id': 20, 'workflow_id': 10, 'path': '.github/workflows/ci.yml',
                        'head_sha': 'new', 'status': 'waiting'}]
            continue_approvals(store, gh, row)
            continue_approvals(store, gh, row)
            self.assertEqual(gh.sent, ['repos/o/r/actions/runs/20/approve'])
            row['approval_baseline'][0]['manifest_hash'] = 'changed'
            gh.runs[0]['id'] = 21
            continue_approvals(store, gh, row)
            self.assertEqual(len(gh.sent), 1)

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
