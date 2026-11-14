import contextlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from urllib.request import Request

from agentops.ledger import Ledger
from agentops.panel import report, make_server, normalize_usage, check_status


class PanelTests(unittest.TestCase):
    def test_check_status_uses_latest_context_and_handles_unknown_conclusions(self):
        self.assertEqual(check_status({'statuses': [
            {'context': 'build', 'state': 'success', 'id': 2},
            {'context': 'build', 'state': 'failure', 'id': 1}]}), 'passed')
        self.assertEqual(check_status({'checks': [{'name': 'ci', 'conclusion': 'stale'}]}), 'unknown')
        self.assertEqual(check_status({'checks': [{'name': 'ci', 'conclusion': 'startup_failure'}]}), 'failed')
        self.assertEqual(check_status({'checks': [{'name': 'ci', 'status': 'in_progress'}]}), 'pending')
        self.assertEqual(check_status({}), 'unknown')

    def test_copilot_totals_are_not_counted_again_from_nested_agents(self):
        metrics = {'modelMetrics': {'model': {'usage': {'inputTokens': 100, 'outputTokens': 20,
                   'cacheReadTokens': 60, 'cacheWriteTokens': 5, 'reasoningTokens': 7}}},
                   'agentMetrics': {'main': {'modelMetrics': {'model': {'usage': {'inputTokens': 100}}}}},
                   'totalNanoAiu': 2500000000}
        usage = normalize_usage(metrics)
        self.assertEqual(usage['input_tokens'], 100)
        self.assertEqual(usage['cached_input_tokens'], 60)
        self.assertEqual(usage['ai_credits'], 2.5)
        self.assertNotIn('cost_usd', usage)

    def test_attempt_accounting_and_readonly_http(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.sqlite3'
            ledger = Ledger(path)
            sid = ledger.save_snapshot({'repo': 'owner/repo', 'prs': [{'number': 1, 'title': '<script>bad</script>'}]})
            run = ledger.create_run(sid, {})
            jid = ledger.enqueue(run, {'number': 1, 'head_sha': 'a'*40}, 'b'*40)
            with ledger.connect() as db:
                db.execute("UPDATE jobs SET state='held',attempt=2,result=? WHERE id=?",
                           (json.dumps({'execution': {'usage': {'input_tokens': 30, 'output_tokens': 3}}}), jid))
                for attempt, tokens in ((1, 10), (2, 30)):
                    result = {'execution': {'usage': {'input_tokens': tokens, 'output_tokens': 2, 'cost_usd': .01}}}
                    db.execute('INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?)',
                               (jid, attempt, 'owner', None, 'held', json.dumps(result), 1))
            before = path.read_bytes()
            r = report(path, run)
            self.assertEqual(r['usage']['input_tokens'], 40)
            self.assertEqual(r['usage']['cost_usd'], .02)
            self.assertEqual(r['finished'], 1)
            server = make_server(path, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                for name, mime, signature in (
                        ('icon.svg', 'image/svg+xml', b'<svg'),
                        ('icon.ico', 'image/x-icon', b'\x00\x00\x01\x00'),
                        ('icon-192.png', 'image/png', b'\x89PNG')):
                    with urlopen(base + '/brand/' + name) as response:
                        self.assertEqual(response.headers['Content-Type'], mime)
                        self.assertTrue(response.read().startswith(signature))
                with self.assertRaises(HTTPError):
                    urlopen(base + '/brand/unknown.svg')
                for name in ('marked.js', 'purify.js', 'markdown.js'):
                    with urlopen(base + '/ui/' + name) as response:
                        self.assertEqual(response.headers['Content-Type'], 'text/javascript; charset=utf-8')
                        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
                        self.assertGreater(len(response.read()), 100)
                with self.assertRaises(HTTPError):
                    urlopen(base + '/ui/unknown.js')
                with urlopen(base + '/api/report') as response:
                    self.assertEqual(json.load(response)['total'], 1)
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + '/api/report?run=absent')
                self.assertEqual(error.exception.code, 404)
                with self.assertRaises(HTTPError):
                    urlopen(base + '/api/report', data=b'{}')
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(base + '/api/report', headers={'Host': 'outside.example'}))
                self.assertEqual(denied.exception.code, 403)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
            self.assertEqual(path.read_bytes(), before)

    def test_missing_ledger_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.sqlite3'
            with self.assertRaises(Exception):
                report(path)
            self.assertFalse(path.exists())

    def test_running_retry_does_not_recount_previous_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.sqlite3'
            ledger = Ledger(path)
            sid = ledger.save_snapshot({'repo': 'owner/repo', 'prs': []})
            run = ledger.create_run(sid, {})
            jid = ledger.enqueue(run, {'number': 1, 'head_sha': 'a'*40}, 'b'*40)
            ledger.claim('first', 1, run)
            ledger.finish(jid, 'first', 'held', {'execution': {'usage': {'input_tokens': 100}}})
            ledger.retry(jid)
            ledger.claim('second', 1, run)
            r = report(path, run)
            self.assertEqual(r['usage']['input_tokens'], 100)
            self.assertEqual(r['usage']['unknown_attempts'], 1)
            self.assertEqual(r['jobs'][0]['usage'], {})

