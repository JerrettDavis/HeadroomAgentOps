"""Optional pinned harnesses, through a single-request private loopback bridge.

No harness inherits provider credentials. Tool logs contain private transcripts.
Only provider usage is evidence; nonstreaming bridge timings are not TTFT.
"""
from __future__ import annotations

import json
from functools import lru_cache
import os
from pathlib import Path
import secrets
import signal
import socket
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PINS = {'inspect': ('inspect-ai', '0.3.292'), 'aiperf': ('aiperf', '0.14.0')}
INSPECT_TRANSPORT = 'openai==3.4.0'


def _execute(command, directory, env, log, timeout=180):
    """Own all harness descendants, including workers surviving their leader."""
    from .process import WindowsJob
    job = WindowsJob() if os.name == 'nt' else None
    if job:
        command = [sys.executable, '-I', '-X', 'utf8', str(Path(__file__).with_name('process.py')),
                   job.name, *command]
    process = None
    try:
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=log,
            start_new_session=os.name != 'nt', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        code = process.wait(timeout=timeout)
        return subprocess.CompletedProcess(command, code)
    finally:
        try:
            if job:
                job.terminate()
            elif process:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            if process:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if not job:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        finally:
            if job:
                job.close()


def _environment():
    # Preserve OS process necessities, never provider keys, proxy URLs or Python injection.
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        'SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT',
    }}
    env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', NO_COLOR='1',
               HF_HUB_OFFLINE='1', TOKENIZERS_PARALLELISM='false')
    return env


def available(tool: str, config: dict | None = None) -> dict:
    if tool not in PINS:
        return {'available': False, 'version': None, 'reason': 'Unsupported benchmark tool'}
    python = str((config or {}).get('tool_python') or (config or {}).get('python') or sys.executable)
    return dict(_probe(tool, python, int(time.monotonic() / 10)))


