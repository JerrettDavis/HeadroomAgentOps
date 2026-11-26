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

    def test_existing_pr_reconciles_without_launching_agent(self):
        from agentops.maintenance_agent import MaintenanceAgent
        with tempfile.TemporaryDirectory() as temp:
            class Github:
                repo = 'o/r'
                def pages(self, endpoint, key=None):
                    return [{'number': 9, 'html_url': 'https://github.com/o/r/pull/9', 'state': 'open'}]
            agent = MaintenanceAgent(Path(temp) / 'ledger', {'repo': 'o/r', 'harness': 'codex'}, Github())
            with patch.object(agent, '_start', side_effect=AssertionError('must not restart')):
                result = agent.execute({'task': 'main', 'main_sha': 'a' * 40}, lambda _: None)
            self.assertEqual(result['pr']['number'], 9)


if __name__ == '__main__':
    unittest.main()
