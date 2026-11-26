import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class MaintenanceAgentTests(unittest.TestCase):
    def test_missing_validation_never_publishes(self):
        from agentops.maintenance_agent import validate_repair
        with self.assertRaisesRegex(ValueError, 'passing'):
            validate_repair({'outcome': 'ready', 'summary': 'done', 'evidence': []})
        with self.assertRaisesRegex(ValueError, 'failed'):
            validate_repair({'outcome': 'ready', 'summary': 'done', 'evidence': [
                {'kind': 'test', 'status': 'passed'}, {'kind': 'test', 'status': 'failed'}]})

    def test_passing_tests_and_ready_are_required(self):
        from agentops.maintenance_agent import validate_repair
        result = {'outcome': 'ready', 'summary': 'diagnosis', 'evidence': [
                  {'kind': 'test', 'status': 'passed', 'command': 'python -m unittest'}]}
        self.assertEqual(validate_repair(result), ['python -m unittest'])

    def test_empty_test_command_is_not_validation(self):
        from agentops.maintenance_agent import validate_repair
        with self.assertRaisesRegex(ValueError, 'command'):
            validate_repair({'outcome': 'ready', 'evidence': [{'kind': 'test', 'status': 'passed', 'command': ''}]})

    def test_existing_pr_reconciles_without_launching_agent(self):
        from agentops.maintenance_agent import MaintenanceAgent
        with tempfile.TemporaryDirectory() as temp:
            class Github:
                repo = 'o/r'
                def pages(self, endpoint, key=None):
                    return [{'number': 9, 'html_url': 'https://github.com/o/r/pull/9', 'state': 'open',
                        'body': '<!-- agentops-main-ci:' + 'a' * 40 + ' -->',
                        'head': {'ref': 'agentops/main-ci-' + 'a' * 16, 'sha': 'b' * 40,
                                 'repo': {'full_name': 'o/r'}}, 'base': {'ref': 'main'}}]
            agent = MaintenanceAgent(Path(temp) / 'ledger', {'repo': 'o/r', 'harness': 'codex'}, Github())
            with patch.object(agent, '_start', side_effect=AssertionError('must not restart')):
                result = agent.execute({'task': 'main', 'main_sha': 'a' * 40, 'candidate': 'b' * 40}, lambda _: None)
            self.assertEqual(result['pr']['number'], 9)

    def test_unrelated_pr_on_deterministic_branch_is_never_claimed(self):
        from agentops.maintenance_agent import MaintenanceAgent
        with tempfile.TemporaryDirectory() as temp:
            class Github:
                repo = 'o/r'
                def pages(self, endpoint, key=None):
                    return [{'number': 9, 'state': 'open', 'body': 'Unrelated PR',
                             'head': {'sha': 'b' * 40}, 'base': {'ref': 'other'}}]
            agent = MaintenanceAgent(Path(temp) / 'ledger', {'repo': 'o/r'}, Github())
            with self.assertRaisesRegex(ValueError, 'identity'):
                agent.execute({'task': 'main', 'main_sha': 'a' * 40, 'candidate': 'b' * 40}, lambda _: None)

    def test_explicit_retry_starts_new_attempt_only_after_missing_container(self):
        import uuid
        from agentops.maintenance_agent import MaintenanceAgent
        from agentops.worker import Worker
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker = Worker(root / 'workers', 'o/r')
            worker.observe = lambda _: {'state': 'missing'}
            session = str(uuid.uuid4())
            directory = worker.root / session
            (directory / 'auth').mkdir(parents=True)
            (directory / 'auth' / 'model-env.json').write_text('{}')
            agent = MaintenanceAgent(root / 'ledger', {'repo': 'o/r'}, object(), worker=worker)
            row = {'task': 'rebase', 'main_sha': 'a' * 40, 'retry_requested': True,
                   'handle': {'session_id': session, 'directory': str(directory), 'container': 'missing'}}
            saved = {}
            with patch.object(agent, '_start', side_effect=ValueError('new attempt started')):
                with self.assertRaisesRegex(ValueError, 'new attempt started'):
                    agent.execute(row, saved.update)
            self.assertEqual(len(saved['attempts']), 1)
            self.assertFalse(list((directory / 'auth').iterdir()))
            worker.observe = lambda _: {'state': 'exited', 'exit_code': 0}
            with patch.object(agent, '_start', side_effect=ValueError('new attempt started')):
                with self.assertRaisesRegex(ValueError, 'new attempt started'):
                    agent.execute(row, saved.update)

    def test_protected_source_changes_require_candidate_bound_provenance(self):
        from agentops.maintenance_agent import protected_source_gate
        with self.assertRaisesRegex(ValueError, 'source provenance'):
            protected_source_gate(['.github/workflows/ci.yml'], 'manifest', {})
        with self.assertRaisesRegex(ValueError, 'source provenance'):
            protected_source_gate(['.gitmodules'], 'manifest', {'source_review': {'verdict': 'clear',
                'manifest_hash': 'other', 'rationale': 'checked', 'evidence': ['audit']}})
        protected_source_gate(['.github/workflows/ci.yml'], 'manifest', {'source_review': {'verdict': 'clear',
            'manifest_hash': 'manifest', 'rationale': 'checked candidate sources', 'evidence': ['audit']}})

    def test_protected_publication_requires_exact_main_authorization_and_never_overrides_contributor_gate(self):
        from agentops.maintenance_agent import protected_publication_gate
        with self.assertRaisesRegex(ValueError, 'maintainer'):
            protected_publication_gate('main', 'candidate', 'manifest', None)
        approval = {'candidate': 'candidate', 'source_manifest': 'manifest'}
        protected_publication_gate('main', 'candidate', 'manifest', approval)
        with self.assertRaisesRegex(ValueError, 'Contributor'):
            protected_publication_gate('rebase', 'candidate', 'manifest', approval)


if __name__ == '__main__':
    unittest.main()