@lru_cache(maxsize=64)
def _probe(tool, python, epoch):
    package, version = PINS[tool]
    try:
        script = 'import importlib.metadata,sys; print(importlib.metadata.version(sys.argv[1]))'
        if tool == 'inspect':
            script += '; print(importlib.metadata.version("openai"))'
        result = subprocess.run([python, '-I', '-c',
            script, package],
            capture_output=True, text=True, timeout=20, env=_environment(),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        installed = result.stdout.strip()
        expected = version + '\n3.4.0' if tool == 'inspect' else version
        if result.returncode == 0 and installed == expected:
            return {'available': True, 'version': version, 'reason': '', 'python': python}
    except (OSError, subprocess.SubprocessError):
        pass
    return {'available': False, 'version': version,
            'reason': f'Install {package}=={version}' + (f' and {INSPECT_TRANSPORT}' if tool == 'inspect' else '') + ' in the selected tool Python environment', 'python': python}


def prepare(tool: str, root: Path) -> dict:
    """Explicit setup only; callers must not invoke this during discovery."""
    if tool not in PINS:
        raise ValueError('Unsupported benchmark tool')
    package, version = PINS[tool]
    directory = Path(root).resolve() / f'{tool}-{version}'
    python = directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    directory.parent.mkdir(parents=True, exist_ok=True)
    uv = shutil.which('uv')
    with (directory.parent / f'{tool}-setup.log').open('ab') as log:
        if not python.is_file():
            command = [uv, 'venv', '--python', '3.12', str(directory)] if uv else [sys.executable, '-m', 'venv', str(directory)]
            subprocess.run(command, check=True, stdout=log, stderr=log, timeout=300)
        if not available(tool, {'python': str(python)})['available']:
            requirements = [f'{package}=={version}'] + ([INSPECT_TRANSPORT] if tool == 'inspect' else [])
            command = [uv, 'pip', 'install', '--python', str(python), *requirements] if uv else [str(python), '-m', 'pip', 'install', *requirements]
            subprocess.run(command, check=True, stdout=log, stderr=log, timeout=600)
            _probe.cache_clear()
    return available(tool, {'python': str(python)})


def _validate(tool, protocol, model, task):
    if tool not in PINS or protocol not in {'openai', 'azure'}:
        raise ValueError('Tool adapters support OpenAI/Azure chat only')
    if not isinstance(model, str) or not model or task.get('stream') or any(k in task for k in ('tools', 'tool_choice', 'expected_tools')):
        raise ValueError('Tool adapters require nonstreaming text tasks without tools')
    messages = task.get('messages')
    if not isinstance(messages, list) or not messages or any(
        not isinstance(m, dict) or set(m) != {'role', 'content'} or
        m['role'] not in {'system', 'user', 'assistant'} or not isinstance(m['content'], str)
        for m in messages):
        raise ValueError('Tool adapters require plain text chat messages')
    if tool == 'aiperf' and (len(messages) != 1 or messages[0]['role'] != 'user'):
        raise ValueError('AIPerf currently supports a single user message')


def attempt(tool: str, request: callable, protocol: str, model: str, task: dict,
            workspace: Path, config: dict) -> dict:
    _validate(tool, protocol, model, task)
    readiness = available(tool, config)
    if not readiness['available']:
        raise ValueError(readiness['reason'])
    directory = Path(workspace).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # Each invocation gets independent immutable logs even when a cell reuses a workspace.
    directory = directory / (tool + '-' + secrets.token_hex(8))
    directory.mkdir()
    credential = secrets.token_urlsafe(24)
    captured, violations = [], []
    reservation = threading.Lock()
    stopping = threading.Event()
    connections = set()
    body = {'model': model, 'messages': task['messages'], 'temperature': 0,
            'max_tokens': task.get('max_tokens', 1024), 'stream': False}

    class Gateway(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            with reservation:
                connections.add(self.connection)
                if stopping.is_set():
                    self.connection.close()

        def finish(self):
            try:
                super().finish()
            finally:
                with reservation:
                    connections.discard(self.connection)

        def log_message(self, *args):
            pass

        def reply(self, status, value):
            if stopping.is_set():
                return
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(404, {'error': 'Unsupported route'})

        def do_POST(self):
            try:
                if self.headers.get('Authorization') != 'Bearer ' + credential:
                    self.reply(403, {'error': 'Private harness gateway'})
                    return
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError('Unsupported request size')
                submitted = json.loads(self.rfile.read(size))
                with reservation:
                    if (stopping.is_set() or self.path != '/v1/chat/completions' or captured or violations or
                            submitted.get('model') != 'studio-model' or
                            submitted.get('messages') != task['messages'] or submitted.get('stream')):
                        raise ValueError('Harness request differed from the planned single task')
                    # Reserve before calling upstream: concurrent retries cannot incur extra charges.
                    captured.append({})
                start = time.perf_counter()
                response = request('/v1/chat/completions', body)
                with reservation:
                    if stopping.is_set():
                        return
                    captured[0].update(response=response, latency_ms=(time.perf_counter() - start) * 1000)
                self.reply(200, response)
            except Exception:
                with reservation:
                    if stopping.is_set():
                        return
                    violations.append('Harness gateway rejected a request or the provider request failed')
                try:
                    self.reply(502, {'error': violations[-1]})
                except (OSError, TimeoutError):
                    pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    server.daemon_threads = True
    server.block_on_close = False
    server.timeout = 1
    # Bound incomplete HTTP clients rather than allowing shutdown to block forever.
    original_get = server.get_request
    def get_request():
        connection, address = original_get()
        connection.settimeout(30)
        return connection, address
    server.get_request = get_request
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
    thread.start()
    env = _environment()
    env.update(STUDIO_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1', STUDIO_API_KEY=credential)
    input_path = directory / 'task.json'
    input_path.write_text(json.dumps(task), encoding='utf-8')
    if tool == 'inspect':
        command = [readiness['python'], '-I', str(Path(__file__).with_name('tools_worker.py')), str(input_path), str(directory)]
    else:
        dataset = directory / 'dataset.jsonl'
        dataset.write_text(json.dumps({'text': task['messages'][0]['content'], 'output_length': task.get('max_tokens', 1024)}) + '\n', encoding='utf-8')
        command = [readiness['python'], '-I', str(Path(__file__).with_name('tools_worker.py')), 'aiperf', 'profile', '--model', 'studio-model',
                   '--endpoint-type', 'chat', '--url', env['STUDIO_BASE_URL'].removesuffix('/v1'),
                   '--input-file', str(dataset), '--custom-dataset-type', 'single_turn',
                   '--concurrency', '1', '--request-count', '1',
                   '--wait-for-model-timeout', '0', '--request-timeout-seconds', '60',
                   '--tokenizer', 'builtin', '--use-server-token-count', '--use-legacy-max-tokens',
                   '--extra-inputs', 'temperature:0', '--ui', 'none', '--no-server-metrics',
                   '--artifact-dir', str(directory / 'native')]
    try:
        with (directory / 'harness.log').open('wb') as log:
            completed = _execute(command, directory, env, log)
        if completed.returncode or violations or len(captured) != 1 or 'response' not in captured[0]:
            raise ValueError(f'{tool} execution failed; inspect private harness diagnostics')
    finally:
        # An upstream callback may remain blocked until LocalAdapter tears down
        # its proxy. Do not wait for it or let it publish late responses.
        with reservation:
            stopping.set()
            pending = list(connections)
        for connection in pending:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    response = captured[0]['response']
    choices = response.get('choices') or []
    text = (choices[0].get('message') or {}).get('content') if choices else None
    tokens = (response.get('usage') or {}).get('prompt_tokens')
    if type(tokens) is not int or tokens < 0 or not isinstance(response.get('model'), str):
        raise ValueError('Provider response omitted measured token usage or model identity')
    return {'success': bool(not task.get('expected') or isinstance(text, str) and task['expected'] in text),
            'tokens': tokens, 'latency_ms': captured[0]['latency_ms'], 'response_model': response['model'],
            'tool_metrics': {'tool': tool, 'version': PINS[tool][1], 'requests': 1,
                             'latency_basis': 'proxy_roundtrip_nonstreaming', 'native_logs': str(directory)}}
