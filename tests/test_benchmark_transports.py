import argparse
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from benchmark_fixture import build_fixture
from test_benchmark_core import manifest
from test_benchmark_runner import Adapter

from agentops.benchmarks.core import digest
from agentops.benchmarks.docker_transport import collect_docker
from agentops.benchmarks.runner import run_experiment, write_evidence
from agentops.benchmarks.transports import (
    _copy_results,
    collect_remote,
    docker_command,
    execute_transport,
    ssh_command,
    stage_inputs,
)


def args(tmp_path):
    artifact, _ = build_fixture(tmp_path)
    return argparse.Namespace(
        command="inspect",
        artifact=str(artifact),
        config=None,
        sha256="sha256:" + "a" * 64,
        seed=42,
        output=str(tmp_path / "out"),
        host="bench@example.com",
        ssh_port=22,
        ssh_config=None,
        cpus=2,
        memory_gib=4,
        env=["OPENAI_API_KEY"],
        image="image",
        lane="simulator",
    )


def test_ssh_command_preserves_remote_argument_boundaries(tmp_path):
    config = args(tmp_path)
    command = ssh_command(config, ["python3", "-c", "print('hello; $HOME')"])
    assert command[-2] == "bench@example.com"
    assert command[-1].startswith("python3 -c ")
    assert "BatchMode=yes" in command
    config.host = "-oProxyCommand=unsafe"
    with pytest.raises(ValueError):
        ssh_command(config, ["true"])


def test_stage_contains_only_worker_and_explicit_inputs(tmp_path):
    config = args(tmp_path)
    (tmp_path / "secret.txt").write_text("secret")
    stage = tmp_path / "stage"
    inner = stage_inputs(config, stage)
    assert (stage / Path(config.artifact).name).exists()
    assert not (stage / "secret.txt").exists()
    assert inner[0] == "inspect"
    assert "--transport" not in inner
    assert (stage / "agentops/benchmarks/cli.py").exists()


def test_two_build_staging_keeps_same_named_wheels_separate(tmp_path):
    config=args(tmp_path)
    directory=tmp_path/'baseline';directory.mkdir()
    baseline,_=build_fixture(directory)
    with zipfile.ZipFile(baseline,'a') as wheel:
        wheel.writestr('headroom/build_variant.py','VALUE = "baseline"\n')
    config.command='run'
    config.baseline_artifact=str(baseline)
    config.baseline_config=None
    config.manifest=str(tmp_path/'comparison.json')
    Path(config.manifest).write_text(json.dumps({'schema_version':2}))
    stage=tmp_path/'stage'
    inner=stage_inputs(config,stage)
    assert (stage/Path(config.artifact).name).read_bytes()!= (stage/'baseline'/baseline.name).read_bytes()
    assert inner[inner.index('--baseline-artifact')+1]=='baseline/'+baseline.name
    assert (stage/'baseline/runner.json').is_file()


def test_docker_command_pins_image_and_enforces_limits(tmp_path):
    config = args(tmp_path)
    command = docker_command(config, tmp_path, "sha256:" + "a" * 64, ["inspect"])
    assert "--cpus" in command and "--memory" in command
    assert command[-2:] == ["sha256:" + "a" * 64, "inspect"]
    assert "OPENAI_API_KEY" in command
    assert not any("OPENAI_API_KEY=" in token for token in command)
    config.cpus = float("nan")
    with pytest.raises(ValueError):
        docker_command(config, tmp_path, "sha256:" + "a" * 64, ["inspect"])


def test_collection_never_reuses_a_stale_local_pass(tmp_path, monkeypatch):
    (tmp_path / "summary.json").write_text(json.dumps({"status": "pass"}))
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(
            "worker.json", json.dumps({"status": "running", "observed_running": True})
        )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=archive.getvalue()),
    )
    receipt = {
        "host": "bench@host",
        "ssh_port": 22,
        "ssh_config": None,
        "remote_path": "/tmp/headroom-benchmark-" + "a" * 32,
    }
    assert collect_remote(receipt, tmp_path) == 75


