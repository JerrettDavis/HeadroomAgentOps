import json
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from agentops import benchmark_tool_setup as setup


def test_local_readiness_never_installs(tmp_path, monkeypatch):
    python = tmp_path / 'inspect-0.3.292' / 'bin' / 'python'
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(setup.tools, 'available', lambda tool, config: {
        'available': True, 'version': '0.3.292', 'python': config['python']})
    monkeypatch.setattr(setup.tools, 'prepare', lambda *args: pytest.fail('view installed a tool'))
    assert setup.runtime('inspect', tmp_path, {'transport': 'local'})['python'] == str(python)


def test_docker_readiness_probes_selected_image_without_network_or_credentials(tmp_path, monkeypatch):
    calls = []
    image = 'sha256:' + 'a' * 64
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '0.3.292\n3.4.0\n', '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.runtime('inspect', tmp_path, {'transport': 'docker', 'image': 'selected'}, image)
    assert result['available']
    command, options = calls[0]
    assert command[command.index('--entrypoint') + 1] == 'python'
    assert '--read-only' in command and '--network' in command and 'none' in command
    assert image in command and '--env' not in command and '--mount' not in command
    assert not {'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'AZURE_OPENAI_API_KEY'} & options['env'].keys()


def test_docker_setup_commits_only_success_and_cleans_owned_container(tmp_path, monkeypatch):
    calls = []
    image = 'sha256:' + 'a' * 64
    prepared = 'sha256:' + 'b' * 64
    container = 'c' * 64
    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            value = image
        elif command[:2] == ['docker', 'create']:
            value = container
        elif command[:3] == ['docker', 'container', 'inspect']:
            value = json.dumps([{'Id': container, 'Image': image,
                                'State': {'Running': False, 'ExitCode': 0, 'OOMKilled': False}}])
        elif command[:2] == ['docker', 'commit']:
            value = prepared
        else:
            value = '0.14.0'
        return subprocess.CompletedProcess(command, 0, value, '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.setup({'tool': 'aiperf', 'runner': {'transport': 'docker', 'image': 'original'}},
                         tmp_path / 'tools', tmp_path / 'job')
    assert result['available'] and result['image_identity'] == prepared
    create = next(c for c in calls if c[:2] == ['docker', 'create'])
    assert create[create.index('--cpus') + 1] == '2'
    assert create[create.index('--memory') + 1] == '4g'
    assert image in create and '--mount' not in create and '--env' not in create
    assert ['docker', 'rm', '--force', container] in calls


def test_docker_timeout_never_commits_and_removes_only_owned_id(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(command, 0, 'sha256:' + 'a' * 64, '')
        if command[:2] == ['docker', 'create']:
            return subprocess.CompletedProcess(command, 0, 'c' * 64, '')
        if command[:2] == ['docker', 'start']:
            raise subprocess.TimeoutExpired(command, 900)
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.setup({'tool': 'inspect', 'runner': {'transport': 'docker', 'image': 'original'}},
                         tmp_path / 'tools', tmp_path / 'job')
    assert not result['available']
    assert not any(c[:2] == ['docker', 'commit'] for c in calls)
    assert ['docker', 'rm', '--force', 'c' * 64] in calls


def test_ssh_observation_loss_keeps_receipt_without_restarting(tmp_path, monkeypatch):
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'kind': 'ssh-tool-setup', 'tool': 'inspect',
        'runner': {'transport': 'ssh', 'host': 'worker', 'ssh_port': 22},
        'remote_path': '/tmp/headroom-benchmark-' + 'a' * 32}))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.recover(receipt, tmp_path / 'tools', tmp_path / 'recover')
    assert result['status'] == 'inconclusive'
    assert len(calls) == 1 and 'prepare-local-tool' not in calls[0][-1]
    assert not (tmp_path / 'tools' / 'runtimes.json').exists()


def test_unknown_transport_and_tool_are_rejected_before_subprocess(tmp_path, monkeypatch):
    monkeypatch.setattr(setup.subprocess, 'run', lambda *args, **kw: pytest.fail('dispatched invalid spec'))
    with pytest.raises(ValueError):
        setup.setup({'tool': 'pip', 'runner': {'transport': 'local'}}, tmp_path, tmp_path / 'job')


