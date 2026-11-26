"""Bounded genuine AIPerf load batches over a private credential-free gateway."""
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import tools
from .process import WindowsJob


def _execute(command, directory, env, log, cancel, timeout):
    job = WindowsJob() if os.name == 'nt' else None
    if job:
        command = [sys.executable, '-I', '-X', 'utf8', str(Path(__file__).with_name('process.py')), job.name, *command]
    process = None
    try:
        process = subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=log,
                                   start_new_session=os.name != 'nt', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if cancel.is_set():
                raise InterruptedError('Load batch cancelled')
            if time.monotonic() >= deadline:
                raise TimeoutError('Load batch timed out')
            cancel.wait(.05)
        return process.returncode
    finally:
        if job:
            job.terminate()
        elif process:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process:
            process.wait(timeout=5)
        if job:
            job.close()


def run(request, protocol, model, task, workspace, config, *, concurrency=1, requests=1,
        cancel=None, timeout=180):
    """Run one arm/one concurrency level. Caller budgets sum across arms/levels <=1200.

    request(path, body) is the native arm's actual SDK closure, called exactly once
    per reserved harness request. It must have bounded I/O timeouts. Cancellation
    terminates harness descendants; already executing provider calls cannot be undone.
    """
    tools._validate('aiperf', protocol, model, task)
    if type(requests) is not int or not 1 <= requests <= 1200:
        raise ValueError('Load requests must be an integer from 1 to 1200')
    if type(concurrency) is not int or not 1 <= concurrency <= min(64, requests):
        raise ValueError('Load concurrency must be from 1 to min(64, requests)')
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 3600:
        raise ValueError('Load timeout must be from 1 to 3600 seconds')
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise InterruptedError('Load batch cancelled')
    ready = tools.available('aiperf', config)
    if not ready['available']:
        raise ValueError(ready['reason'])
    directory = Path(workspace).resolve() / ('aiperf-load-' + secrets.token_hex(8))
    directory.mkdir(parents=True)
    credential = secrets.token_urlsafe(24)
    lock = threading.Lock()
    stopping = threading.Event()
    connections, captured, violations = set(), [], []
    active = 0
    body = {'model': model, 'messages': task['messages'], 'temperature': 0,
            'max_tokens': task.get('max_tokens', 1024), 'stream': False}
    class Gateway(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            with lock:
                connections.add(self.connection)
        def finish(self):
            try:
                super().finish()
            finally:
                with lock:
                    connections.discard(self.connection)
        def log_message(self, *args):
            pass
        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def do_GET(self):
            self.reply(404, {'error':'Unsupported route'})
        def do_POST(self):
            nonlocal active
            reserved = False
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError('Invalid size')
                submitted = json.loads(self.rfile.read(size))
                with lock:
                    if (stopping.is_set() or cancel.is_set() or self.path != '/v1/chat/completions'
                        or self.headers.get('Authorization') != 'Bearer ' + credential
                        or len(captured) >= requests or active >= concurrency
                        or submitted.get('model') != 'studio-model'
                        or submitted.get('messages') != task['messages'] or submitted.get('stream')):
                        raise ValueError('Unplanned harness request')
                    entry = {}
                    captured.append(entry)
                    active += 1
                    reserved = True
                    start = time.perf_counter()
                response = request('/v1/chat/completions', dict(body))
                with lock:
                    entry.update(response=response, start=start, end=time.perf_counter())
                if not stopping.is_set():
                    self.reply(200, response)
            except Exception:
                with lock:
                    violations.append('Gateway rejected a request or provider failed')
                try:
                    if not stopping.is_set():
                        self.reply(502, {'error':'Load request failed'})
                except OSError:
                    pass
            finally:
                if reserved:
                    with lock:
                        active -= 1
    server = ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
    server.daemon_threads, server.block_on_close = True, False
    original = server.get_request
    def get_request():
        connection, address = original()
        connection.settimeout(30)
        return connection, address
    server.get_request = get_request
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval':.05}, daemon=True)
    thread.start()
    env = tools._environment()
    env.update(STUDIO_API_KEY=credential, STUDIO_BASE_URL=f'http://127.0.0.1:{server.server_port}/v1')
    dataset = directory / 'dataset.jsonl'
    dataset.write_text(json.dumps({'text':task['messages'][0]['content'], 'output_length':task.get('max_tokens',1024)})+'\n', encoding='utf-8')
    command = [ready['python'], '-I', str(Path(__file__).with_name('tools_worker.py')), 'aiperf', 'profile',
        '--model', 'studio-model', '--endpoint-type', 'chat', '--url', env['STUDIO_BASE_URL'].removesuffix('/v1'),
        '--input-file', str(dataset), '--custom-dataset-type', 'single_turn', '--concurrency', str(concurrency),
        '--request-count', str(requests), '--wait-for-model-timeout', '0', '--request-timeout-seconds', '60',
        '--tokenizer', 'builtin', '--use-server-token-count', '--use-legacy-max-tokens',
        '--extra-inputs', 'temperature:0', '--ui', 'none', '--no-server-metrics', '--artifact-dir', str(directory/'native')]
    try:
        with (directory/'harness.log').open('wb') as log:
            code = _execute(command, directory, env, log, cancel, timeout)
        if code or violations or len(captured) != requests or any('response' not in e for e in captured):
            raise ValueError('AIPerf load batch failed; inspect private harness diagnostics')
    finally:
        with lock:
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
    prompt = completion = 0
    models = set()
    attempts = []
    for index, entry in enumerate(captured):
        response = entry['response']
        usage = response.get('usage') or {}
        if any(type(usage.get(k)) is not int or usage[k] < 0 for k in ('prompt_tokens','completion_tokens')) or not response.get('model') or not isinstance(response['model'], str):
            raise ValueError('Provider omitted actual usage or model identity')
        prompt += usage['prompt_tokens']
        completion += usage['completion_tokens']
        models.add(response['model'])
        choices = response.get('choices') or []
        text = (choices[0].get('message') or {}).get('content') if choices else None
        attempts.append({'request_index': index, 'tokens': usage['prompt_tokens'],
            'completion_tokens': usage['completion_tokens'], 'response_model': response['model'],
            'latency_ms': (entry['end'] - entry['start']) * 1000,
            'success': bool(not task.get('expected') or isinstance(text, str) and task['expected'] in text)})
    elapsed = max(e['end'] for e in captured) - min(e['start'] for e in captured)
    return {'tool':'aiperf', 'version':tools.PINS['aiperf'][1], 'concurrency':concurrency,
            'completed_requests':len(captured), 'elapsed_seconds':elapsed,
            'requests_per_second':len(captured)/elapsed, 'prompt_tokens':prompt,
            'completion_tokens':completion, 'response_models':sorted(models),
            'attempts':attempts,
            'latency_ms':[(e['end']-e['start'])*1000 for e in captured],
            'measurement_basis':'gateway_first_dispatch_to_last_completion_nonstreaming',
            'native_logs':str(directory)}
