import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentops.automation import AutoReview
from agentops.launch import Launcher
from agentops.cli import DEFAULTS


class AutomationTests(unittest.TestCase):
    def setup_auto(self, directory):
        launcher = Mock()
        launcher.root = Path(directory)
        launcher.config = {'repo': 'o/r', 'exclude_authors': ['owner']}
        launcher.status.return_value = {}
        launcher.ledger.jobs.return_value = []
        launcher.preview.side_effect = lambda request: {'preview_id': 'receipt', 'prs': [
            p for p in launcher.github.snapshot.return_value['prs']
            if str(p['number']) in request['prs'].split()]}
        launcher.launch.return_value = {'run_id': 'run', 'selected': [2]}
        return AutoReview(launcher), launcher

    def snapshot(self, *prs, complete=True):
        return {'repo': 'o/r', 'main_sha': 'm'*40, 'complete': complete, 'prs': [
            {'number': n, 'head_sha': head, 'draft': draft, 'author': author,
             'title': f'PR {n}', 'updated_at': '2026-11-13'}
            for n, head, draft, author in prs]}

    def test_new_updated_draft_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': True, 'updated_prs': False, 'non_draft_only': True})
            launcher.github.snapshot.return_value = self.snapshot((1, 'a', False, 'user'))
            auto.tick()
            launcher.launch.assert_not_called()
            launcher.github.snapshot.return_value = self.snapshot(
                (1, 'b', False, 'user'), (2, 'c', True, 'user'), (3, 'd', False, 'OWNER'))
            auto.tick()
            launcher.launch.assert_not_called()
            launcher.github.snapshot.return_value['prs'][1]['draft'] = False
            auto.tick()
            self.assertEqual(launcher.preview.call_args.args[0], {
                'source': 'batch', 'prs': '2', 'non_draft_only': True, 'require_decision': True})
            launcher.launch.assert_called_once()
            restarted = AutoReview(launcher)
            restarted.tick()
            launcher.launch.assert_called_once()
            self.assertTrue(restarted.status()['settings']['new_prs'])

    def test_updates_ignore_comments_and_incomplete_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': False, 'updated_prs': True, 'non_draft_only': False})
            launcher.github.snapshot.return_value = self.snapshot((1, 'a', False, 'user'))
            auto.tick()
            auto.tick()
            launcher.launch.assert_not_called()
            launcher.github.snapshot.return_value = self.snapshot((1, 'b', False, 'user'), complete=False)
            with self.assertRaises(ValueError):
                auto.tick()
            launcher.launch.assert_not_called()
            launcher.github.snapshot.return_value['complete'] = True
            auto.tick()
            launcher.launch.assert_called_once()

    def test_invalid_toggle_and_busy_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            with self.assertRaises(ValueError):
                auto.configure({'new_prs': 'true'})
            auto.configure({'new_prs': True})
            launcher.github.snapshot.return_value = self.snapshot()
            auto.tick()
            launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'))
            launcher.status.return_value = {'manual': {'running': True}}
            auto.tick()
            launcher.launch.assert_not_called()
            launcher.status.return_value = {}
            auto.tick()
            launcher.launch.assert_called_once()

    def test_ledger_prevents_duplicate_after_dispatch_save_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': True})
            launcher.github.snapshot.return_value = self.snapshot()
            auto.tick()
            launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'))
            launcher.ledger.jobs.return_value = [{'repo': 'o/r', 'number': 2, 'head_sha': 'a'}]
            AutoReview(launcher).tick()
            launcher.launch.assert_not_called()

    def test_required_backlog_is_not_hidden_by_comment_only_or_stale_jobs(self):
        for state in ('held', 'stale', 'needs_followup'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as directory:
                auto, launcher = self.setup_auto(directory)
                auto.configure({'new_prs': True, 'updated_prs': True})
                launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'))
                launcher.github.snapshot.return_value['prs'][0]['review_decision'] = 'REVIEW_REQUIRED'
                launcher.ledger.jobs.return_value = [{'repo': 'o/r', 'number': 2, 'head_sha': 'a',
                    'main_sha': 'm'*40, 'state': state, 'stale': state=='stale',
                    'result': {'require_decision': False}}]
                auto.tick()
                launcher.launch.assert_called_once()
                self.assertTrue(launcher.preview.call_args.args[0]['require_decision'])

    def test_current_required_attempt_does_not_hot_loop_but_remains_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': True, 'updated_prs': True})
            launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'))
            launcher.github.snapshot.return_value['prs'][0]['review_decision'] = 'REVIEW_REQUIRED'
            launcher.ledger.jobs.return_value = [{'repo':'o/r','number':2,'head_sha':'a',
                'main_sha':'m'*40,'state':'held','stale':False,
                'result':{'require_decision':True, 'qualification_limits':['Proof missing']}}]
            auto.tick()
            auto.tick()
            launcher.launch.assert_not_called()
            self.assertEqual(auto.status()['attention'][0]['number'], 2)

    def test_stopped_unstarted_mandatory_plan_is_not_a_completed_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'updated_prs': True})
            launcher.github.snapshot.return_value = self.snapshot((2,'a',False,'user'))
            launcher.github.snapshot.return_value['prs'][0]['review_decision'] = 'REVIEW_REQUIRED'
            launcher.ledger.jobs.return_value = [{'repo':'o/r','number':2,'head_sha':'a',
                'main_sha':'m'*40,'state':'queued','run_id':'old','result':None}]
            launcher.ledger.get_run.return_value = {'stopped':True,'config':{'require_decision':True}}
            auto.tick()
            launcher.launch.assert_called_once()

    def test_real_launcher_persists_plan_and_restart_does_not_spawn_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            github = Mock()
            launcher = Launcher(Path(directory) / 'ledger.sqlite3', {**DEFAULTS, 'repo': 'o/r'}, github)
            auto = AutoReview(launcher)
            auto.configure({'new_prs': True, 'updated_prs': True})
            github.snapshot.return_value = self.snapshot()
            auto.tick()
            github.snapshot.return_value = self.snapshot((2, 'a'*40, False, 'user'))
            with patch('agentops.launch.subprocess.Popen') as spawn:
                spawn.return_value.poll.return_value = 0
                auto.tick()
                jobs = launcher.ledger.jobs(auto.status()['last_run'])
                self.assertEqual([(j['number'], j['head_sha']) for j in jobs], [(2, 'a'*40)])
                restarted = AutoReview(Launcher(Path(directory) / 'ledger.sqlite3',
                                                {**DEFAULTS, 'repo': 'o/r'}, github))
                restarted.tick()
                spawn.assert_called_once()

    def test_updated_only_reviews_newly_observed_pr_after_head_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'updated_prs': True})
            launcher.github.snapshot.return_value = self.snapshot()
            auto.tick()
            launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'))
            auto.tick()
            launcher.launch.assert_not_called()
            launcher.github.snapshot.return_value = self.snapshot((2, 'b', False, 'user'))
            auto.tick()
            launcher.launch.assert_called_once()

    def test_failed_spawn_retries_same_plan_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            github = Mock()
            config = {**DEFAULTS, 'repo': 'o/r'}
            path = Path(directory) / 'ledger.sqlite3'
            auto = AutoReview(Launcher(path, config, github))
            auto.configure({'new_prs': True})
            github.snapshot.return_value = self.snapshot()
            auto.tick()
            github.snapshot.return_value = self.snapshot((2, 'a'*40, False, 'user'))
            with patch('agentops.launch.subprocess.Popen', side_effect=OSError('cannot start')):
                with self.assertRaises(ValueError):
                    auto.tick()
            run_id = auto.data['retry_launch']['run_id']
            restarted = AutoReview(Launcher(path, config, github))
            with patch('agentops.launch.subprocess.Popen') as spawn:
                spawn.return_value.poll.return_value = None
                restarted.tick()
                spawn.assert_called_once()
                self.assertIn(run_id, spawn.call_args.args[0])
            self.assertIsNone(restarted.data['retry_launch'])
            self.assertEqual(len(restarted.launcher.ledger.jobs()), 1)

    def test_failed_spawn_draft_conversion_cancels_plan_and_waits_for_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            github = Mock()
            path = Path(directory) / 'ledger.sqlite3'
            auto = AutoReview(Launcher(path, {**DEFAULTS, 'repo': 'o/r'}, github))
            auto.configure({'new_prs': True})
            github.snapshot.return_value = self.snapshot()
            auto.tick()
            github.snapshot.return_value = self.snapshot((2, 'a'*40, False, 'user'))
            with patch('agentops.launch.subprocess.Popen', side_effect=OSError('cannot start')):
                with self.assertRaises(ValueError):
                    auto.tick()
            run_id = auto.data['retry_launch']['run_id']
            github.snapshot.return_value['prs'][0]['draft'] = True
            with patch('agentops.launch.subprocess.Popen') as spawn:
                auto.tick()
                spawn.assert_not_called()
                self.assertTrue(auto.launcher.ledger.get_run(run_id)['stopped'])
                github.snapshot.return_value['prs'][0]['draft'] = False
                auto.tick()
                spawn.assert_called_once()

    def test_toggle_can_be_disabled_during_slow_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': True})
            entered, release = threading.Event(), threading.Event()
            def slow_snapshot(**kwargs):
                entered.set()
                release.wait(3)
                return self.snapshot()
            launcher.github.snapshot.side_effect = slow_snapshot
            worker = threading.Thread(target=auto.tick)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(auto.configure({'new_prs': False})['settings']['new_prs'])
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            launcher.launch.assert_not_called()
