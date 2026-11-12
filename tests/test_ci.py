import unittest

from agentops.ci import CIHandler


class FakeGitHub:
    repo = 'o/r'
    def request(self, method, path, payload=None):
        return {'accepted': path}


class CITests(unittest.TestCase):
    def test_ambiguous_rerun_is_reconciled_only_after_attempt_advances(self):
        handler = CIHandler(FakeGitHub())
        job = {'head_sha': 'b' * 40}
        action = {'workflow_id': 7, 'event': 'RERUN'}
        current = {'workflows': [{'id': 7, 'head_sha': 'b' * 40, 'run_attempt': 1}]}
        self.assertIsNone(handler.reconcile(job, current, action, {'run_attempt': 1}))
        current['workflows'][0]['run_attempt'] = 2
        self.assertEqual(handler.reconcile(job, current, action, {'run_attempt': 1})['run_attempt'], 2)

    def test_ci_action_is_bound_to_actual_exact_head_run_and_source(self):
        current = {'head_sha': 'b' * 40, 'workflows': [
            {'id': 7, 'head_sha': 'b' * 40, 'status': 'completed', 'path': '.github/workflows/ci.yml'}],
            'workflow_manifest': [{'path': '.github/workflows/ci.yml'}]}
        action = {'workflow_id': 7, 'event': 'RERUN'}
        self.assertEqual(CIHandler(FakeGitHub())({'repo': 'o/r', 'head_sha': 'b' * 40}, current, action),
                         {'accepted': 'repos/o/r/actions/runs/7/rerun'})
        with self.assertRaises(ValueError):
            CIHandler(FakeGitHub())({'repo': 'o/r', 'head_sha': 'b' * 40}, current, {**action, 'workflow_id': 8})
        with self.assertRaises(ValueError):
            CIHandler(FakeGitHub())({'repo': 'o/r', 'head_sha': 'c' * 40}, current, action)

    def test_fork_workflow_target_is_not_assumed_to_be_upstream(self):
        current = {'head_sha': 'b' * 40, 'workflows': [
            {'id': 7, 'head_sha': 'b' * 40, 'source_repo': 'fork/r', 'status': 'completed',
             'path': '.github/workflows/ci.yml'}],
            'workflow_manifest': [{'path': '.github/workflows/ci.yml'}]}
        reply = CIHandler(FakeGitHub())({'repo': 'o/r', 'head_sha': 'b' * 40}, current,
                                       {'workflow_id': 7, 'event': 'RERUN'})
        self.assertEqual(reply['accepted'], 'repos/fork/r/actions/runs/7/rerun')


if __name__ == '__main__':
    unittest.main()
