import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentops.cli import DEFAULTS
from agentops.panel import make_server


class MaintenancePanelTests(unittest.TestCase):
    def test_read_only_status_and_authenticated_mutations(self):
        class Service:
            def __init__(self):
                self.calls = []
            def status(self):
                return {'updates': [], 'incidents': []}
            def dispatch(self, action, request):
                self.calls.append((action, request))
                return self.status()
        service = Service()
        with tempfile.TemporaryDirectory() as tmp:
            with make_server(Path(tmp) / 'ledger.sqlite3', port=0, config=DEFAULTS,
                             maintenance_service=service) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    origin = f'http://127.0.0.1:{server.server_port}'
                    with urlopen(origin + '/api/maintenance') as response:
                        self.assertEqual(json.load(response)['updates'], [])
                    self.assertFalse(service.calls)
                    with urlopen(origin + '/api/launcher') as response:
                        token = json.load(response)['token']
                    for action in ('refresh', 'update', 'remediate', 'retry', 'settings', 'fix-main'):
                        url = origin + '/api/maintenance/' + action
                        for headers in ({}, {'Origin': 'http://evil.example', 'X-Launch-Token': token}):
                            with self.assertRaises(HTTPError) as error:
                                urlopen(Request(url, data=b'{}', headers=headers))
                            self.assertEqual(error.exception.code, 403)
                        with urlopen(Request(url, data=b'{}', headers={'Origin': origin,
                            'X-Launch-Token': token, 'Content-Type': 'application/json'})) as response:
                            self.assertEqual(response.status, 200)
                    self.assertEqual(len(service.calls), 6)
                    with urlopen(origin + '/ui/maintenance.js') as response:
                        self.assertEqual(response.status, 200)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
