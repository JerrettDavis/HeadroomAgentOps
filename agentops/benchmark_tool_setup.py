"""Explicit, runner-scoped setup for pinned optional benchmark harnesses."""

import hashlib
import io
import json
import re
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from pathlib import Path
from types import SimpleNamespace

from .benchmarks import tools
from .benchmarks.transports import REMOTE_WORKER, ssh_command


_cache = {}
_lock = threading.RLock()


def _validate(tool, runner):
    if not isinstance(tool, str) or tool not in tools.PINS:
        raise ValueError('Unsupported benchmark tool')
    if not isinstance(runner, dict):
        raise ValueError('Runner configuration must be an object')
    transport = runner.get('transport', 'local')
    if transport not in ('local', 'docker', 'ssh'):
        raise ValueError('Unsupported runner transport')
    if transport == 'ssh':
        ssh_command(_ssh_args(runner), ['true'])  # Validate host, port and config only.
    if transport == 'docker' and not isinstance(runner.get('image'), str):
        raise ValueError('Select a Docker image')
    return transport


def _ssh_args(runner):
    return SimpleNamespace(host=runner.get('host'), ssh_port=runner.get('ssh_port', 22),
                           ssh_config=runner.get('ssh_config'))


def _runner_key(runner):
    # Environment values, credentials and proxy configuration never enter receipts.
    selected = {key: runner.get(key) for key in ('transport', 'host', 'ssh_port', 'ssh_config', 'image')}
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest()


def _safe_runner(runner):
    return {key: runner[key] for key in ('transport', 'host', 'ssh_port', 'ssh_config', 'image') if key in runner}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


def _run(command, timeout=30, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                          env=tools._environment(),
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), **kwargs)


def _missing(tool, reason, **extra):
    return {'available': False, 'version': tools.PINS[tool][1], 'reason': reason, **extra}


def _probe_script(tool):
    package, _ = tools.PINS[tool]
    return ('import importlib.metadata as m; print(m.version(' + repr(package) + '))' +
            ('; print(m.version("openai"))' if tool == 'inspect' else ''))


def _probe_result(tool, result, **extra):
    expected = tools.PINS[tool][1] + ('\n3.4.0' if tool == 'inspect' else '')
    success = result.returncode == 0 and result.stdout.strip().replace('\r\n', '\n') == expected
    return {'available': success, 'version': tools.PINS[tool][1],
            'reason': '' if success else 'Pinned tool dependencies are missing on the selected runner', **extra}


def _image(runner, identity=None):
    if identity is None:
        result = _run(['docker', 'image', 'inspect', '--format', '{{.Id}}', runner['image']])
        if result.returncode:
            raise ValueError('Selected image is not available locally; no pull was attempted')
        identity = result.stdout.strip()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', identity):
        raise ValueError('A verified immutable Docker image ID is required')
    return identity


