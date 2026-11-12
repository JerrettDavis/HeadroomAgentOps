"""Loopback inference capability; native Copilot OAuth never enters a worker."""

import argparse
import hmac
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .process import process_identity


HOST_ENVIRONMENT = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE',
                    'APPDATA', 'LOCALAPPDATA', 'HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
                    'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}


def host_environment():
    return {key: value for key, value in os.environ.items() if key.upper() in HOST_ENVIRONMENT}


RESPONSE_FIELDS = {'model', 'input', 'instructions', 'tools', 'tool_choice', 'parallel_tool_calls', 'stream',
    'store', 'background', 'max_output_tokens', 'temperature', 'top_p', 'reasoning', 'text', 'truncation',
    'include', 'metadata', 'prompt_cache_key', 'prompt_cache_retention', 'safety_identifier', 'service_tier',
    'user', 'stream_options', 'context_management'}


def self_contained_input(value):
    def reject(reason):
        print("Inference policy rejected input shape: " + reason, file=sys.stderr, flush=True)
        return False
    def references(node):
        if isinstance(node, dict):
            if (node.get('type') == 'item_reference' or set(node) & {'file_id', 'file_ids', 'file_url',
                    'conversation', 'conversation_id', 'previous_response_id', 'vector_store_ids', 'container_id'}):
                return True
            return any(references(v) for v in node.values())
        return isinstance(node, list) and any(references(v) for v in node)
    if references(value):
        return reject("shape_1")
    def content(items):
        if isinstance(items, str):
            return True
        if not isinstance(items, list):
            return reject("shape_2")
        for item in items:
            if not isinstance(item, dict) or any(key in item for key in ('file_id', 'file_url', 'item_id')):
                return reject("shape_3")
            kind = item.get('type')
            if kind in ('input_text', 'output_text', 'summary_text'):
                if not isinstance(item.get('text'), str):
                    return reject("shape_4")
            elif kind == 'refusal':
                if not isinstance(item.get('refusal'), str):
                    return reject("shape_5")
            elif kind == 'input_image':
                if not isinstance(item.get('image_url'), str) or not item['image_url'].startswith('data:image/'):
                    return reject("shape_6")
            elif kind == 'input_file':
                if not isinstance(item.get('file_data'), str) or not item['file_data'].startswith('data:'):
                    return reject("shape_7")
            else:
                return reject("shape_8")
        return True
    if isinstance(value, str):
        return True
    if not isinstance(value, list):
        return reject("shape_9")
    for item in value:
        if not isinstance(item, dict):
            return reject("shape_10")
        kind = item.get('type', 'message')
        allowed = {
            'message': {'type', 'role', 'content', 'id', 'status'},
            'function_call': {'type', 'id', 'call_id', 'name', 'arguments', 'status'},
            'custom_tool_call': {'type', 'id', 'call_id', 'name', 'input', 'status'},
            'function_call_output': {'type', 'id', 'call_id', 'output', 'status'},
            'custom_tool_call_output': {'type', 'id', 'call_id', 'output', 'status'},
            'reasoning': {'type', 'id', 'summary', 'content', 'encrypted_content', 'status'},
            'compaction': {'type', 'id', 'encrypted_content', 'status'},
        }
        if not isinstance(kind, str) or kind not in allowed or set(item) - allowed[kind]:
            known_fields = {'phase', 'channel', 'signature', 'provider_metadata', 'provider_specific_fields',
                            'summary', 'name', 'content', 'role', 'type', 'id', 'status', 'call_id',
                            'arguments', 'input', 'output', 'encrypted_content'}
            print(json.dumps({'rejected_item_type': kind if isinstance(kind, str) and kind in allowed else 'unknown',
                'known_fields': sorted(set(item) & known_fields), 'unknown_field_count': len(set(item) - known_fields)}),
                file=sys.stderr, flush=True)
            return reject("shape_11")
        if kind == 'message':
            if item.get('role') not in ('user', 'assistant', 'system', 'developer') or not content(item.get('content')):
                return reject("shape_12")
            item['type'] = 'message'
        elif kind in ('function_call', 'custom_tool_call'):
            argument = 'arguments' if kind == 'function_call' else 'input'
            if not all(isinstance(item.get(k), str) for k in ('call_id', 'name', argument)):
                return reject("shape_13")
        elif kind in ('function_call_output', 'custom_tool_call_output'):
            if not isinstance(item.get('call_id'), str) or not content(item.get('output')):
                return reject("shape_14")
        elif kind in ('reasoning', 'compaction'):
            if 'content' in item and not content(item['content']):
                return reject('reasoning_content')
            if not isinstance(item.get('encrypted_content'), str) and not (
                    kind == 'reasoning' and item.get('summary') and content(item['summary'])):
                return reject("shape_15")
        else:
            return reject("shape_16")
    return True

class LimitedServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, *args):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args)

    def process_request(self, request, address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class InferenceRelay:
    def __init__(self, credential, model, upstream=None):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', model):
            raise ValueError('Invalid Copilot model ID')
        self.credential, self.model = credential, model
        self.access_key = 'agentops_' + secrets.token_urlsafe(32)
        self.upstream = upstream or self._upstream

    def _upstream(self, method, path, body, headers):
        connection = http.client.HTTPSConnection('api.githubcopilot.com', timeout=120)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            response._relay_connection = connection
            return response
        except BaseException:
            connection.close()
            raise

    def headers(self):
        return {'Authorization': 'Bearer ' + self.credential, 'Content-Type': 'application/json',
                'Accept': 'application/json', 'User-Agent': 'GitHubCopilotCLI/1.0.109',
                'Copilot-Integration-Id': 'copilot-developer-cli'}

    @staticmethod
    def close_response(response):
        response.close()
        if getattr(response, '_relay_connection', None):
            response._relay_connection.close()

    def __enter__(self):
        response = self.upstream('GET', '/models', None, self.headers())
        try:
            if response.status != 200:
                raise ValueError('Native Copilot model catalog is unavailable')
            catalog = json.loads(response.read(4 * 1024 * 1024))
            self.models = [m for m in catalog.get('data', []) if m.get('id') == self.model]
            if not self.models:
                raise ValueError('Requested model is not available to the native Copilot account')
        finally:
            self.close_response(response)
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def setup(self):
                super().setup()
                self.connection.settimeout(20)

            def log_message(self, *args):
                pass

            def reply(self, status, data):
                content = json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                self.wfile.write(content)

            def authorized(self):
                provided = self.headers.get('Authorization', '')
                if not hmac.compare_digest(provided.encode(), ('Bearer ' + relay.access_key).encode()):
                    self.reply(401, {'error': 'Model capability required'})
                    return False
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                if self.path == '/v1/models':
                    self.reply(200, {'object': 'list', 'data': relay.models})
                elif self.path == '/health':
                    self.reply(200, {'model': relay.model})
                else:
                    self.reply(404, {'error': 'Inference route only'})

            def do_POST(self):
                if not self.authorized():
                    return
                if self.path != '/v1/responses':
                    self.reply(404, {'error': 'Inference route only'})
                    return
                try:
                    if (self.headers.get('Transfer-Encoding') or
                            len(self.headers.get_all('Content-Length', [])) != 1):
                        raise ValueError()
                    length = int(self.headers['Content-Length'])
                    if not 0 < length <= 8 * 1024 * 1024:
                        self.reply(413, {'error': 'Request size limit'})
                        return
                    if self.headers.get_content_type() != 'application/json':
                        raise ValueError()
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        raise ValueError()
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError()
                    if data.get('model') != relay.model:
                        self.reply(403, {'error': 'Selected model only'})
                        return
                    if (data.get('store') is True or data.get('background') is True or
                            set(data) - RESPONSE_FIELDS or not self_contained_input(data.get('input', ''))):
                        self.reply(403, {'error': 'Stateless inference only'})
                        return
                    tools = data.get('tools', [])
                    if not isinstance(tools, list) or any(not isinstance(t, dict) or t.get('type') not in ('function', 'custom') for t in tools):
                        self.reply(403, {'error': 'Client-side tools only',
                            'tool_types': [str(t.get('type'))[:64] for t in tools if isinstance(t, dict)][:32] if isinstance(tools, list) else []})
                        return
                    choice = data.get('tool_choice', 'auto')
                    if not (choice in ('auto', 'none', 'required') if isinstance(choice, str) else
                            isinstance(choice, dict) and choice.get('type') in ('function', 'custom')):
                        self.reply(403, {'error': 'Client-side tool selection only',
                                        'tool_choice_type': str(choice.get('type'))[:64] if isinstance(choice, dict) else 'invalid'})
                        return
                    data.update(store=False, background=False, tools=tools)
                    # Re-encode so duplicate JSON keys cannot select another model upstream.
                    body = json.dumps(data, allow_nan=False).encode()
                except (ValueError, TypeError, RecursionError):
                    self.reply(400, {'error': 'Invalid inference request'})
                    return
                response = None
                sent_headers = False
                try:
                    response = relay.upstream('POST', '/responses', body, relay.headers())
                    if 300 <= response.status < 400:
                        self.reply(502, {'error': 'Upstream redirect refused'})
                        return
                    self.send_response(response.status)
                    self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.close_connection = True
                    sent_headers = True
                    while chunk := response.read1(65536):
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (OSError, http.client.HTTPException):
                    if not sent_headers:
                        self.reply(502, {'error': 'Copilot inference transport unavailable'})
                finally:
                    if response is not None:
                        relay.close_response(response)

        self.server = LimitedServer(('127.0.0.1', 0), Handler)
        self.local_url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def worker_environment(self):
        return {'COPILOT_PROVIDER_BASE_URL': 'http://host.docker.internal:' + str(self.server.server_port) + '/v1',
                'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_WIRE_API': 'responses',
                'COPILOT_PROVIDER_API_KEY': self.access_key, 'COPILOT_PROVIDER_MODEL_ID': self.model,
                'COPILOT_PROVIDER_WIRE_MODEL': self.model, 'COPILOT_MODEL': self.model}

    def __exit__(self, *args):
        if hasattr(self, 'server'):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(5)


def native_credential(cli_root=None, account=None):
    node = shutil.which('node')
    if not node:
        raise ValueError('Native Copilot relay requires Node and the locally installed Copilot CLI')
    candidates = ([Path(cli_root)] if cli_root else [
        Path(node).resolve().parent / 'node_modules/@github/copilot',
        Path(node).resolve().parent.parent / 'lib/node_modules/@github/copilot',
        Path.home() / '.local/lib/node_modules/@github/copilot'])
    root = next((p.resolve() for p in candidates if (p / 'package.json').is_file()), None)
    if root is None:
        raise ValueError('Set copilot_cli_root to the locally installed @github/copilot package')
    script = ('const p=require("path");const k=require(p.join(process.argv[1],"prebuilds",'
              'process.platform+"-"+process.arch,"keytar.node"));'
              'k.findCredentials("copilot-cli").then(x=>process.stdout.write(JSON.stringify(x)))'
              '.catch(()=>process.exit(3));')
    env = host_environment()
    result = subprocess.run([node, '-e', script, str(root)], capture_output=True, text=True, env=env, timeout=30)
    if result.returncode:
        raise ValueError('Native Copilot credential store is unavailable; use copilot login locally')
    entries = json.loads(result.stdout)
    entries = [e for e in entries if e['account'].startswith('https://github.com:') and
               (not account or e['account'].rsplit(':', 1)[-1].lower() == account.lower())]
    if len(entries) != 1 or not entries[0].get('password'):
        raise ValueError('Select one native Copilot sign-in with copilot_account')
    return entries[0]['password']


def atomic_json(path, data):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data), encoding='utf8')
        temporary.chmod(0o600)
        # Windows readers can briefly deny replacement while opening the old
        # heartbeat. Retry only that local sharing error; never expose partial JSON.
        for attempt in range(10):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.01)
    finally:
        temporary.unlink(missing_ok=True)