def test_ssh_archive_has_private_supervisor_without_provider_credential_loading():
    with zipfile.ZipFile(io.BytesIO(setup._setup_archive())) as archive:
        worker = archive.read('remote_worker.py').decode()
        assert 'env.json' not in worker and 'os.environ.clear()' in worker
        assert 'signal.SIGHUP, signal.SIG_IGN' in worker
        assert 'agentops/benchmarks/tools.py' in archive.namelist()
        compile(worker, 'remote_worker.py', 'exec')


def test_ssh_success_is_verified_before_host_scoped_mapping(tmp_path, monkeypatch):
    root = tmp_path / 'tools'
    receipt = tmp_path / 'receipt.json'
    runner = {'transport': 'ssh', 'host': 'worker', 'ssh_port': 22}
    receipt.write_text(json.dumps({'kind': 'ssh-tool-setup', 'tool': 'inspect', 'runner': runner,
                                  'remote_path': '/tmp/headroom-benchmark-' + 'a' * 32}))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            value = json.dumps({'worker': {'status': 'terminal', 'exit_code': 0},
                                'tool': {'tool': 'inspect', 'available': True, 'version': '0.3.292',
                                         'python': '/home/test/.cache/tools/bin/python'}})
        else:
            value = '0.3.292\n3.4.0'
        return subprocess.CompletedProcess(command, 0, value, '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.recover(receipt, root, tmp_path / 'collection')
    assert result['available'] and len(calls) == 2
    mapping = json.loads((root / 'runtimes.json').read_text())
    assert mapping[setup._runner_key(runner)]['inspect']['python'].startswith('/home/test/')
    assert setup._runner_key({**runner, 'host': 'different'}) not in mapping


def test_readiness_explicit_refresh_bypasses_false_cache(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '0.14.0' if len(calls) > 1 else '', '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    runner = {'transport': 'docker', 'image': 'selected'}
    image = 'sha256:' + 'a' * 64
    assert not setup.runtime('aiperf', tmp_path, runner, image)['available']
    assert not setup.runtime('aiperf', tmp_path, runner, image)['available']
    assert setup.runtime('aiperf', tmp_path, runner, image, refresh=True)['available']
    assert len(calls) == 2


def test_ssh_discovers_managed_runtime_on_remote_host_without_install(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            'python': '/home/worker/.cache/headroom-agentops-tools/inspect-0.3.292/bin/python',
            'returncode': 0, 'stdout': '0.3.292\n3.4.0\n'}), '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.runtime('inspect', tmp_path, {'transport': 'ssh', 'host': 'worker'})
    assert result['available'] and result['python'].startswith('/home/worker/')
    assert 'headroom-agentops-tools' in calls[0][-1] and 'pip' not in calls[0][-1]


def test_reentering_ssh_job_collects_existing_receipt_instead_of_reinstalling(tmp_path, monkeypatch):
    directory = tmp_path / 'job'
    directory.mkdir()
    runner = {'transport': 'ssh', 'host': 'worker'}
    receipt = directory / 'tool-setup-receipt.json'
    receipt.write_text(json.dumps({'kind': 'ssh-tool-setup', 'tool': 'inspect', 'runner': runner,
                                  'remote_path': '/tmp/headroom-benchmark-' + 'a' * 32}))
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, 30)
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.setup({'tool': 'inspect', 'runner': runner}, tmp_path / 'tools', directory)
    assert result['status'] == 'inconclusive'
    assert len(calls) == 1 and 'prepare-local-tool' not in calls[0][-1]
    assert json.loads(receipt.read_text())['remote_path'] == '/tmp/headroom-benchmark-' + 'a' * 32


def test_lost_docker_create_response_cleans_only_label_verified_owned_container(tmp_path, monkeypatch):
    calls = []
    name = None
    image = 'sha256:' + 'a' * 64
    container = 'c' * 64
    def run(command, **kwargs):
        nonlocal name
        calls.append(command)
        if command[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(command, 0, image, '')
        if command[:2] == ['docker', 'create']:
            name = command[command.index('--name') + 1]
            raise subprocess.TimeoutExpired(command, 30)
        if command[:3] == ['docker', 'container', 'inspect']:
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                'Id': container, 'Image': image, 'Config': {'Labels': {'agentops.tool-setup': name}}}]), '')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(setup.subprocess, 'run', run)
    result = setup.setup({'tool': 'inspect', 'runner': {'transport': 'docker', 'image': 'original'}},
                         tmp_path / 'tools', tmp_path / 'job')
    assert not result['available']
    assert ['docker', 'rm', '--force', container] in calls