def test_summary_without_fresh_raw_evidence_is_rejected(tmp_path):
    old, incoming = tmp_path / "old", tmp_path / "incoming"
    old.mkdir()
    incoming.mkdir()
    result = run_experiment(manifest(), Adapter())
    raw = write_evidence(result, old)
    summary = {
        "status": "pass",
        "result_file": raw.name,
        "result_sha256": digest(result),
    }
    (incoming / "summary.json").write_text(json.dumps(summary))
    with pytest.raises(ValueError):
        _copy_results(incoming, old)


def test_zero_exit_docker_without_results_is_inconclusive(tmp_path, monkeypatch):
    config = args(tmp_path)
    config.transport = "docker"

    def run(command, **kwargs):
        if command[:3] == ["docker", "container", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="No such container")
        return SimpleNamespace(returncode=0, stdout="sha256:" + "a" * 64 + "\n")

    monkeypatch.setattr("subprocess.run", run)
    assert execute_transport(config) == 2
    record = json.loads((Path(config.output) / "transport.json").read_text())
    assert record["status"] == "inconclusive"


def test_docker_cancellation_stops_owned_container_and_preserves_receipt(
    tmp_path, monkeypatch
):
    config = args(tmp_path)
    config.transport = "docker"
    seen = []

    def run(command, **kwargs):
        seen.append(command)
        if command[:2] == ["docker", "run"]:
            raise KeyboardInterrupt()
        if command[:3] == ["docker", "container", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="No such container")
        return SimpleNamespace(returncode=0, stdout="sha256:" + "a" * 64 + "\n")

    monkeypatch.setattr("subprocess.run", run)
    assert execute_transport(config) == 2
    receipt = json.loads((Path(config.output) / "docker-receipt.json").read_text())
    assert receipt["cancelled"] is True
    assert Path(receipt["stage"]).exists()
    assert any(
        command[:2] == ["docker", "stop"] and command[-1] == receipt["container_name"]
        for command in seen
    )


def test_docker_collection_of_live_worker_never_restarts_it(tmp_path, monkeypatch):
    calls = []
    image_id = "sha256:" + "a" * 64

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"Image": image_id, "State": {"Running": True}}]),
        )

    monkeypatch.setattr("subprocess.run", run)
    stage = tmp_path / (".docker-" + "a" * 32)
    stage.mkdir()
    receipt = {
        "kind": "docker",
        "container_name": "headroom-benchmark-" + "a" * 32,
        "image_digest": image_id,
        "stage": str(stage),
        "command": "run",
        "cancelled": False,
    }
    assert collect_docker(receipt, tmp_path) == 75
    assert all(command[:3] == ["docker", "container", "inspect"] for command in calls)


def test_ssh_cancellation_signals_only_receipted_worker_and_retains_handle(
    tmp_path, monkeypatch
):
    config = args(tmp_path)
    config.transport = "ssh"
    calls = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(
            "worker.json", json.dumps({"status": "terminal", "observed_running": False})
        )

    def run(command, **kwargs):
        calls.append(command)
        remote = command[-1]
        if "/remote_worker.py inspect" in remote:
            raise KeyboardInterrupt()
        if "sys.stdout.buffer.write" in remote:
            return SimpleNamespace(returncode=0, stdout=archive.getvalue())
        return SimpleNamespace(returncode=0, stdout=b"")

    monkeypatch.setattr("subprocess.run", run)
    assert execute_transport(config) == 2
    receipt = json.loads((Path(config.output) / "remote-receipt.json").read_text())
    assert receipt["cancelled"] is True
    signals = [
        command[-1] for command in calls if "os.kill(pid,signal.SIGTERM)" in command[-1]
    ]
    assert len(signals) == 1
    assert "cmdline" in signals[0] and receipt["remote_path"] in signals[0]


def test_noncanonical_raw_bytes_are_rejected(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    result = run_experiment(manifest(), Adapter())
    raw = write_evidence(result, incoming)
    raw.write_text(json.dumps(result, indent=2), encoding="utf-8")
    with pytest.raises(ValueError):
        _copy_results(incoming, tmp_path / "output")
