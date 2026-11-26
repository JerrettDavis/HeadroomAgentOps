import threading
import time
from pathlib import Path
import pytest

from agentops.benchmarks import load

PYTHON = Path(__file__).resolve().parents[1] / '.agentops/tool-adapters/Scripts/python.exe'

@pytest.mark.parametrize('concurrency', [2, 4])
def test_real_pinned_sweep_exact_count_and_parallelism(tmp_path, concurrency):
    if not PYTHON.exists():
        pytest.skip('pinned tool environment unavailable')
    lock = threading.Lock()
    active = peak = calls = 0
    def request(path, body):
        nonlocal active, peak, calls
        assert path == '/v1/chat/completions'
        assert body['model'] == 'actual-model'
        with lock:
            calls += 1
            active += 1
            peak = max(peak, active)
        time.sleep(.15)
        with lock:
            active -= 1
        return {'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 1,
                'model': 'provider-model', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 3, 'completion_tokens': 1, 'total_tokens': 4}}
    result = load.run(request, 'openai', 'actual-model',
        {'messages': [{'role': 'user', 'content': 'Say OK'}], 'max_tokens': 8},
        tmp_path, {'python': str(PYTHON)}, concurrency=concurrency, requests=8)
    assert calls == result['completed_requests'] == 8
    assert 1 < peak <= concurrency
    assert result['prompt_tokens'] == 24
    assert result['completion_tokens'] == 8
    assert result['response_models'] == ['provider-model']
    assert result['requests_per_second'] == 8 / result['elapsed_seconds']
    assert 'ttft' not in result
    assert Path(result['native_logs']).is_dir()

@pytest.mark.parametrize('kwargs', [{'requests':1201}, {'concurrency':0}, {'concurrency':65}, {'requests':True}])
def test_load_preflight_bounds_before_calls(tmp_path, kwargs):
    with pytest.raises(ValueError):
        load.run(lambda *a: pytest.fail('upstream called'), 'openai', 'model',
                 {'messages':[{'role':'user','content':'hi'}]}, tmp_path, {}, **kwargs)

def test_retry_cannot_charge_past_budget_and_environment_is_private(tmp_path, monkeypatch):
    import sys
    import json
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from agentops.benchmarks import tools
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-escape')
    monkeypatch.setattr(tools, 'available', lambda *a: {'available':True, 'python':sys.executable})
    calls = []
    task = {'messages':[{'role':'user','content':'hi'}]}
    def execute(command, directory, env, log, cancel, timeout):
        assert 'OPENAI_API_KEY' not in env
        assert 'must-not-escape' not in str(command)
        body = json.dumps({'model':'studio-model', 'messages':task['messages'], 'stream':False}).encode()
        for index in range(2):
            req = Request(env['STUDIO_BASE_URL']+'/chat/completions', data=body,
                          headers={'Authorization':'Bearer '+env['STUDIO_API_KEY']})
            if index:
                with pytest.raises(HTTPError):
                    urlopen(req, timeout=2)
            else:
                with urlopen(req, timeout=2) as response:
                    response.read()
        return 0
    monkeypatch.setattr(load, '_execute', execute)
    def upstream(*a):
        calls.append(a)
        return {'model':'model','usage':{'prompt_tokens':1,'completion_tokens':1}}
    with pytest.raises(ValueError, match='batch failed'):
        load.run(upstream, 'openai', 'model', task, tmp_path, {})
    assert len(calls) == 1


def test_pre_cancel_and_unsupported_protocol_never_invoke_provider(tmp_path):
    cancelled = threading.Event()
    cancelled.set()
    request = lambda *a: pytest.fail('provider invoked')
    task = {'messages':[{'role':'user','content':'hi'}]}
    with pytest.raises(InterruptedError):
        load.run(request, 'openai', 'model', task, tmp_path, {}, cancel=cancelled)
    with pytest.raises(ValueError, match='OpenAI/Azure'):
        load.run(request, 'anthropic', 'model', task, tmp_path, {})

