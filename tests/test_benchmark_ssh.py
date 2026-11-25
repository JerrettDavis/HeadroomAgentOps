import json
import os
import subprocess
import time
from types import SimpleNamespace

import pytest
from benchmark_fixture import build_fixture
from test_benchmark_core import manifest

from agentops.benchmarks.cli import main
from agentops.benchmarks.transports import ssh_command


@pytest.mark.skipif(
    not os.environ.get("HEADROOM_BENCH_SSH_IMAGE"),
    reason="set HEADROOM_BENCH_SSH_IMAGE for a real disposable Linux SSH server",
)
def test_ssh_staging_execution_and_collection_use_same_run(tmp_path, monkeypatch):
    key = tmp_path / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    image = os.environ["HEADROOM_BENCH_SSH_IMAGE"]
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--cpus",
            "2",
            "--memory",
            "4g",
            "-p",
            "127.0.0.1::22",
            image,
        ],
        text=True,
    ).strip()
    try:
        subprocess.run(
            [
                "docker",
                "cp",
                str(key.with_suffix(".pub")),
                container + ":/root/.ssh/authorized_keys",
            ],
            check=True,
        )
        # docker cp retains source mode bits; hosted Linux runners may create
        # group-writable public keys, which sshd correctly rejects.
        subprocess.run(
            [
                "docker",
                "exec",
                container,
                "chown",
                "root:root",
                "/root/.ssh/authorized_keys",
            ],
            check=True,
        )
        subprocess.run(
            ["docker", "exec", container, "chmod", "600", "/root/.ssh/authorized_keys"],
            check=True,
        )
        port = (
            subprocess.check_output(["docker", "port", container, "22/tcp"], text=True)
            .strip()
            .rsplit(":", 1)[1]
        )
        public = subprocess.check_output(
            ["docker", "exec", container, "cat", "/etc/ssh/ssh_host_ed25519_key.pub"],
            text=True,
        ).split()
        known = tmp_path / "known_hosts"
        known.write_text(
            f"[127.0.0.1]:{port} {public[0]} {public[1]}\n", encoding="utf-8"
        )
        ssh_config = tmp_path / "ssh_config"
        ssh_config.write_text(
            f'Host *\n  IdentityFile "{key.as_posix()}"\n  UserKnownHostsFile "{known.as_posix()}"\n  IdentitiesOnly yes\n  StrictHostKeyChecking yes\n',
            encoding="utf-8",
        )
        artifact, sha256 = build_fixture(tmp_path)
        config = tmp_path / "runner.json"
        config.write_text(
            json.dumps({"wheelhouse": str(tmp_path), "startup_timeout": 15}),
            encoding="utf-8",
        )
        transport = [
            "--transport",
            "ssh",
            "--host",
            "root@127.0.0.1",
            "--ssh-port",
            port,
            "--ssh-config",
            str(ssh_config),
        ]
        # Container creation/host-key availability do not prove sshd accepts
        # authenticated sessions yet, especially on fast Linux CI hosts.
        connection = SimpleNamespace(
            host="root@127.0.0.1", ssh_port=int(port), ssh_config=str(ssh_config)
        )
        deadline = time.monotonic() + 30
        while True:
            ready = subprocess.run(
                ssh_command(
                    connection,
                    ["python3", "-c", "import sys; print(sys.version_info[:2])"],
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            if time.monotonic() >= deadline:
                server_log = subprocess.run(
                    ["docker", "logs", container], capture_output=True, text=True
                )
                pytest.fail(ready.stderr + server_log.stdout + server_log.stderr)
            time.sleep(0.2)
        inspection_dir = tmp_path / "inspection"
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
        assert inspection["environment"]["runner"] == "ssh"
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
        # Collection is idempotent and uses the stored remote handle.
        assert (
            main(
                [
                    "collect",
                    str(output / "remote-receipt.json"),
                    "--output",
                    str(output),
                ]
            )
            == 2
        )
        assert json.loads((output / "summary.json").read_text()) == summary
        runner = json.loads(config.read_text())
        runner["proxy_args"] = ["--fixture-delay", "30"]
        config.write_text(json.dumps(runner), encoding="utf-8")
        cancelled = tmp_path / "cancelled"
        original_run = subprocess.run
        clients = []
        connection = SimpleNamespace(
            host="root@127.0.0.1", ssh_port=int(port), ssh_config=str(ssh_config)
        )

        def interrupted(command, **kwargs):
            if command[0] != "ssh" or "/remote_worker.py run" not in command[-1]:
                return original_run(command, **kwargs)
            client = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            clients.append(client)
            remote = json.loads((cancelled / "remote-receipt.json").read_text())[
                "remote_path"
            ]
            script = "import pathlib,sys;print(bool(list(pathlib.Path(sys.argv[1]).rglob('request-started'))))"
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                probe = original_run(
                    ssh_command(connection, ["python3", "-c", script, remote]),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if probe.stdout.strip() == "True":
                    raise KeyboardInterrupt()
                assert client.poll() is None, (
                    "remote worker exited before cancellation point"
                )
                time.sleep(0.2)
            raise AssertionError("remote worker never reached cancellation point")

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
                        str(cancelled / "remote-receipt.json"),
                        "--output",
                        str(cancelled),
                    ]
                )
                == 2
            )
        finally:
            for client in clients:
                client.wait(timeout=30)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], check=True, stdout=subprocess.DEVNULL
        )
