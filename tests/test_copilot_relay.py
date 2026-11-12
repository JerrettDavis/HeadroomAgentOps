import io
import json
import unittest
import urllib.error
import urllib.request
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch, Mock

from agentops.copilot_relay import InferenceRelay, NativeRelay, host_environment, atomic_json, serve


class Reply(io.BytesIO):
    def __init__(self, data, status=200, content_type='application/json'):
        super().__init__(data)
        self.status = status
        self.content_type = content_type

    def getheader(self, name, default=None):
        return self.content_type if name.lower() == 'content-type' else default


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def upstream(method, path, body, headers):
            self.calls.append((method, path, body, headers))
            if path == '/models':
                return Reply(json.dumps({'data': [{'id': 'gpt-6.1-sol'}, {'id': 'other-model'}]}).encode())
            return Reply(b'data: {"type":"response.completed"}\n\ndata: [DONE]\n\n', content_type='text/event-stream')
        self.relay = InferenceRelay('host-only-repository-capable-token', 'gpt-6.1-sol', upstream)
        self.relay.__enter__()
        self.addCleanup(self.relay.__exit__, None, None, None)

    def request(self, path, data=None, token=None):
        request = urllib.request.Request(self.relay.local_url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization': 'Bearer ' + (token or self.relay.access_key), 'Content-Type': 'application/json'})
        return urllib.request.urlopen(request, timeout=5)

    def test_models_are_limited_to_selected_model_and_never_expose_host_credential(self):
        with self.request('/v1/models') as response:
            content = response.read()
        self.assertEqual(json.loads(content)['data'], [{'id': 'gpt-6.1-sol'}])
        self.assertNotIn(b'host-only-repository-capable-token', content)
        self.assertNotIn('host-only-repository-capable-token', json.dumps(self.relay.worker_environment()))

    def test_responses_relay_stream_and_replace_worker_authentication(self):
        with self.request('/v1/responses', {'model': 'gpt-6.1-sol', 'input': 'hello', 'stream': True}) as response:
            self.assertIn(b'response.completed', response.read())
        method, path, body, headers = self.calls[-1]
        self.assertEqual((method, path), ('POST', '/responses'))
        self.assertEqual(headers['Authorization'], 'Bearer host-only-repository-capable-token')
        self.assertNotIn(self.relay.access_key, json.dumps(headers))
        self.assertEqual(json.loads(body)['model'], 'gpt-6.1-sol')

    def test_repository_routes_arbitrary_urls_and_other_models_are_rejected(self):
        for path, body, status in [('/repos/o/r/issues', {}, 404),
                                   ('/v1/responses?url=https://github.com', {}, 404),
                                   ('/v1/responses', {'model': 'other-model'}, 403)]:
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, body)
            self.assertEqual(error.exception.code, status)
        self.assertEqual(len(self.calls), 1)

    def test_unauthenticated_requests_cannot_use_host_credential(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/responses', {'model': 'gpt-6.1-sol'}, token='wrong-key')
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(len(self.calls), 1)

    def test_server_side_tools_and_provider_storage_are_not_capabilities(self):
        for extra in [{'tools': [{'type': 'mcp', 'server_url': 'https://github.com'}]},
                      {'tools': [{'type': 'web_search'}]}, {'store': True},
                      {'background': True}, {'previous_response_id': 'unrelated-account-response'}]:
            with self.subTest(extra=extra), self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/v1/responses', {'model': 'gpt-6.1-sol', **extra})
            self.assertEqual(error.exception.code, 403)
        self.assertEqual(len(self.calls), 1)

    def test_account_conversations_files_and_item_references_are_not_capabilities(self):
        for extra in [{'conversation': 'private-account-conversation'}, {'prompt': {'id': 'private-prompt'}},
                      {'input': [{'type': 'item_reference', 'id': 'account-object'}]},
                      {'input': [{'role': 'user', 'content': [{'type': 'input_file', 'file_id': 'file-secret'}]}]},
                      {'input': [{'role': 'user', 'content': [{'type': 'input_image', 'file_id': 'file-secret'}]}]},
                      {'input': [{'role': 'user', 'content': [{'type': 'input_file', 'file_url': 'https://github.com'}]}]}]:
            with self.subTest(extra=extra), self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/v1/responses', {'model': 'gpt-6.1-sol', **extra})
            self.assertEqual(error.exception.code, 403)
        self.assertEqual(len(self.calls), 1)

    def test_self_contained_function_history_remains_supported(self):
        body = {'model': 'gpt-6.1-sol', 'input': [
            {'role': 'user', 'content': [{'type': 'input_text', 'text': 'Read the fixture.'}]},
            {'type': 'function_call', 'call_id': 'call-1', 'name': 'read_file', 'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'call-1', 'output': 'Fixture body.'}],
            'tools': [{'type': 'function', 'name': 'read_file', 'parameters': {'type': 'object'}}]}
        with self.request('/v1/responses', body) as response:
            self.assertEqual(response.status, 200)

    def test_client_side_custom_patch_tools_remain_supported(self):
        body = {'model': 'gpt-6.1-sol', 'input': [
            {'type': 'custom_tool_call', 'call_id': 'patch-1', 'name': 'apply_patch', 'input': 'fixture patch'},
            {'type': 'custom_tool_call_output', 'call_id': 'patch-1', 'output': 'Applied inside worker.'}],
            'tools': [{'type': 'custom', 'name': 'apply_patch', 'format': {'type': 'text'}}]}
        with self.request('/v1/responses', body) as response:
            self.assertEqual(response.status, 200)

    def test_inline_reasoning_content_is_supported_but_provider_references_are_rejected(self):
        body = {'model': 'gpt-6.1-sol', 'input': [{'type': 'reasoning', 'id': 'reason-1',
                'summary': [], 'content': [], 'encrypted_content': 'inline-encrypted-state'}]}
        with self.request('/v1/responses', body) as response:
            self.assertEqual(response.status, 200)
        body['input'][0]['content'] = [{'type': 'item_reference', 'id': 'private-account-object'}]
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/responses', body)
        self.assertEqual(error.exception.code, 403)

    def test_upstream_redirect_is_not_followed_or_exposed(self):
        self.relay.upstream = lambda *args: Reply(b'elsewhere', status=302)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/v1/responses', {'model': 'gpt-6.1-sol'})
        self.assertEqual(error.exception.code, 502)


class RelayLifecycleTests(unittest.TestCase):
    def test_preserve_close_stops_heartbeat_without_terminating_live_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            relay = NativeRelay(directory, 'run', timeout_seconds=10)
            relay.process = Mock()
            relay.log = Mock()
            relay.beat_thread = threading.Thread(target=relay.heartbeat)
            relay.beat_thread.start()
            relay.close(preserve=True)
            self.assertFalse(relay.beat_thread.is_alive())
            self.assertFalse((relay.directory / 'stop').exists())
            relay.process.wait.assert_not_called()
            relay.process.terminate.assert_not_called()
            relay.log.close.assert_called_once()
            self.assertEqual(relay.config['grace_seconds'], 70)

    def test_host_environment_strips_credentials_and_runtime_preloads(self):
        with patch.dict('os.environ', {'PATH': 'tools', 'GH_TOKEN': 'publisher',
                'GITHUB_TOKEN': 'publisher', 'OPENAI_API_KEY': 'model',
                'NODE_OPTIONS': '--require hostile.js', 'PYTHONPATH': 'hostile'}, clear=True):
            self.assertEqual(host_environment(), {'PATH': 'tools'})

    def exercise_server(self, stop_explicitly):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / str(uuid.uuid4())
            directory.mkdir()
            atomic_json(directory / 'config.json', {'model': 'gpt-6.1-sol', 'grace_seconds': 0.4})
            atomic_json(directory / 'heartbeat.json', {'time': time.time()})
            results = []
            def factory(credential, model):
                self.assertEqual(credential, 'host-only')
                return InferenceRelay(credential, model,
                    lambda *args: Reply(json.dumps({'data': [{'id': model}]}).encode()))
            thread = threading.Thread(target=lambda: results.append(serve(directory,
                credential_loader=lambda *args: 'host-only', relay_factory=factory)))
            thread.start()
            try:
                deadline = time.monotonic() + 5
                while not (directory / 'ready.json').exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue((directory / 'ready.json').exists())
                for _ in range(4):
                    atomic_json(directory / 'heartbeat.json', {'time': time.time()})
                    time.sleep(0.15)
                self.assertTrue(thread.is_alive(), 'Fresh heartbeats must keep relay alive')
                if stop_explicitly:
                    (directory / 'stop').touch()
                thread.join(4)
                self.assertFalse(thread.is_alive())
                self.assertEqual(results, [0])
                self.assertFalse((directory / 'ready.json').exists())
                self.assertNotIn('host-only', ''.join(p.read_text() for p in directory.iterdir()))
            finally:
                (directory / 'stop').touch()
                thread.join(5)

    def test_explicit_stop_retires_provider_and_removes_capability(self):
        self.exercise_server(True)

    def test_lost_parent_heartbeat_retires_provider_after_grace(self):
        self.exercise_server(False)

    def test_worker_stages_explicit_model_capability_without_ambient_credentials(self):
        from agentops.worker import Worker
        with tempfile.TemporaryDirectory() as temporary, patch.dict('os.environ', {
                'COPILOT_PROVIDER_API_KEY': 'ambient-secret', 'GH_TOKEN': 'publisher-secret'}):
            environment = {'COPILOT_PROVIDER_BASE_URL': 'http://host.docker.internal:1234/v1',
                           'COPILOT_PROVIDER_API_KEY': 'relay-capability'}
            worker = Worker(temporary, 'o/r', harness='copilot', runner=lambda *a, **k: '',
                            model_env=environment, model_relay={'directory': 'owned-relay'})
            prepared = worker.prepare({'number': 1, 'head_sha': 'b' * 40, 'main_sha': 'a' * 40},
                                      {'base_sha': 'a' * 40}, None, '{}')
            staged = (Path(prepared['directory']) / 'auth/model-env.json').read_text()
            self.assertIn('relay-capability', staged)
            self.assertNotIn('ambient-secret', staged)
            self.assertNotIn('publisher-secret', staged)
            self.assertEqual(prepared['model_relay'], {'directory': 'owned-relay'})
