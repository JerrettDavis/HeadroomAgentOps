import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from test_benchmark_core import manifest
from test_benchmark_runner import Adapter

from agentops.benchmarks.core import digest
from agentops.benchmarks.docker_transport import collect_docker
from agentops.benchmarks.runner import run_experiment, write_evidence
from agentops.benchmarks.transports import collect_remote


def docker_result(tmp_path):
    data = manifest()
    result = run_experiment(data, Adapter())
    result["lane"] = "live"
    nonce = "a" * 32
    stage = tmp_path / (".docker-" + nonce)
    source = stage / "results"
    raw = write_evidence(result, source)
    (source / "summary.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "result_file": raw.name,
                "result_sha256": digest(result),
                "lane": "live",
            }
        )
    )
    receipt = {
        "kind": "docker",
        "container_name": "headroom-benchmark-" + nonce,
        "stage": str(stage),
        "image_digest": "sha256:" + "b" * 64,
        "command": "run",
        "cancelled": False,
        "manifest_digest": digest(data),
    }
    return receipt


@pytest.mark.parametrize("code,oom", [(137, True), (1, False), (0, True)])
def test_failed_container_cannot_qualify_passing_measurements(
    tmp_path, monkeypatch, code, oom
):
    receipt = docker_result(tmp_path)

    def run(command, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": "c" * 64,
                        "Image": receipt["image_digest"],
                        "State": {
                            "Status": "exited",
                            "Running": False,
                            "ExitCode": code,
                            "OOMKilled": oom,
                            "Error": "",
                        },
                    }
                ]
            ),
        )

    monkeypatch.setattr("subprocess.run", run)
    assert collect_docker(receipt, tmp_path) == 2
    summary = json.loads((tmp_path / "summary.json").read_text())
    raw = json.loads((tmp_path / summary["result_file"]).read_text())
    assert raw["decision"]["status"] == "inconclusive"
    assert raw["execution"]["exit_code"] == code


def test_missing_container_without_exit_receipt_cannot_qualify(tmp_path, monkeypatch):
    receipt = docker_result(tmp_path)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stderr="No such container", stdout=""
        ),
    )
    assert collect_docker(receipt, tmp_path) == 2


def test_verified_container_exit_is_durable_after_container_removal(
    tmp_path, monkeypatch
):
    receipt = docker_result(tmp_path)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Id": "c" * 64,
                        "Image": receipt["image_digest"],
                        "State": {
                            "Status": "exited",
                            "Running": False,
                            "ExitCode": 0,
                            "OOMKilled": False,
                            "Error": "",
                        },
                    }
                ]
            ),
        )

    monkeypatch.setattr("subprocess.run", run)
    assert collect_docker(receipt, tmp_path) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert any(command == ["docker", "rm", "c" * 64] for command in calls)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: pytest.fail(
            "verified terminal collection must not restart or require the removed container"
        ),
    )
    assert collect_docker(tmp_path / "docker-receipt.json", tmp_path) == 0
    assert json.loads((tmp_path / "summary.json").read_text()) == summary


@pytest.mark.parametrize("exit_code,expected", [(0, 0), (137, 2), (None, 2)])
def test_ssh_requires_successful_supervised_exit(
    tmp_path, monkeypatch, exit_code, expected
):
    data = manifest()
    result = run_experiment(data, Adapter())
    result["lane"] = "live"
    raw = write_evidence(result, tmp_path / "source")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr(raw.name, raw.read_bytes())
        package.writestr(
            "summary.json",
            json.dumps(
                {
                    "status": "pass",
                    "result_file": raw.name,
                    "result_sha256": digest(result),
                    "lane": "live",
                }
            ),
        )
        package.writestr(
            "worker.json",
            json.dumps(
                {
                    "status": "terminal",
                    "exit_code": exit_code,
                    "error": False,
                    "observed_running": False,
                }
            ),
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
    assert collect_remote(receipt, tmp_path / "output") == expected
