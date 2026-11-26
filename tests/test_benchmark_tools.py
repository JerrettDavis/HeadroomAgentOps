import os
import subprocess
import sys
import time
import json
import threading
from urllib.request import Request, urlopen
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_explicit_setup_does_not_pass_provider_credentials_to_installers(tmp_path, monkeypatch):
    from agentops.benchmarks import tools
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'):
        monkeypatch.setenv(key, 'private-installer-test-secret')
    calls = []
    def record(command, env):
        calls.append((command, env))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(tools.subprocess, 'run', lambda command, **kwargs: record(command, kwargs.get('env')))
    monkeypatch.setattr(tools, '_execute', lambda command, directory, env, log, timeout: record(command, env))
    monkeypatch.setattr(tools.shutil, 'which', lambda command: 'uv')
    monkeypatch.setattr(tools, 'available', lambda *args: {'available': len(calls) == 2, 'version': '0.3.292'})
    assert tools.prepare('inspect', tmp_path)['available']
    assert len(calls) == 2
    assert 'venv' in calls[0][0]
    assert 'install' in calls[1][0]
    for _, env in calls:
        assert env is not None, 'installer inherited the full process environment'
        assert not any(key in env for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'))
        assert 'private-installer-test-secret' not in env.values()


def test_cancellation_does_not_wait_for_upstream_and_reserves_only_one_request(tmp_path, monkeypatch):
    from agentops.benchmarks import tools
    entered, release = threading.Event(), threading.Event()
    calls, clients = [], []
    monkeypatch.setattr(tools, 'available', lambda *args: {'available': True, 'python': sys.executable})
    def provider(*args):
        calls.append(args)
        entered.set()
        release.wait(2)
        return {'model': 'model', 'usage': {'prompt_tokens': 1}, 'choices': []}
    def execute(command, directory, env, log, timeout=180):
        def send():
            try:
                request = Request(env['STUDIO_BASE_URL'] + '/chat/completions',
                    data=json.dumps({'model': 'studio-model', 'messages': task['messages'], 'stream': False}).encode(),
                    headers={'Authorization': 'Bearer ' + env['STUDIO_API_KEY'], 'Content-Type': 'application/json'})
                with urlopen(request, timeout=4) as response:
                    response.read()
            except Exception:
                pass  # Cancellation closes the gateway before the blocked request can reply.
        client = threading.Thread(target=send)
        clients.append(client)
        client.start()
        assert entered.wait(1)
        # Concurrent retries must be rejected before reaching the provider.
        send()
        raise KeyboardInterrupt()
    monkeypatch.setattr(tools, '_execute', execute)
    task = {'id': 'one', 'messages': [{'role': 'user', 'content': 'Say OK'}]}
    start = time.monotonic()
    try:
        with pytest.raises(KeyboardInterrupt):
            tools.attempt('inspect', provider, 'openai', 'model', task, tmp_path, {})
        assert time.monotonic() - start < .8
        assert len(calls) == 1
        clients[0].join(timeout=.2)
        assert not clients[0].is_alive(), 'gateway connection remained open after cancellation'
    finally:
        release.set()
        for client in clients:
            client.join(timeout=3)


def test_harness_timeout_terminates_spawned_descendants(tmp_path):
    from agentops.benchmarks.tools import _execute
    from agentops.process import _started
    pidfile = tmp_path / 'child.pid'
    script = ('import subprocess,sys,time; from pathlib import Path; '
              'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
              'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
    with (tmp_path / 'log').open('wb') as log:
        with pytest.raises(subprocess.TimeoutExpired):
            _execute([sys.executable, '-c', script, str(pidfile)], tmp_path, dict(os.environ), log, timeout=2)
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _started(child)[0] == 'live' and time.monotonic() < deadline:
        time.sleep(.02)
    assert _started(child)[0] == 'missing'


def test_readiness_probes_are_cached_and_report_missing_inspect_transport(monkeypatch):
    from agentops.benchmarks import tools
    calls = []
    def probe(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='0.3.292\n')
    monkeypatch.setattr(tools.subprocess, 'run', probe)
    config = {'tool_python': 'unique-missing-transport-runtime'}
    assert not tools.available('inspect', config)['available']
    assert not tools.available('inspect', config)['available']
    assert len(calls) == 1


def test_unavailable_tool_is_honest_and_does_not_install(tmp_path):
    from agentops.benchmarks.tools import available
    state = available('inspect', {'python': str(tmp_path / 'missing-python')})
    assert state['available'] is False
    assert state['version'] == '0.3.292'
    assert state['reason']
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('tool,protocol,changes', [
    ('inspect', 'anthropic', {}), ('aiperf', 'openai', {'stream': True}),
    ('aiperf', 'openai', {'messages': [{'role': 'system', 'content': 'secret'}]}),
    ('inspect', 'openai', {'tools': []}),
])
def test_unsupported_combinations_never_call_provider(tmp_path, tool, protocol, changes):
    from agentops.benchmarks.tools import attempt
    def forbidden(*args):
        pytest.fail('unsupported task reached provider')
    task = dict({'id': 'one', 'messages': [{'role': 'user', 'content': 'Say OK'}], 'expected': 'OK'}, **changes)
    with pytest.raises(ValueError):
        attempt(tool, forbidden, protocol, 'model', task, tmp_path, {})


@pytest.mark.parametrize('tool', ['inspect', 'aiperf'])
def test_genuine_tool_executes_exactly_one_request_and_preserves_provider_evidence(tmp_path, tool):
    from agentops.benchmarks.tools import attempt, available
    python = os.environ.get('BENCHMARK_TOOL_PYTHON')
    if not python:
        pytest.skip('set BENCHMARK_TOOL_PYTHON to a pinned tool environment')
    config = {'python': python}
    assert available(tool, config)['available']
    calls = []
    def provider(path, body):
        calls.append((path, body))
        return {'id': 'controlled', 'object': 'chat.completion', 'created': 1,
                'model': 'observed-provider-model',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 17, 'completion_tokens': 2, 'total_tokens': 19}}
    task = {'id': 'one', 'messages': [{'role': 'user', 'content': 'Say OK'}], 'expected': 'OK', 'max_tokens': 12}
    result = attempt(tool, provider, 'openai', 'requested-model', task, tmp_path, config)
    assert len(calls) == 1
    assert calls[0] == ('/v1/chat/completions', {'model': 'requested-model', 'messages': task['messages'], 'temperature': 0, 'max_tokens': 12, 'stream': False})
    assert result['tokens'] == 17
    assert result['response_model'] == 'observed-provider-model'
    assert result['success'] is True
    assert result['latency_ms'] >= 0
    assert result['tool_metrics']['tool'] == tool
    assert 'ttft' not in result['tool_metrics']
    assert list(tmp_path.rglob('*'))


@pytest.mark.parametrize('tool', ['inspect', 'aiperf'])
def test_genuine_tool_failure_never_retries_the_upstream_request(tmp_path, tool):
    from agentops.benchmarks.tools import attempt
    python = os.environ.get('BENCHMARK_TOOL_PYTHON')
    if not python:
        pytest.skip('set BENCHMARK_TOOL_PYTHON to a pinned tool environment')
    calls = []
    def provider(*args):
        calls.append(args)
        raise ValueError('private-provider-credential-must-never-be-returned')
    task = {'id': 'one', 'messages': [{'role': 'user', 'content': 'Say OK'}], 'expected': 'OK'}
    with pytest.raises(ValueError, match='private harness diagnostics') as error:
        attempt(tool, provider, 'azure', 'requested-model', task, tmp_path, {'tool_python': python})
    assert len(calls) == 1
    assert 'private-provider-credential' not in str(error.value)
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert b'private-provider-credential' not in path.read_bytes()
