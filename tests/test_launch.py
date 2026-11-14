import tempfile
import unittest
import json
import threading
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from agentops.launch import Launcher, parse_prs
from agentops.cli import DEFAULTS


class LaunchTests(unittest.TestCase):
    def test_publication_uses_configured_default_and_explicit_choice(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher, _ = self.setup_launcher(directory)
            self.assertTrue(launcher.settings({})['publish'])
            self.assertFalse(launcher.settings({'publish': False})['publish'])
            launcher.config['publish'] = False
            self.assertFalse(launcher.settings({})['publish'])

    def test_batch_parses_deduplicates_and_rejects_foreign_urls(self):
        self.assertEqual(parse_prs('1, #2 https://github.com/o/r/pull/1', 'o/r'), [1, 2])
        for text in ('', '0', '-1', 'https://github.com/other/r/pull/1', '1;echo bad'):
            with self.assertRaises(ValueError):
                parse_prs(text, 'o/r')

    def setup_launcher(self, directory):
        github = Mock()
        github.snapshot.return_value = {'repo': 'o/r', 'complete': True, 'main_sha': 'a'*40,
            'prs': [dict(number=n, title=f'PR {n}', head_sha=str(n)*40,
                         author='someone', updated_at='2026-11-11') for n in (1, 2)]}
        launcher = Launcher(Path(directory) / 'ledger.sqlite3', {**DEFAULTS, 'repo': 'o/r'}, github)
        return launcher, github

    def test_launch_exact_subset_once_with_saved_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher, github = self.setup_launcher(directory)
            preview = launcher.preview({'source': 'batch', 'prs': '1 2', 'harness': 'claude', 'concurrency': 1})
            with patch('agentops.launch.subprocess.Popen') as spawn:
                spawn.return_value.poll.return_value = None
                result = launcher.launch({'preview_id': preview['preview_id'], 'selected': [2]})
                again = launcher.launch({'preview_id': preview['preview_id'], 'selected': [2]})
            self.assertEqual(result, again)
            spawn.assert_called_once()
            self.assertIn(result['run_id'], spawn.call_args.args[0])
            self.assertIn('--force-fresh', spawn.call_args.args[0])
            self.assertEqual([j['number'] for j in launcher.ledger.jobs(result['run_id'])], [2])
            self.assertEqual(launcher.ledger.get_run(result['run_id'])['config']['harness'], 'claude')

    def test_changed_head_and_invalid_settings_cannot_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher, github = self.setup_launcher(directory)
            for settings in ({'concurrency': 9}, {'publish': 'yes'}, {'harness': 'shell'}):
                with self.assertRaises(ValueError):
                    launcher.preview({'source': 'single', 'prs': '1', **settings})
            preview = launcher.preview({'source': 'single', 'prs': '1'})
            github.snapshot.return_value['prs'][0]['head_sha'] = 'c'*40
            with patch('agentops.launch.subprocess.Popen') as spawn, self.assertRaises(ValueError):
                launcher.launch({'preview_id': preview['preview_id'], 'selected': [1]})
            spawn.assert_not_called()

    def test_draft_conversion_after_preview_cannot_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher, github = self.setup_launcher(directory)
            for pr in github.snapshot.return_value['prs']:
                pr['draft'] = False
            preview = launcher.preview({'source': 'single', 'prs': '1', 'non_draft_only': True})
            github.snapshot.return_value['prs'][0]['draft'] = True
            with patch('agentops.launch.subprocess.Popen') as spawn, self.assertRaises(ValueError):
                launcher.launch({'preview_id': preview['preview_id'], 'selected': [1]})
            spawn.assert_not_called()

    def test_auto_decision_requirement_is_saved_on_plan_and_qualification_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher, _ = self.setup_launcher(directory)
            preview = launcher.preview({'source':'single','prs':'1','require_decision':True})
            with patch('agentops.launch.subprocess.Popen'):
                result = launcher.launch({'preview_id':preview['preview_id'],'selected':[1]})
            self.assertTrue(launcher.ledger.get_run(result['run_id'])['config']['require_decision'])
            self.assertEqual(launcher.ledger.jobs(result['run_id'])[0]['result']['review_task'], 'qualification')

    def test_query_is_scoped_and_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher, github = self.setup_launcher(directory)
            github.request.return_value = {'total_count': 1, 'incomplete_results': False,
                                           'items': [{'number': 2, 'repository_url': 'https://api.github.com/repos/o/r'}]}
            preview = launcher.preview({'source': 'query', 'query': 'author:someone'})
            self.assertEqual([p['number'] for p in preview['prs']], [2])
            self.assertIn('repo%3Ao%2Fr', github.request.call_args.args[1])
            github.request.return_value['incomplete_results'] = True
            with self.assertRaises(ValueError):
                launcher.preview({'source': 'query', 'query': 'author:someone'})

    def test_query_reads_every_page(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher, github = self.setup_launcher(directory)
            def page(numbers):
                return {'total_count': 101, 'incomplete_results': False, 'items': [
                    {'number': n, 'repository_url': 'https://api.github.com/repos/o/r'} for n in numbers]}
            github.request.side_effect = [page(range(1, 101)), page([101])]
            self.assertEqual(launcher.query_numbers('label:bug'), set(range(1, 102)))
            self.assertEqual(github.request.call_count, 2)

    def test_http_protection_and_real_plan_creation(self):
        from agentops.panel import make_server
        with tempfile.TemporaryDirectory() as directory:
            launcher, github = self.setup_launcher(directory)
            with patch('agentops.launch.GitHub', return_value=github):
                server = make_server(Path(directory) / 'ledger.sqlite3', port=0,
                                     config={**DEFAULTS, 'repo': 'o/r'})
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base + '/api/launcher') as response:
                    token = json.load(response)['token']
                payload = {'source': 'batch', 'prs': '1 2'}
                def post(path, body, origin=base, launch_token=token):
                    return urlopen(Request(base + path, data=json.dumps(body).encode(),
                        headers={'Content-Type': 'application/json', 'Origin': origin,
                                 'X-Launch-Token': launch_token}))
                for origin, launch_token in (('https://evil.example', token), (base, 'bad')):
                    with self.assertRaises(HTTPError) as denied:
                        post('/api/preview', payload, origin, launch_token)
                    self.assertEqual(denied.exception.code, 403)
                    with self.assertRaises(HTTPError) as denied_auto:
                        post('/api/automation', {'new_prs': True}, origin, launch_token)
                    self.assertEqual(denied_auto.exception.code, 403)
                with post('/api/automation', {'new_prs': True, 'updated_prs': True,
                                              'non_draft_only': True}) as response:
                    self.assertTrue(json.load(response)['settings']['new_prs'])
                with urlopen(base + '/api/launcher') as response:
                    self.assertTrue(json.load(response)['automation']['settings']['updated_prs'])
                with post('/api/preview', payload) as response:
                    preview = json.load(response)
                with patch('agentops.launch.subprocess.Popen') as spawn:
                    spawn.return_value.poll.return_value = 1
                    with post('/api/launch', {'preview_id': preview['preview_id'], 'selected': [1, 2]}) as response:
                        result = json.load(response)
                    with urlopen(base + '/api/launcher') as response:
                        self.assertEqual(json.load(response)['processes'][result['run_id']]['exit_code'], 1)
                with urlopen(base + '/api/report?run=' + result['run_id']) as response:
                    self.assertEqual(json.load(response)['total'], 2)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
