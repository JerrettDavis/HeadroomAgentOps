import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentops.stack_git import GitStack, validate_commands
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


if __name__ == '__main__':
    unittest.main()
