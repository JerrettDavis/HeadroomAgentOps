import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentops.cli import DEFAULTS
from agentops.panel import make_server


class AllowanceAPITests(unittest.TestCase):
    def test_status_is_responsive_and_settings_are_origin_protected_and_persistent(self):
        entered, release = threading.Event(), threading.Event()
        def reader(config):
            entered.set()
            release.wait(3)
            return {'source': 'Synthetic account', 'blocked': False, 'auth_fingerprint': 'synthetic', 'windows': [{
                'id': 'weekly', 'label': 'Weekly', 'period': 'weekly', 'window_minutes': 10080,
                'used_percent': 60, 'resets_at': time.time() + 3600}]}
        with tempfile.TemporaryDirectory() as temporary:
            config = {**DEFAULTS, 'auth_root': temporary}
            path = Path(temporary) / 'ledger.db'
            server = make_server(path, port=0, config=config, usage_reader=reader,
                                 usage_identity_reader=lambda config: 'synthetic')
            self.addCleanup(server.server_close)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            def get(route):
                with urlopen(base + route, timeout=2) as response:
                    return json.load(response)
            def post(body, origin=base, token=None):
                return urlopen(Request(base + '/api/allowances', data=json.dumps(body).encode(),
                    headers={'Origin': origin, 'X-Launch-Token': token or '',
                             'Content-Type': 'application/json'}), timeout=2)
            try:
                first = get('/api/allowances')
                self.assertEqual(first['providers'][0]['state'], 'unknown')
                self.assertTrue(entered.wait(1))
                start = time.monotonic()
                self.assertFalse(get('/api/allowances')['providers'][0]['allowed'])
                self.assertLess(time.monotonic() - start, 1)
                token = get('/api/launcher')['token']
                for origin, credential in (('https://evil.test', token), (base, 'wrong')):
                    with self.assertRaises(HTTPError) as denied:
                        post({'provider': 'codex', 'stop_percent': 55}, origin, credential)
                    self.assertEqual(denied.exception.code, 403)
                for invalid in ({'provider': 'codex', 'stop_percent': 101},
                                {'provider': 'wrong', 'stop_percent': 80}):
                    with self.assertRaises(HTTPError) as rejected:
                        post(invalid, token=token)
                    self.assertEqual(rejected.exception.code, 400)
                with post({'provider': 'codex', 'stop_percent': 55}, token=token) as response:
                    self.assertEqual(json.load(response)['providers'][0]['stop_percent'], 55)
                release.set()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    result = get('/api/allowances')['providers'][0]
                    if not result['refreshing']:
                        break
                    time.sleep(.01)
                self.assertEqual(result['state'], 'paused')
                from agentops.allowances import Allowances
                from agentops.ledger import Ledger
                self.assertEqual(Allowances(Ledger(path), config).status()['stop_percent'], 55)
            finally:
                release.set()
                server.shutdown()
                thread.join(2)


if __name__ == '__main__':
    unittest.main()