class NativeRelay:
    """Owned provider process survives coordinator loss for one worker budget."""
    def __init__(self, root, run_id, model='gpt-6.1-sol', account=None, cli_root=None, timeout_seconds=3600):
        self.directory = Path(root).resolve() / str(uuid.uuid4())
        self.directory.mkdir(parents=True)
        self.config = {'run_id': run_id, 'model': model, 'account': account, 'cli_root': cli_root,
                       'grace_seconds': timeout_seconds + 60}
        self.stop_event = threading.Event()

    def heartbeat(self):
        while not self.stop_event.is_set():
            atomic_json(self.directory / 'heartbeat.json', {'time': time.time()})
            self.stop_event.wait(5)

    def __enter__(self):
        atomic_json(self.directory / 'config.json', self.config)
        atomic_json(self.directory / 'heartbeat.json', {'time': time.time()})
        self.log = (self.directory / 'server.log').open('ab')
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        self.process = subprocess.Popen([sys.executable, '-m', 'agentops.copilot_relay', '--serve', str(self.directory)],
            cwd=Path(__file__).resolve().parent.parent, stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log,
            env=host_environment(), **options)
        self.beat_thread = threading.Thread(target=self.heartbeat, daemon=True)
        self.beat_thread.start()
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                ready = self.directory / 'ready.json'
                if ready.is_file():
                    self.ready = json.loads(ready.read_text(encoding='utf8'))
                    return self
                if self.process.poll() is not None:
                    raise ValueError('Native Copilot relay could not authenticate or start; inspect its local server log')
                time.sleep(0.1)
            raise ValueError('Native Copilot relay startup timed out')
        except BaseException:
            self.close()
            raise

    def worker_environment(self):
        port, model = self.ready['port'], self.config['model']
        return {'COPILOT_PROVIDER_BASE_URL': f'http://host.docker.internal:{port}/v1',
                'COPILOT_PROVIDER_TYPE': 'openai', 'COPILOT_PROVIDER_WIRE_API': 'responses',
                'COPILOT_PROVIDER_API_KEY': self.ready['access_key'], 'COPILOT_PROVIDER_MODEL_ID': model,
                'COPILOT_PROVIDER_WIRE_MODEL': model, 'COPILOT_MODEL': model}

    def descriptor(self):
        return {'directory': str(self.directory), 'model': self.config['model'],
                'owner': {k: self.ready[k] for k in ('pid', 'started', 'host')}}

    def close(self, preserve=False):
        self.stop_event.set()
        if hasattr(self, 'beat_thread'):
            self.beat_thread.join(2)
        if not preserve:
            (self.directory / 'stop').touch()
            if hasattr(self, 'process'):
                try:
                    self.process.wait(10)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(10)
        if hasattr(self, 'log'):
            self.log.close()

    def __exit__(self, kind, *args):
        self.close(preserve=kind is not None)


