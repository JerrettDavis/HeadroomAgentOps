import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentops.panel import make_server
from agentops.cli import DEFAULTS


class StackPanelTests(unittest.TestCase):
    def test_stack_actions_require_existing_human_request_boundary(self):
        class Service:
            def __init__(self):
                self.calls = []
            def status(self):
                return {'stacks': [], 'model_calls': 0}
            def prepare(self, request):
                self.calls.append(('prepare', request))
                return {'id': 'a' * 64, 'state': 'preparing'}
            def publish(self, request):
                self.calls.append(('publish', request))
                return {'state': 'published', 'pr_url': 'https://github.com/owner/repo/pull/99'}
            def refresh(self, request):
                self.calls.append(('refresh', request))
                return {'state': 'prepared'}
        service = Service()
        with tempfile.TemporaryDirectory() as tmp:
            with make_server(Path(tmp) / 'ledger.sqlite3', port=0, config={**DEFAULTS, 'publish': False},
                             stack_service=service) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.addCleanup(server.shutdown)
                origin = f'http://127.0.0.1:{server.server_port}'
                with urlopen(origin + '/api/stacks') as response:
                    self.assertEqual(json.load(response)['model_calls'], 0)
                with urlopen(origin + '/api/launcher') as response:
                    token = json.load(response)['token']
                for action in ('prepare', 'publish', 'refresh'):
                    url = origin + '/api/stacks/' + action
                    before = len(service.calls)
                    for headers in ({}, {'X-Launch-Token': token},
                                    {'X-Launch-Token': token, 'Origin': 'http://evil.example'}):
                        with self.assertRaises(HTTPError) as caught:
                            urlopen(Request(url, data=b'{}', headers=headers))
                        self.assertEqual(caught.exception.code, 403)
                    self.assertEqual(len(service.calls), before)
                    with urlopen(Request(url, data=b'{"numbers":[1,2]}',
                                         headers={'X-Launch-Token': token, 'Origin': origin,
                                                  'Content-Type': 'application/json'})) as response:
                        self.assertEqual(response.status, 200)
                self.assertEqual([c[0] for c in service.calls], ['prepare', 'publish', 'refresh'])
                server.shutdown()
                thread.join()