def test_cancellation_terminates_harness_descendants(tmp_path):
    import os
    import sys
    from agentops.process import _started
    pidfile = tmp_path / 'child.pid'
    script = ('import subprocess,sys,time; from pathlib import Path; '
              'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
              'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
    cancel = threading.Event()
    timer = threading.Timer(2, cancel.set)
    timer.start()
    try:
        with (tmp_path/'log').open('wb') as log, pytest.raises(InterruptedError):
            load._execute([sys.executable, '-c', script, str(pidfile)], tmp_path,
                          dict(os.environ), log, cancel, 30)
    finally:
        timer.cancel()
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _started(child)[0] == 'live' and time.monotonic() < deadline:
        time.sleep(.02)
    assert _started(child)[0] == 'missing'

def test_expected_output_is_scored_from_actual_response(tmp_path, monkeypatch):
    import sys
    import json
    from urllib.request import Request, urlopen
    from agentops.benchmarks import tools
    monkeypatch.setattr(tools, 'available', lambda *a: {'available':True, 'python':sys.executable})
    task = {'messages':[{'role':'user','content':'hi'}], 'expected':'OK'}
    def execute(command, directory, env, log, cancel, timeout):
        req = Request(env['STUDIO_BASE_URL']+'/chat/completions',
            data=json.dumps({'model':'studio-model','messages':task['messages']}).encode(),
            headers={'Authorization':'Bearer '+env['STUDIO_API_KEY']})
        with urlopen(req, timeout=2) as response:
            response.read()
        return 0
    monkeypatch.setattr(load, '_execute', execute)
    result = load.run(lambda *a: {'model':'actual','usage':{'prompt_tokens':3,'completion_tokens':2},
        'choices':[{'message':{'content':'NOT MATCHED'}}]}, 'openai','model',task,tmp_path,{})
    observation = result['attempts'][0]
    assert observation['request_index'] == 0
    assert observation['tokens'] == 3
    assert observation['completion_tokens'] == 2
    assert observation['response_model'] == 'actual'
    assert observation['success'] is False
    assert observation['latency_ms'] >= 0

@pytest.mark.parametrize('changed', [False, True])
def test_local_load_batch_attests_each_actual_observation(tmp_path, monkeypatch, changed):
    from contextlib import contextmanager
    from agentops.benchmarks import local
    adapter = local.LocalAdapter.__new__(local.LocalAdapter)
    adapter.python, adapter.workspace, adapter.config = 'python', tmp_path, {}
    runtime = {'rollout_registry_digest':'registry','rollout_snapshot_digest':'snapshot','optimization_enabled':True}
    @contextmanager
    def context(experiment, arm):
        yield lambda *a: None, runtime
    adapter.runtime = context
    monkeypatch.setattr(local, 'describe_environment', lambda *a: {'environment':'exact'})
    monkeypatch.setattr(local, 'agent_identity', lambda: {'agent':'exact'})
    monkeypatch.setattr(local, 'expected_identity', lambda *a: {'model':'expected'})
    monkeypatch.setattr(local, 'observe_runtime', lambda *a: dict(runtime, optimization_enabled=False) if changed else runtime)
    monkeypatch.setattr(load, 'run', lambda *a, **kw: {'attempts':[{'response_model':'actual','tokens':2,'success':True}]})
    if changed:
        with pytest.raises(ValueError, match='identity changed'):
            adapter.load_batch({'model':'expected'}, {}, 'candidate', concurrency=2, requests=4)
    else:
        batch = adapter.load_batch({'model':'expected'}, {}, 'candidate', concurrency=2, requests=4)
        observation = batch['attempts'][0]
        assert observation['identity']['model'] == 'actual'
        assert observation['identity']['rollout_registry_digest'] == 'registry'
        assert observation['identity']['environment'] == {'environment':'exact'}
        assert observation['optimization_enabled'] is True
