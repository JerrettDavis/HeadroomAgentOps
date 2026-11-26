import tempfile
import time
import unittest
from pathlib import Path


class Github:
    repo = 'o/r'
    sha = 'a' * 40

    def __init__(self):
        self.runs = []
        self.sent = []

    def main_sha(self):
        return self.sha

    def pages(self, endpoint, key=None):
        if '/actions/runs?' in endpoint:
            return [{'event': 'push', 'repository': {'full_name': self.repo}, **r} for r in self.runs]
        if '/pulls?' in endpoint:
            return []
        return []

    def request(self, method, endpoint, payload=None):
        if method == 'POST':
            self.sent.append((endpoint, payload))
            return {'number': 99, 'html_url': 'https://github.com/o/r/pull/99'}
        if '/pulls/' in endpoint:
            number = int(endpoint.rsplit('/', 1)[-1])
            return {'state': 'open', 'number': number, 'head': {'sha': 'b' * 40, 'ref': f'p{number}',
                    'repo': {'full_name': 'o/r'}}, 'base': {'ref': 'main', 'repo': {'full_name': 'o/r'}}}
        if '/jobs' in endpoint:
            return {'jobs': []}
        raise ValueError(endpoint)


class Engine:
    def __init__(self):
        self.published = []

    def prepare(self, record):
        if record['number'] == 2:
            raise ValueError('Rebase conflict')
        return {'new_head': 'c' * 40, 'checkout': '/fixture', 'equivalent': True,
                'base_sha': 'd' * 40, 'contribution': 'proof'}

    def publish(self, record):
        self.published.append(record['number'])
        return {'new_head': record['new_head']}


class Agent:
    def __init__(self):
        self.calls = []

    def execute(self, record, save):
        self.calls.append(record)
        save({'phase': 'validated', 'candidate': 'e' * 40})
        return {'pr': {'number': 99, 'html_url': 'https://github.com/o/r/pull/99'},
                'summary': 'Fixed', 'validation': ['test passed']}