def _mapping(root):
    try:
        return json.loads((Path(root) / 'runtimes.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def runtime(tool, root, runner, image_identity=None, refresh=False):
    """Probe the selected execution host; never install, pull or execute a benchmark."""
    transport = _validate(tool, runner)
    key = (tool, str(Path(root).resolve()), _runner_key(runner), image_identity)
    with _lock:
        cached = _cache.get(key)
        if not refresh and cached and time.monotonic() - cached[0] < 10:
            return dict(cached[1])
    try:
        if transport == 'local':
            if refresh:
                tools._probe.cache_clear()
            base = Path(root) / (tool + '-' + tools.PINS[tool][1])
            candidates = [base / 'Scripts/python.exe', base / 'bin/python']
            python = next((str(p) for p in candidates if p.is_file()), sys.executable)
            result = tools.available(tool, {'python': python})
        elif transport == 'docker':
            identity = _image(runner, image_identity)
            result = _probe_result(tool, _run([
                'docker', 'run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
                '--cpus', '2', '--memory', '4g', '--pids-limit', '256',
                '--entrypoint', 'python', identity, '-I', '-c', _probe_script(tool)]), image_identity=identity)
        else:
            managed = _mapping(root).get(_runner_key(runner), {}).get(tool, {})
            python = managed.get('python')
            if python is None:
                script = ('import json,pathlib,subprocess,sys; p=pathlib.Path.home()/'
                    '".cache/headroom-agentops-tools"/' + repr(tool + '-' + tools.PINS[tool][1]) +
                    '/"bin/python"; python=str(p) if p.is_file() else sys.executable; '
                    'r=subprocess.run([python,"-I","-c",sys.argv[1]],capture_output=True,text=True,timeout=20); '
                    'print(json.dumps({"python":python,"returncode":r.returncode,"stdout":r.stdout}))')
                observed = _run(ssh_command(_ssh_args(runner), ['python3', '-I', '-c', script, _probe_script(tool)]))
                if observed.returncode:
                    raise ValueError('Remote discovery failed')
                discovered = json.loads(observed.stdout)
                python = discovered['python']
                result = _probe_result(tool, subprocess.CompletedProcess([], discovered['returncode'],
                                                                         discovered['stdout']), python=python)
            else:
                result = _probe_result(tool, _run(ssh_command(_ssh_args(runner),
                    [python, '-I', '-c', _probe_script(tool)])), python=python)
            if not isinstance(python, str) or '\n' in python:
                raise ValueError('Invalid managed runtime')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        result = _missing(tool, 'Selected runner could not be probed; setup was not attempted')
    with _lock:
        _cache[key] = (time.monotonic(), dict(result))
    return result


def setup(spec, root, directory):
    """Explicit setup job. Docker returns a NEW image; SSH receipts permit recovery."""
    if not isinstance(spec, dict):
        raise ValueError('Setup specification must be an object')
    tool, runner = spec.get('tool'), spec.get('runner', {})
    transport = _validate(tool, runner)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if transport == 'local':
        with _lock:
            result = tools.prepare(tool, Path(root))
            _cache.clear()
        return result
    if transport == 'docker':
        return _setup_docker(tool, runner, root, directory)
    return _setup_ssh(tool, runner, root, directory)


def _setup_docker(tool, runner, root, directory):
    container = None
    receipt_path = directory / 'tool-setup-receipt.json'
    receipt = {'kind': 'docker-tool-setup', 'tool': tool, 'runner': _safe_runner(runner)}
    try:
        identity = _image(runner)
        name = 'headroom-tool-setup-' + uuid.uuid4().hex
        receipt.update(image_identity=identity, container_name=name)
        _write(receipt_path, receipt)
        requirements = [tools.PINS[tool][0] + '==' + tools.PINS[tool][1]]
        if tool == 'inspect':
            requirements.append(tools.INSPECT_TRANSPORT)
        script = ('import os,subprocess,sys; safe={k:v for k,v in os.environ.items() '
                  'if k in ("PATH","HOME","LANG","TMPDIR")}; os.environ.clear(); os.environ.update(safe); '
                  'subprocess.run([sys.executable,"-m","pip","install",'
                  '"--disable-pip-version-check",' + ','.join(repr(r) for r in requirements) +
                  '],check=True); ' + _probe_script(tool))
        created = _run(['docker', 'create', '--name', name, '--label', 'agentops.tool-setup=' + name,
                        '--cpus', '2', '--memory', '4g', '--pids-limit', '256', '--pull', 'never',
                        '--entrypoint', 'python', identity, '-I', '-c', script])
        if created.returncode or not re.fullmatch(r'[a-f0-9]{64}', created.stdout.strip()):
            return _missing(tool, 'Could not create owned setup container')
        container = created.stdout.strip()
        receipt['container_id'] = container
        _write(receipt_path, receipt)
        _run(['docker', 'start', '--attach', container], timeout=900)
        observed = _run(['docker', 'container', 'inspect', container])
        if observed.returncode:
            return _missing(tool, 'Owned setup exit could not be verified')
        state = json.loads(observed.stdout)[0]
        if (state.get('Id') != container or state.get('Image') != identity or
                state['State'].get('Running') or state['State'].get('ExitCode') != 0 or
                state['State'].get('OOMKilled') or state['State'].get('Error')):
            return _missing(tool, 'Setup did not finish successfully; original image is unchanged')
        committed = _run(['docker', 'commit', container], timeout=120)
        if committed.returncode:
            return _missing(tool, 'Prepared image could not be committed')
        prepared = _image(runner, committed.stdout.strip())
        receipt['prepared_image_identity'] = prepared
        _write(receipt_path, receipt)
        return runtime(tool, root, runner, prepared, refresh=True)
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return _missing(tool, 'Setup failed or timed out; original image is unchanged')
    finally:
        if not container and receipt.get('container_name'):
            try:
                observed = _run(['docker', 'container', 'inspect', receipt['container_name']])
                owned = json.loads(observed.stdout)[0] if observed.returncode == 0 else {}
                if (owned.get('Image') == receipt.get('image_identity') and
                        owned.get('Config', {}).get('Labels', {}).get('agentops.tool-setup') == receipt['container_name'] and
                        re.fullmatch(r'[a-f0-9]{64}', owned.get('Id', ''))):
                    container = owned['Id']
            except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
                pass
        if container:
            try:
                _run(['docker', 'rm', '--force', container])
            except (OSError, subprocess.SubprocessError):
                pass  # Persisted owned ID permits manual cleanup; never target another container.


def _setup_archive():
    stream = io.BytesIO()
    worker = REMOTE_WORKER
    # Reuse durable supervision, without loading remote benchmark provider keys.
    start = worker.index('        credentials = ')
    end = worker.index('        os.environ["HEADROOM_BENCH_RUNNER"]', start)
    worker = worker[:start] + worker[end:]
    worker = worker.replace('        os.environ["HEADROOM_BENCH_RUNNER"] = "ssh"',
        '        safe_env = {k:v for k,v in os.environ.items() if k in '
        '("PATH","HOME","LANG","LC_ALL","TMPDIR")}\n'
        '        os.environ.clear()\n        os.environ.update(safe_env)')
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('agentops/__init__.py', '')
        for source in Path(tools.__file__).parent.glob('*.py'):
            archive.write(source, 'agentops/benchmarks/' + source.name)
        archive.writestr('remote_worker.py', worker)
    return stream.getvalue()


def _setup_ssh(tool, runner, root, directory):
    receipt_path = directory / 'tool-setup-receipt.json'
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text(encoding='utf-8'))
        if previous.get('tool') != tool or _runner_key(previous.get('runner', {})) != _runner_key(runner):
            raise ValueError('Setup job already belongs to a different tool or runner')
        return recover(receipt_path, root, directory)
    remote_path = '/tmp/headroom-benchmark-' + uuid.uuid4().hex
    receipt = {'kind': 'ssh-tool-setup', 'tool': tool, 'runner': _safe_runner(runner),
               'remote_path': remote_path, 'status': 'staging'}
    _write(receipt_path, receipt)
    script = ('import io,pathlib,sys,zipfile; p=pathlib.Path(sys.argv[1]); p.mkdir(mode=0o700); '
              'z=zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())); '
              'assert all((p/n).resolve().is_relative_to(p) for n in z.namelist()); z.extractall(p)')
    try:
        staged = _run(ssh_command(_ssh_args(runner), ['python3', '-c', script, remote_path]),
                      timeout=120, input=_setup_archive().decode('latin1'), encoding='latin1')
        if staged.returncode:
            return _missing(tool, 'Remote setup staging failed', receipt_path=str(receipt_path))
        receipt['status'] = 'launch_requested'
        _write(receipt_path, receipt)
        _run(ssh_command(_ssh_args(runner), ['python3', remote_path + '/remote_worker.py',
            'prepare-local-tool', '--tool', tool, '--root', '~/.cache/headroom-agentops-tools',
            '--output', 'results']), timeout=960)
    except (OSError, subprocess.SubprocessError):
        return _missing(tool, 'Remote observation lost; collect this receipt without restarting setup',
                        status='inconclusive', receipt_path=str(receipt_path))
    return recover(receipt_path, root, directory)


def recover(receipt_path, root, directory):
    """Collect an already launched SSH setup. Never dispatch another installation."""
    receipt = json.loads(Path(receipt_path).read_text(encoding='utf-8'))
    tool, runner = receipt.get('tool'), receipt.get('runner', {})
    _validate(tool, runner)
    remote_path = receipt.get('remote_path', '')
    if receipt.get('kind') != 'ssh-tool-setup' or not re.fullmatch(r'/tmp/headroom-benchmark-[a-f0-9]{32}', remote_path):
        raise ValueError('Not an owned SSH setup receipt')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    copied = directory / 'tool-setup-receipt.json'
    _write(copied, receipt)
    script = ('import json,pathlib,sys; p=pathlib.Path(sys.argv[1])/"results"; '
              'print(json.dumps({n:json.loads((p/(n+".json")).read_text()) '
              'if (p/(n+".json")).is_file() else None for n in ("worker","tool")}))')
    try:
        observed = _run(ssh_command(_ssh_args(runner), ['python3', '-c', script, remote_path]))
        if observed.returncode:
            raise ValueError('Remote observation failed')
        value = json.loads(observed.stdout)
        worker, result = value.get('worker') or {}, value.get('tool') or {}
        if worker.get('status') != 'terminal':
            return _missing(tool, 'Remote setup is still running or unobservable; collect again',
                            status='inconclusive', receipt_path=str(copied))
        if (worker.get('exit_code') != 0 or worker.get('cancelled') or worker.get('error') or
                result.get('tool') != tool or result.get('available') is not True or
                result.get('version') != tools.PINS[tool][1] or not isinstance(result.get('python'), str)):
            return _missing(tool, 'Remote setup did not produce verified success', status='failed', receipt_path=str(copied))
        probe = _run(ssh_command(_ssh_args(runner), [result['python'], '-I', '-c', _probe_script(tool)]))
        verified = _probe_result(tool, probe, python=result['python'])
        if verified['available']:
            with _lock:
                mapping = _mapping(root)
                mapping.setdefault(_runner_key(runner), {})[tool] = verified
                _write(Path(root) / 'runtimes.json', mapping)
                _cache.clear()
            _write(directory / 'tool.json', {**verified, 'tool': tool})
        return {**verified, 'receipt_path': str(copied), 'status': 'completed' if verified['available'] else 'failed'}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return _missing(tool, 'Remote observation lost; collect this receipt without restarting setup',
                        status='inconclusive', receipt_path=str(copied))
