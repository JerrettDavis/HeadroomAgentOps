import subprocess
import tempfile
import unittest
from pathlib import Path


class MaintenanceGitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'remote'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.git('config', 'receive.denyCurrentBranch', 'ignore')
        (self.repo / 'file').write_text('base\n')
        self.git('add', '.')
        self.git('commit', '-m', 'base')
        self.base = self.git('rev-parse', 'HEAD')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def record(self, conflict=False):
        self.git('checkout', '-b', 'topic')
        (self.repo / 'file').write_text('topic\n')
        self.git('commit', '-am', 'topic')
        head = self.git('rev-parse', 'HEAD')
        self.git('update-ref', 'refs/pull/1/head', head)
        self.git('checkout', 'main')
        (self.repo / ('file' if conflict else 'main-file')).write_text('main\n')
        self.git('add', '.')
        self.git('commit', '-m', 'main moved')
        return {'number': 1, 'head_sha': head, 'main_sha': self.git('rev-parse', 'HEAD'),
                'head_ref': 'topic', 'head_repo': 'o/r', 'repo': 'o/r'}

    def engine(self):
        from agentops.maintenance_git import MaintenanceGit
        return MaintenanceGit(self.root / 'jobs', 'o/r', remote=str(self.repo))

    def test_rebase_contains_main_and_preserves_contribution(self):
        record = self.record()
        engine = self.engine()
        prepared = engine.prepare(record)
        self.assertNotEqual(prepared['new_head'], record['head_sha'])
        self.assertTrue(prepared['equivalent'])
        engine.publish({**record, **prepared})
        self.assertEqual(self.git('rev-parse', 'topic'), prepared['new_head'])
        self.assertEqual(self.git('merge-base', 'topic', 'main'), record['main_sha'])

    def test_conflict_is_retained_without_remote_mutation(self):
        record = self.record(True)
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.engine().prepare(record)
        self.assertEqual(self.git('rev-parse', 'topic'), record['head_sha'])

    def test_author_push_rejects_lease(self):
        record = self.record()
        engine = self.engine()
        prepared = engine.prepare(record)
        self.git('update-ref', 'refs/heads/topic', record['main_sha'])
        with self.assertRaises((RuntimeError, ValueError)):
            engine.publish({**record, **prepared})
        self.assertEqual(self.git('rev-parse', 'topic'), record['main_sha'])

    def test_moved_main_rejected_during_fetch(self):
        record = self.record()
        record['main_sha'] = self.base
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.engine().prepare(record)


if __name__ == '__main__':
    unittest.main()