class MaintenanceServiceTests(unittest.TestCase):
    def setUp(self):
        from agentops.maintenance_service import MaintenanceService
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.github, self.engine, self.agent = Github(), Engine(), Agent()
        self.service = MaintenanceService(Path(self.tmp.name) / 'ledger.sqlite',
                                          {'repo': 'o/r', 'harness': 'codex'}, self.github,
                                          engine=self.engine, agent=self.agent)
        self.addCleanup(self.service.close)

    def inventory(self):
        rows = [{'number': n, 'repo': 'o/r', 'title': str(n), 'head_sha': 'b' * 40,
                 'main_sha': self.github.sha, 'head_repo': 'o/r', 'head_ref': f'p{n}', 'behind': 1}
                for n in (1, 2)]
        self.service.store.put('inventory', 'current', {'prs': rows, 'main_sha': self.github.sha, 'fetched': time.time()})

    def fail_main(self):
        self.github.runs = [{'id': 1, 'workflow_id': 10, 'head_sha': self.github.sha,
                             'head_branch': 'main', 'status': 'completed', 'conclusion': 'failure',
                             'html_url': 'https://github.com/o/r/actions/runs/1'}]

    def test_partial_batch_is_durable_and_no_duplicate_publish(self):
        self.inventory()
        self.service.update_selected({'numbers': [1, 2], 'reapprove': False})
        self.service.update_selected({'numbers': [1, 2], 'reapprove': False})
        self.service.process_pending()
        rows = self.service.status()['updates']
        self.assertEqual({row['state'] for row in rows}, {'updated', 'needs_remediation'})
        self.assertEqual(self.engine.published, [1])
        self.assertEqual(len(self.service.status()['secondary']), 1)

    def test_conflicts_only_run_agent_after_explicit_selection(self):
        self.inventory()
        self.service.update_selected({'numbers': [2], 'reapprove': False})
        self.service.process_pending()
        self.assertFalse(self.agent.calls)
        key = self.service.status()['secondary'][0]['key']
        self.service.remediate({'keys': [key]})
        self.service.process_pending()
        self.assertEqual(len(self.agent.calls), 1)
        self.assertEqual(self.service.status()['updates'][0]['state'], 'remediated')

    def test_alert_then_autofix_deduplicates_repairs_and_keeps_pr_link(self):
        self.fail_main()
        self.service.tick()
        self.assertTrue(self.service.status()['alert'])
        self.assertFalse(self.agent.calls)
        self.service.configure({'autofix': True})
        self.service.tick()
        self.service.process_pending()
        self.service.tick()
        self.service.process_pending()
        self.assertEqual(len(self.agent.calls), 1)
        self.assertFalse(self.service.status()['alert'])
        self.assertEqual(self.service.status()['incidents'][0]['pr']['number'], 99)

    def test_pending_new_head_does_not_resolve_previous_failure(self):
        self.fail_main()
        self.service.tick()
        self.github.sha = 'f' * 40
        self.github.runs = []
        self.service.tick()
        self.assertTrue(self.service.status()['alert'])
        self.github.runs = [{'id': 2, 'workflow_id': 10, 'head_sha': self.github.sha,
                             'head_branch': 'main', 'status': 'completed', 'conclusion': 'success'}]
        self.service.tick()
        self.assertFalse(self.service.status()['alert'])
        self.assertEqual(self.service.status()['incidents'][0]['state'], 'resolved')

    def test_unknown_inventory_cannot_be_submitted(self):
        self.service.store.put('inventory', 'current', {'prs': [{'number': 1, 'behind': None}]})
        with self.assertRaises(ValueError):
            self.service.update_selected({'numbers': [1]})

    def test_incident_exposes_queued_and_running_repair_state(self):
        self.fail_main()
        self.service.tick()
        self.service.fix_main({})
        self.assertEqual(self.service.status()['incidents'][0]['repair_state'], 'agent_queued')

    def test_failed_rerun_reopens_resolved_incident(self):
        self.fail_main()
        self.service.tick()
        self.github.runs[0]['conclusion'] = 'success'
        self.service.tick()
        self.assertFalse(self.service.status()['alert'])
        self.github.runs[0]['conclusion'] = 'failure'
        self.github.runs[0]['run_attempt'] = 2
        self.service.tick()
        self.assertTrue(self.service.status()['alert'])

    def test_api_failure_keeps_prior_incident_and_marks_status_stale(self):
        self.fail_main()
        self.service.tick()
        self.github.main_sha = lambda: (_ for _ in ()).throw(RuntimeError('offline'))
        self.service.tick()
        self.assertTrue(self.service.status()['alert'])
        self.assertEqual(self.service.status()['monitor']['error'], 'offline')

    def test_publication_disabled_blocks_mechanical_and_agent_mutations(self):
        self.inventory()
        self.service.config['publish'] = False
        with self.assertRaisesRegex(ValueError, 'Publication'):
            self.service.update_selected({'numbers': [1]})
        self.fail_main()
        self.service.tick()
        with self.assertRaisesRegex(ValueError, 'Publication'):
            self.service.fix_main({})

    def test_recovery_routes_agent_candidates_back_to_agent(self):
        from unittest.mock import patch
        row = {'number': 1, 'repo': 'o/r', 'state': 'agent_running', 'task': 'rebase',
               'head_sha': 'b' * 40, 'main_sha': self.github.sha, 'owner': 'dead',
               'prepared': {'new_head': 'c' * 40}, 'handle': {'container': 'retained'}}
        self.service.store.put('update', 'candidate', row)
        with patch('agentops.maintenance_service.owner_state', return_value='missing'):
            self.service.recover()
        self.service.process_pending()
        self.assertEqual(len(self.agent.calls), 1)
        self.assertFalse(self.engine.published)
        self.assertFalse(self.service.store.get('update', 'candidate')['equivalent'])

    def test_missing_workflow_coverage_never_resolves_incident(self):
        self.fail_main()
        self.github.runs.append({**self.github.runs[0], 'id': 2, 'workflow_id': 11})
        self.service.tick()
        self.github.runs = [self.github.runs[1]]
        self.service.tick()
        self.github.sha = 'f' * 40
        self.github.runs = [{**self.github.runs[0], 'head_sha': self.github.sha, 'conclusion': 'success'}]
        self.service.tick()
        self.assertTrue(self.service.status()['alert'])

    def test_approval_needed_alert_never_launches_code_fixer(self):
        self.fail_main()
        self.github.runs[0]['conclusion'] = 'action_required'
        self.service.tick()
        self.assertTrue(self.service.status()['alert'])
        with self.assertRaisesRegex(ValueError, 'approval'):
            self.service.fix_main({})
        self.assertFalse(self.agent.calls)

    def test_stale_inventory_cannot_authorize_batch(self):
        self.inventory()
        self.service.store.update('inventory', 'current', {'fetched': time.time() - 301})
        with self.assertRaisesRegex(ValueError, 'fresh'):
            self.service.update_selected({'numbers': [1]})

    def test_successful_rerun_before_agent_start_prevents_unnecessary_execution(self):
        self.fail_main()
        self.service.tick()
        self.service.fix_main({})
        self.github.runs[0]['conclusion'] = 'success'
        self.service.process_pending()
        self.assertFalse(self.agent.calls)

    def test_source_authorization_is_bound_to_current_held_candidate(self):
        self.service.store.put('repair', 'source', {'task': 'main', 'state': 'source_held',
            'candidate': 'b' * 40, 'source_manifest': 'c' * 64, 'main_sha': self.github.sha})
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.service.authorize_source({'key': 'source', 'candidate': 'other', 'source_manifest': 'c' * 64})
        self.service.authorize_source({'key': 'source', 'candidate': 'b' * 40, 'source_manifest': 'c' * 64})
        row = self.service.store.get('repair', 'source')
        self.assertEqual(row['state'], 'agent_queued')
        self.assertEqual(row['source_authorization']['candidate'], 'b' * 40)


if __name__ == '__main__':
    unittest.main()