def serve(directory, credential_loader=native_credential, relay_factory=InferenceRelay):
    directory = Path(directory).resolve()
    uuid.UUID(directory.name)
    config = json.loads((directory / 'config.json').read_text(encoding='utf8'))
    try:
        credential = credential_loader(config.get('cli_root'), config.get('account'))
        with relay_factory(credential, config['model']) as relay:
            atomic_json(directory / 'ready.json', {**process_identity(), 'port': relay.server.server_port,
                        'model': relay.model, 'access_key': relay.access_key})
            heartbeat = None
            last_seen = time.monotonic()
            while not (directory / 'stop').exists():
                current = (directory / 'heartbeat.json').read_bytes()
                if current != heartbeat:
                    heartbeat, last_seen = current, time.monotonic()
                if time.monotonic() - last_seen > config['grace_seconds']:
                    break
                time.sleep(0.25)
        return 0
    except Exception:
        # No native credentials, model inputs or authentication headers in diagnostics.
        print('Native Copilot relay failed to initialize or remain available.', file=sys.stderr)
        return 1
    finally:
        (directory / 'ready.json').unlink(missing_ok=True)


def retire_relays(root, run_id):
    root = Path(root).resolve()
    if not root.exists():
        return
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink() or directory.resolve().parent != root:
            continue
        try:
            uuid.UUID(directory.name)
            config = directory / 'config.json'
            if config.is_symlink() or config.resolve().parent != directory:
                continue
            if json.loads(config.read_text(encoding='utf8')).get('run_id') == run_id:
                (directory / 'stop').touch()
        except (ValueError, OSError):
            continue


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--serve', required=True)
    sys.exit(serve(parser.parse_args().serve))
