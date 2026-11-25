import json
import os
import subprocess
import time

import pytest
from benchmark_fixture import build_fixture
from test_benchmark_core import manifest

from agentops.benchmarks.cli import main


@pytest.mark.skipif(
    not os.environ.get("HEADROOM_BENCH_DOCKER_IMAGE"),
    reason="set HEADROOM_BENCH_DOCKER_IMAGE to test real Docker execution",
)
def test_docker_runs_the_same_worker_with_observed_resource_limits(
    tmp_path, monkeypatch
):
    image = os.environ["HEADROOM_BENCH_DOCKER_IMAGE"]
    artifact, sha256 = build_fixture(tmp_path)
    config = tmp_path / "runner.json"
    config.write_text(
        json.dumps({"wheelhouse": str(tmp_path), "startup_timeout": 15}),
        encoding="utf-8",
    )
    inspection_dir = tmp_path / "inspection"
    transport = [
        "--transport",
        "docker",
        "--image",
        image,
        "--cpus",
        "2",
        "--memory-gib",
        "4",
    ]
    assert (
        main(
            [
                "inspect",
                "--artifact",
                str(artifact),
                "--sha256",
                sha256,
                "--config",
                str(config),
                "--output",
                str(inspection_dir),
                *transport,
            ]
        )
        == 0
    )
    inspection = json.loads((inspection_dir / "inspection.json").read_text())
    assert inspection["environment"]["runner"] == "docker"
    assert inspection["environment"]["resource_limits"] == {
        "cpu": 2.0,
        "memory_gib": 4.0,
    }
    experiment = manifest()
    experiment["candidate"]["artifact_sha256"] = sha256
    experiment["environment"] = inspection["environment"]
    experiment["agent"] = inspection["agent"]
    experiment["policy"]["maximum_latency_ratio"] = 1000
    source = tmp_path / "experiment.json"
    source.write_text(json.dumps(experiment), encoding="utf-8")
    output = tmp_path / "results"
    assert (
        main(
            [
                "run",
                str(source),
                "--artifact",
                str(artifact),
                "--config",
                str(config),
                "--output",
                str(output),
                *transport,
            ]
        )
        == 2
    )
    summary = json.loads((output / "summary.json").read_text())
    result = json.loads((output / summary["result_file"]).read_text())
    assert result["decision"]["simulated_decision"]["status"] == "pass"
    assert result["identity_verification"]["status"] == "pass"
    assert len(result["attempts"]) == 10

    # Interrupt the controller only after a real candidate request is active.
    # Its named container must be stopped and its partial evidence retained.
    runner = json.loads(config.read_text())
    runner["proxy_args"] = ["--fixture-delay", "30"]
    config.write_text(json.dumps(runner), encoding="utf-8")
    cancelled = tmp_path / "cancelled"
    original_run = subprocess.run
    clients = []

    def interrupted(command, **kwargs):
        if command[:2] != ["docker", "run"]:
            return original_run(command, **kwargs)
        client = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        clients.append(client)
        name = command[command.index("--name") + 1]
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            probe = original_run(
                [
                    "docker",
                    "exec",
                    name,
                    "python",
                    "-c",
                    "from pathlib import Path;print(bool(list(Path('/work/results').rglob('request-started'))))",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.stdout.strip() == "True":
                raise KeyboardInterrupt()
            assert client.poll() is None, "worker exited before cancellation point"
            time.sleep(0.2)
        raise AssertionError("worker never reached cancellation point")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "run", interrupted)
            assert (
                main(
                    [
                        "run",
                        str(source),
                        "--artifact",
                        str(artifact),
                        "--config",
                        str(config),
                        "--output",
                        str(cancelled),
                        *transport,
                    ]
                )
                == 2
            )
        cancelled_summary = json.loads((cancelled / "summary.json").read_text())
        cancelled_result = json.loads(
            (cancelled / cancelled_summary["result_file"]).read_text()
        )
        assert cancelled_result["cancelled"] is True
        assert len(cancelled_result["attempts"]) < 10
        assert (
            main(
                [
                    "collect",
                    str(cancelled / "docker-receipt.json"),
                    "--output",
                    str(cancelled),
                ]
            )
            == 2
        )
    finally:
        receipt = cancelled / "docker-receipt.json"
        if receipt.exists():
            name = json.loads(receipt.read_text())["container_name"]
            original_run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        for client in clients:
            client.wait(timeout=30)
