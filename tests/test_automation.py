import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agentops.automation import AutoReview
from agentops.launch import Launcher
from agentops.cli import DEFAULTS
from agentops.evidence import Evidence


class AutomationTests(unittest.TestCase):
    def test_equivalent_head_update_skips_worker_and_persists_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'updated_prs': True})
            source = {'number': 1, 'head_sha': 'old', 'base_sha': 'base',
                      'files': [{'filename': 'a.py', 'status': 'modified',
                                 'patch': '@@ -1 +1 @@\n-a()\n+b()'}]}
            evidence = Evidence(Path(directory) / 'evidence')
            proof = {'number': 1, 'head_sha': 'old',
                     'reviewed_pr_evidence': evidence.put(source)}
            launcher.ledger.jobs.return_value = [{'id': 'prior', 'repo': 'o/r',
                'number': 1, 'head_sha': 'old', 'state': 'stale',
                'result': {'evidence_record': evidence.put(proof)}}]
            launcher.github.snapshot.return_value = self.snapshot((1, 'old', False, 'user'))
            auto.tick()
            launcher.github.snapshot.return_value = self.snapshot((1, 'new', False, 'user'))
            launcher.github.pull.return_value = {**source, 'head_sha': 'new'}
            auto.tick()
            launcher.launch.assert_not_called()
            notes = auto.status()['review_notes']['1']
            self.assertEqual(notes['reason'], 'equivalent_contribution')
            self.assertEqual(notes['source_job'], 'prior')
            self.assertEqual(AutoReview(launcher).status()['review_notes']['1'], notes)
            launcher.github.pull.return_value = {**source, 'head_sha': 'new',
                'files': [{**source['files'][0], 'patch': '@@ -8 +8 @@\n-a()\n+b()'}]}
            launcher.github.review_base_content.side_effect = lambda value, file: (
                'a()\n' if value['head_sha'] == 'old' else 'header\n' * 7 + 'a()\n')
            decision = auto.material_update(launcher.github.snapshot.return_value['prs'][0],
                                           launcher.ledger.jobs.return_value, 'm' * 40)
            self.assertFalse(decision['material'])
            launcher.github.pull.return_value = {**source, 'head_sha': 'new', 'base_ref': 'release'}
            decision = auto.material_update(launcher.github.snapshot.return_value['prs'][0],
                                           launcher.ledger.jobs.return_value, 'm' * 40)
            self.assertTrue(decision['material'])

    def test_material_update_still_launches(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'updated_prs': True})
            launcher.github.snapshot.return_value = self.snapshot((1, 'old', False, 'user'))
            auto.tick()
            launcher.github.snapshot.return_value = self.snapshot((1, 'new', False, 'user'))
            auto.tick()
            launcher.launch.assert_called_once()

    def test_slow_reconciliation_does_not_block_inventory_or_new_pr_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': True})
            launcher.github.snapshot.return_value = self.snapshot()
            entered, release = threading.Event(), threading.Event()
            def slow_refresh(*args, **kwargs):
                entered.set()
                release.wait(5)
                return {'errors': [], 'resume_runs': []}
            launcher.refresh_reviews.side_effect = slow_refresh
            try:
                auto.tick(background=True)
                self.assertTrue(entered.wait(1))
                self.assertIsNotNone(auto.status()['last_poll'])
                launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'))
                auto.tick(background=True)
                launcher.launch.assert_called_once()
                self.assertTrue(auto.status()['reconciling'])
            finally:
                release.set()
                auto.refresh_thread.join(5)

    def test_review_refresh_is_periodic_and_runs_before_new_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': True})
            launcher.github.snapshot.return_value = self.snapshot()
            launcher.refresh_reviews.return_value = {'errors': [], 'resume_runs': ['existing']}
            launcher.resume_reviews.return_value = []
            with patch('agentops.automation.time.time', return_value=1000):
                auto.tick()
                auto.tick()
            launcher.refresh_reviews.assert_called_once()
            launcher.resume_reviews.assert_called_once_with(['existing'], non_draft_only=True)
            with patch('agentops.automation.time.time', return_value=1301):
                auto.tick()
            self.assertEqual(launcher.refresh_reviews.call_count, 2)

    def test_failed_exact_revision_backs_off_without_blocking_new_heads_or_other_prs(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'new_prs': True, 'updated_prs': True})
            launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'), (3, 'b', False, 'user'))
            for pr in launcher.github.snapshot.return_value['prs']:
                pr['review_decision'] = 'REVIEW_REQUIRED'
            launcher.ledger.jobs.return_value = [{'repo': 'o/r', 'number': 2, 'head_sha': 'a',
                'main_sha': 'm'*40, 'state': 'failed', 'created': 990, 'result': {'reason': 'Invalid worker result'}}]
            with patch('agentops.automation.time.time', return_value=1000):
                auto.tick()
            self.assertEqual(launcher.preview.call_args.args[0]['prs'], '3')
            self.assertEqual(auto.status()['attention'][0]['state'], 'retry_wait')
            launcher.launch.reset_mock()
            launcher.preview.reset_mock()
            launcher.github.snapshot.return_value['prs'] = launcher.github.snapshot.return_value['prs'][:1]
            with patch('agentops.automation.time.time', return_value=1400):
                auto.tick()
            launcher.launch.assert_called_once()
            launcher.launch.reset_mock()
            launcher.github.snapshot.return_value['prs'][0]['head_sha'] = 'changed'
            with patch('agentops.automation.time.time', return_value=1001):
                auto.tick()
            launcher.launch.assert_called_once()

    def setup_auto(self, directory):
        launcher = Mock()
        launcher.root = Path(directory)
        launcher.config = {'repo': 'o/r', 'exclude_authors': ['owner']}
        launcher.status.return_value = {}
        launcher.ledger.jobs.return_value = []
        launcher.refresh_reviews.return_value = {'errors': []}
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

    def test_stopped_unstarted_plan_cannot_hide_unreviewed_changed_head(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            auto.configure({'updated_prs': True})
            auto.data.update(initialized=True, seen={'2': {'head': 'a', 'pending': None}})
            launcher.github.snapshot.return_value = self.snapshot((2, 'a', False, 'user'))
            launcher.github.snapshot.return_value['prs'][0]['review_decision'] = 'CHANGES_REQUESTED'
            launcher.ledger.jobs.return_value = [{'repo': 'o/r', 'number': 2, 'head_sha': 'a',
                'main_sha': 'm' * 40, 'state': 'queued', 'run_id': 'stopped', 'result': None}]
            launcher.ledger.get_run.return_value = {'stopped': True, 'config': {}}
            auto.tick()
            launcher.launch.assert_called_once()

    def test_skip_cache_is_bound_to_target_and_inventory_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            pr = {**self.snapshot((2, 'a', False, 'user'))['prs'][0],
                  'base_sha': 'b' * 40, 'base_ref': 'main', 'head_repo': 'o/fork'}
            identity = {key: pr.get(key) for key in
                        ('number', 'head_sha', 'base_sha', 'base_ref', 'head_repo', 'updated_at')}
            record = {'material': False, 'reason': 'equivalent_contribution',
                      'inventory_identity': identity, 'main_sha': 'm' * 40, 'source_job': 'prior'}
            evidence = Evidence(Path(directory) / 'evidence')
            auto.data['review_notes'] = {'2': {**record, 'evidence': evidence.put(record)}}
            self.assertFalse(auto.material_update(pr, [], 'm' * 40)['material'])
            self.assertTrue(auto.material_update({**pr, 'base_ref': 'release'}, [], 'm' * 40)['material'])

    def test_stopped_duplicate_of_analysed_head_reuses_without_full_pr_ingestion(self):
        with tempfile.TemporaryDirectory() as directory:
            auto, launcher = self.setup_auto(directory)
            evidence = Evidence(Path(directory) / 'evidence')
            pr = {**self.snapshot((2, 'a', False, 'user'))['prs'][0],
                  'base_ref': 'main', 'head_repo': 'o/fork', 'files': []}
            proof = {'number': 2, 'head_sha': 'a', 'reviewed_pr_evidence': evidence.put(pr)}
            prior = [{'id': 'analysis', 'state': 'stale', 'head_sha': 'a',
                      'result': {'evidence_record': evidence.put(proof)}}]
            decision = auto.material_update(pr, prior, 'm' * 40)
            self.assertFalse(decision['material'])
            self.assertEqual(decision['reason'], 'unchanged_reviewed_head')
            launcher.github.pull.assert_not_called()

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
