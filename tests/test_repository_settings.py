"""Check portable configuration and removal of stale release permissions."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class RepositorySettingsTests(unittest.TestCase):
    def test_transfer_target_and_release_policy_reconcile_without_duplicate(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/configure_repository.py'
        spec = importlib.util.spec_from_file_location('repository_settings', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.github').mkdir()
            (root / '.github/repository.json').write_text(json.dumps({
                'repository': 'old/project', 'default_branch': 'main', 'description': 'Test',
                'required_checks': ['test'], 'required_approvals': 1,
                'release_environment': 'production-release', 'release_reviewers': ['maintainer'],
                'release_tag_pattern': 'v*'}))
            (root / '.github/labels.json').write_text('[]')
            calls = []

            def run(command, **kwargs):
                calls.append((command, kwargs))
                if command[2] == 'users/maintainer':
                    return SimpleNamespace(returncode=0, stdout='42', stderr='')
                if command == ['gh', 'api', 'repos/new/project/environments/production-release/deployment-branch-policies']:
                    return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'branch_policies': [
                        {'id': 1, 'name': '*', 'type': 'branch'},
                        {'id': 2, 'name': 'v*', 'type': 'tag'}]}))
                return SimpleNamespace(returncode=0, stdout='', stderr='')

            with patch.object(module, '__file__', str(root / 'scripts/configure_repository.py')), \
                    patch('sys.argv', ['configure_repository.py', '--repository', 'new/project', '--apply']), \
                    patch.object(module.subprocess, 'run', side_effect=run):
                module.main()
            endpoints = [part for command, _ in calls for part in command]
            self.assertFalse(any('old/project' in part for part in endpoints))
            self.assertIn('repos/new/project/environments/production-release/deployment-branch-policies/1', endpoints)
            self.assertNotIn('repos/new/project/environments/production-release/deployment-branch-policies/2', endpoints)
            policy_posts = [command for command, _ in calls if 'POST' in command]
            self.assertEqual(policy_posts, [])
            environment = next(json.loads(kwargs['input']) for command, kwargs in calls
                               if command[-3:] == ['repos/new/project/environments/production-release', '--input', '-'])
            self.assertEqual(environment['reviewers'], [{'type': 'User', 'id': 42}])


if __name__ == '__main__':
    unittest.main()
