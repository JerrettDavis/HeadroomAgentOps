import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.cli import main
from agentops.cli import DEFAULTS
from agentops.ledger import Ledger


class CLITests(unittest.TestCase):
    def test_switching_saved_plan_harness_selects_its_auth_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / 'ledger.sqlite3')
            snapshot = ledger.save_snapshot({'repo': DEFAULTS['repo'], 'main_sha': 'a' * 40, 'prs': []})
            run = ledger.create_run(snapshot, {**DEFAULTS, 'auth_root': 'old-codex-directory'})
            with patch('agentops.cli.auth_plan') as auth, patch('agentops.cli.Worker'), patch('agentops.cli.Coordinator') as coordinator, contextlib.redirect_stdout(io.StringIO()):
                coordinator.return_value.run.return_value = {}
                code = main(['--state', directory, 'run', '--plan', run, '--harness', 'claude'])
            self.assertEqual(code, 0)
            self.assertEqual(auth.call_args.args[0], 'claude')
            self.assertEqual(auth.call_args.args[2], str(Path.home() / '.claude'))

    def test_offline_plan_and_status_need_no_harness_or_github(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'snapshot.json'
            source.write_text(json.dumps({'repo': 'headroomlabs-ai/headroom', 'complete': True, 'main_sha': 'a' * 40,
                'prs': [{'number': 1, 'head_sha': 'b' * 40, 'author': 'a', 'updated_at': '2026-11-11'}]}))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--state', str(root / 'state'), 'plan', '--snapshot', str(source)])
            self.assertEqual(code, 0)
            plan = json.loads(output.getvalue())
            self.assertEqual(plan['selected'], [1])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--state', str(root / 'state'), 'status', '--run', plan['run_id']])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['states'], {'queued': 1})

    def test_invalid_concurrency_fails_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            config.write_text('concurrency = 99\n')
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(['--config', str(config), 'doctor']), 2)

    def test_snapshot_cannot_silently_change_target_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'snapshot.json'
            source.write_text(json.dumps({'repo': 'unexpected/repository', 'complete': True,
                                         'main_sha': 'a' * 40, 'prs': []}))
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                code = main(['--state', str(root / 'state'), 'plan', '--snapshot', str(source)])
            self.assertEqual(code, 2)


if __name__ == '__main__':
    unittest.main()
