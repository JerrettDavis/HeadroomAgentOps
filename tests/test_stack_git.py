import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from agentops.stack_git import GitStack, validate_commands, bounded_process
from agentops.stacking import plan_stack
from test_stacking import inventory


class GitFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'upstream'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        (self.repo / 'file.txt').write_text('first\n' + 'middle\n' * 10 + 'last\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')
        self.data = inventory()
        self.data.update(default_sha=self.base, prs=[])

    def git(self, *args):
        result = subprocess.run(['git', *args], cwd=self.repo, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        return result.stdout.strip()

    def source(self, number, old, new, parent=None):
        self.git('checkout', '-B', f'pr-{number}', parent or self.base)
        path = self.repo / 'file.txt'
        path.write_text(path.read_text().replace(old, new))
        self.git('commit', '-am', f'change {number}')
        sha = self.git('rev-parse', 'HEAD')
        self.git('update-ref', f'refs/pull/{number}/head', sha)
        pr = inventory()['prs'][0].copy()
        pr.update(number=number, head_sha=sha, head_ref=f'pr-{number}', base_sha=self.base)
        self.data['prs'].append(pr)
        return sha

    def engine(self, **kwargs):
        return GitStack(self.root / 'stacks', 'owner/repo', remote=str(self.repo), **kwargs)


class IntegrationTests(GitFixture):
    def test_cumulative_merge_preserves_sources_and_does_not_publish(self):
        one = self.source(1, 'first', 'one')
        two = self.source(2, 'last', 'two')
        record = plan_stack(self.data, [2, 1])
        result = self.engine().prepare(record)
        self.assertEqual(result['state'], 'prepared')
        self.assertEqual(result['validation']['state'], 'unconfigured')
        checkout = Path(result['checkout'])
        self.assertEqual((checkout / 'file.txt').read_text(), 'one\n' + 'middle\n' * 10 + 'two\n')
        for sha in (one, two):
            self.assertEqual(subprocess.run(['git', 'merge-base', '--is-ancestor', sha,
                                             result['revision']], cwd=checkout).returncode, 0)
        self.assertEqual(self.git('rev-parse', 'main'), self.base)
        repeated = self.engine().prepare(record)
        self.assertEqual(repeated['revision'], result['revision'])

    def test_cumulative_conflict_reports_member_and_paths(self):
        self.source(1, 'first', 'one')
        self.source(2, 'first', 'two')
        result = self.engine().prepare(plan_stack(self.data, [1, 2]))
        self.assertEqual(result['state'], 'conflict')
        self.assertEqual(result['conflicting_pr'], 2)
        self.assertEqual(result['conflicts'], ['file.txt'])
        self.assertNotIn('revision', result)
        self.assertEqual(self.git('rev-parse', 'main'), self.base)

    def test_changed_fetched_head_cannot_redefine_pinned_input(self):
        self.source(1, 'first', 'one')
        record = plan_stack(self.data, [1])
        self.git('update-ref', 'refs/pull/1/head', self.base)
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.engine().prepare(record)

    def test_validation_cannot_mutate_published_revision(self):
        self.source(1, 'first', 'one')
        record = plan_stack(self.data, [1])
        class Validator:
            def __call__(self, checkout, commands):
                (checkout / 'file.txt').write_text('malicious test side effect')
                return {'state': 'failed', 'commands': [{'argv': commands[0], 'exit_code': 1, 'log': 'failed'}]}
        result = self.engine(commands=[['python3', '-m', 'unittest']], validator=Validator()).prepare(record)
        self.assertEqual(result['state'], 'validation_failed')
        self.assertTrue((Path(result['checkout']) / 'file.txt').read_text().startswith('one'))

    def test_validation_commands_are_argv_and_bounded(self):
        for value in ['echo hello', [['']], [[]], [['python3', 1]], [['a']] * 21]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_commands(value)
        self.assertEqual(validate_commands([['python3', '-m', 'unittest']]), [['python3', '-m', 'unittest']])

    def test_noisy_validation_output_is_bounded_and_timeout_is_reported(self):
        import sys
        code, timed_out, log = bounded_process([sys.executable, '-c',
                                               'import sys;sys.stdout.write("x"*100000+"tail")'], 5)
        self.assertEqual(code, 0)
        self.assertFalse(timed_out)
        self.assertLessEqual(len(log), 16000)
        self.assertTrue(log.endswith('tail'))
        code, timed_out, log = bounded_process([sys.executable, '-c', 'import time;time.sleep(5)'], 1)
        self.assertTrue(timed_out)
        self.assertIsNone(code)

    def test_docker_validation_has_no_credentials_network_or_default_agent_entrypoint(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return 'sha256:' + 'a' * 64 if args[1:3] == ['image', 'inspect'] else ''
        with patch('agentops.stack_git.command', run), patch('agentops.stack_git.bounded_process',
                                                           return_value=(1, False, 'test failed')):
            result = self.engine().validate(self.repo, [['python3', '-m', 'unittest']])
            self.assertEqual(result['state'], 'failed')
        # Inspect the actual container command at its process boundary.
        with patch('agentops.stack_git.command', run), patch('agentops.stack_git.bounded_process') as process:
            process.return_value = (None, True, 'timeout')
            result = self.engine().validate(self.repo, [['python3', '-m', 'unittest']])
            args = process.call_args.args[0]
            self.assertIn('--read-only', args)
            self.assertEqual(args[args.index('--network') + 1], 'none')
            self.assertEqual(args[args.index('--entrypoint') + 1], 'python3')
            self.assertNotIn('/job/auth', ' '.join(args))
            self.assertTrue(result['commands'][0]['timed_out'])
        self.assertTrue(any(args[1:3] == ['rm', '--force'] for args in calls))


if __name__ == '__main__':
    unittest.main()
