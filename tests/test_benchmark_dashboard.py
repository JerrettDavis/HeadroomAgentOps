import json
import time
from pathlib import Path

import pytest


def service(tmp_path):
    from agentops.benchmark_service import BenchmarkService

    return BenchmarkService(tmp_path / "state" / "ledger.jsonl")


def terminal(s, job):
    for _ in range(1200):
        current = next(j for j in s.status()["jobs"] if j["id"] == job["id"])
        if current["state"] not in ("queued", "running"):
            return current
        time.sleep(0.05)
    pytest.fail("supervisor did not finish")


def test_view_is_idle_and_invalid_requests_create_no_jobs(tmp_path):
    s = service(tmp_path)
    assert s.status()["active"] is False
    for request in (
        {"action": "shell"},
        {"action": "export", "tasks": "missing", "format": "inspect"},
        {"action": "export", "tasks": "x", "format": "inspect", "token": "secret"},
    ):
        with pytest.raises(ValueError):
            s.submit(request)
    assert s.status()["jobs"] == []


def test_real_export_import_survive_service_restart(tmp_path):
    s = service(tmp_path)
    tasks = Path("benchmarks/tasksets/protocol-smoke-v1.json").resolve()
    job = terminal(
        s, s.submit({"action": "export", "tasks": str(tasks), "format": "inspect"})
    )
    assert job["state"] == "completed", job
    assert Path(job["paths"]["dataset"]).is_file()
    job = terminal(
        s,
        s.submit(
            {
                "action": "import",
                "dataset": job["paths"]["dataset"],
                "taskset_id": "roundtrip",
                "revision": "v1",
            }
        ),
    )
    assert job["state"] == "completed", job
    assert (
        json.loads(Path(job["paths"]["dataset"]).read_text())["taskset_id"]
        == "roundtrip"
    )
    assert len(service(tmp_path).status()["jobs"]) == 2
    assert "argv" not in json.dumps(s.status())


def test_single_slot_and_cancel_queued_job(tmp_path):
    s = service(tmp_path)
    job = s.submit(
        {
            "action": "export",
            "tasks": str(Path("benchmarks/tasksets/protocol-smoke-v1.json").resolve()),
            "format": "inspect",
        }
    )
    # Repeated cancellation is safe whether the tiny offline job already ended.
    cancelled = s.cancel({"id": job["id"]})
    assert cancelled["state"] in ("queued", "running", "cancelled", "completed")
    assert terminal(s, job)["state"] in ("cancelled", "completed")


def test_public_status_does_not_return_errors_or_raw_result_secrets(tmp_path):
    s = service(tmp_path)
    job = terminal(
        s,
        s.submit(
            {
                "action": "import",
                "dataset": str(
                    Path("benchmarks/tasksets/protocol-smoke-v1.json").resolve()
                ),
                "taskset_id": "x",
                "revision": "v1",
            }
        ),
    )
    assert job["state"] == "failed"
    assert (
        job["error"]
        == "Benchmark command failed; private diagnostics are retained locally."
    )


def test_rejects_second_instance_while_supervisor_is_queued(tmp_path, monkeypatch):
    s = service(tmp_path)
    # Prevent process startup to exercise the durable queued lock deterministically.
    import agentops.benchmark_service as module

    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)
    request = {
        "action": "export",
        "tasks": str(Path("benchmarks/tasksets/protocol-smoke-v1.json").resolve()),
        "format": "inspect",
    }
    s.submit(request)
    with pytest.raises(ValueError, match="already active"):
        service(tmp_path).submit(request)


def test_create_manifest_uses_real_cli_without_provider_calls(tmp_path):
    from test_benchmark_core import manifest

    from agentops.benchmarks.core import validate_manifest

    s = service(tmp_path)
    original = manifest()
    observed = {
        "artifact_sha256": original["candidate"]["artifact_sha256"],
        "runtime": {
            "rollout_registry_digest": original["rollout"]["registry_digest"],
            "rollout_snapshot_digest": original["rollout"]["snapshot_digest"],
        },
        "agent": original["agent"],
        "environment": original["environment"],
    }
    inspection = tmp_path / "inspection.json"
    inspection.write_text(json.dumps(observed))
    job = terminal(
        s,
        s.submit(
            {
                "action": "create",
                "inspection_path": str(inspection),
                "candidate_sha": "a" * 40,
                "model": "pinned-model",
                "tasks": str(
                    Path("benchmarks/tasksets/protocol-smoke-v1.json").resolve()
                ),
                "policy": str(Path("benchmarks/policies/smoke-v1.json").resolve()),
                "experiment_id": "offline-test",
            }
        ),
    )
    assert job["state"] == "completed", job
    result = json.loads(Path(job["paths"]["manifest"]).read_text())
    validate_manifest(result)
    assert result["candidate"]["artifact_sha256"] == observed["artifact_sha256"]


def test_inspect_hash_is_native_prefixed_digest(tmp_path):
    s = service(tmp_path)
    artifact = tmp_path / "candidate.whl"
    artifact.write_bytes(b"wheel")
    argv, _ = s._argv({"action": "inspect", "artifact": str(artifact)}, s.root / "test")
    import hashlib

    assert (
        argv[argv.index("--sha256") + 1]
        == "sha256:" + hashlib.sha256(b"wheel").hexdigest()
    )


def test_sanitized_metrics_keep_native_inconclusive_and_drop_secrets():
    from agentops.benchmark_service import _decision

    raw = {
        "status": "inconclusive",
        "reason": "secret prompt",
        "simulated_decision": {
            "status": "pass",
            "pairs": 2,
            "mean_savings": 0.2,
            "savings_interval": [0.1, 0.3],
            "policy": {"token": "secret"},
        },
        "token": "secret",
    }
    result = _decision(raw)
    assert result["status"] == "inconclusive"
    assert result["simulated_decision"]["pairs"] == 2
    assert "secret" not in json.dumps(result)


def test_transport_environment_is_names_only(tmp_path):
    s = service(tmp_path)
    artifact = tmp_path / "x.whl"
    artifact.write_bytes(b"x")
    argv, _ = s._argv(
        {
            "action": "inspect",
            "artifact": str(artifact),
            "env": ["OPENAI_API_KEY", "AZURE_TOKEN"],
        },
        s.root / "x",
    )
    assert argv.count("--env") == 2
    with pytest.raises(ValueError):
        s._argv(
            {"action": "inspect", "artifact": str(artifact), "env": ["TOKEN=secret"]},
            s.root / "x",
        )


def test_stale_slot_reclamation_serializes_concurrent_instances(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import agentops.benchmark_service as module

    s = service(tmp_path)
    request = {
        "action": "export",
        "tasks": str(Path("benchmarks/tasksets/protocol-smoke-v1.json").resolve()),
        "format": "inspect",
    }
    finished = terminal(s, s.submit(request))
    s.slot.write_text(finished["id"])
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)
    barrier = threading.Barrier(8)

    def attempt(_):
        barrier.wait()
        try:
            return service(tmp_path).submit(request)["id"]
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = [value for value in pool.map(attempt, range(8)) if value]
    assert len(accepted) == 1
    assert sum(job["state"] == "queued" for job in s.status()["jobs"]) == 1


def test_orphan_with_live_owned_child_keeps_slot_failclosed(tmp_path, monkeypatch):
    import agentops.benchmark_service as module

    s = service(tmp_path)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: None)
    request = {
        "action": "export",
        "tasks": str(Path("benchmarks/tasksets/protocol-smoke-v1.json").resolve()),
        "format": "inspect",
    }
    job = s.submit(request)
    path = s.root / job["id"] / "job.json"
    private = json.loads(path.read_text())
    import os

    private.update(started_epoch=0, supervisor_pid=0, child_pid=os.getpid())
    path.write_text(json.dumps(private))
    assert s.status()["active"] is True
    with pytest.raises(ValueError, match="already active"):
        service(tmp_path).submit(request)


def test_real_offline_inspect_create_run_preserves_simulator_boundary(tmp_path):
    from benchmark_fixture import build_fixture
    from test_benchmark_core import manifest

    artifact, _digest = build_fixture(tmp_path)
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"wheelhouse": str(tmp_path), "startup_timeout": 10}))
    s = service(tmp_path)
    inspection = terminal(
        s,
        s.submit(
            {"action": "inspect", "artifact": str(artifact), "config": str(config)}
        ),
    )
    assert inspection["state"] == "completed", inspection
    taskset = tmp_path / "tasks.json"
    native = manifest()
    taskset.write_text(
        json.dumps(
            {
                "taskset_id": native["taskset_id"],
                "revision": native["taskset_revision"],
                "tasks": native["tasks"],
            }
        )
    )
    policy = tmp_path / "policy.json"
    native["policy"]["maximum_latency_ratio"] = 1000
    policy.write_text(json.dumps(native["policy"]))
    created = terminal(
        s,
        s.submit(
            {
                "action": "create",
                "inspection_id": inspection["id"],
                "candidate_sha": "a" * 40,
                "model": "pinned-model",
                "tasks": str(taskset),
                "policy": str(policy),
                "experiment_id": "fixture-dashboard",
            }
        ),
    )
    assert created["state"] == "completed", created
    result = terminal(
        s,
        s.submit(
            {
                "action": "run",
                "artifact": str(artifact),
                "experiment_id": created["id"],
                "config": str(config),
            }
        ),
    )
    assert result["state"] == "completed", result
    assert result["decision"]["status"] == "inconclusive"
    assert result["decision"]["lane"] == "simulator"
    assert result["decision"]["simulated_decision"]["pairs"] == 5
    assert Path(result["paths"]["result"]).is_file()
    assert "hello" not in json.dumps(s.status())


def test_result_summary_requires_hashed_terminal_evidence(tmp_path):
    from agentops.benchmark_service import _result
    from agentops.benchmarks.core import digest

    evidence = {"decision": {"status": "pass", "pairs": 5}, "lane": "live"}
    raw = json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    result = tmp_path / "result.json"
    result.write_bytes(raw)
    summary = {
        "status": "pass",
        "result_file": result.name,
        "result_sha256": digest(evidence),
        "lane": "live",
    }
    assert _result(tmp_path, summary, 0, False)[0]["status"] == "pass"
    assert _result(tmp_path, summary, 2, False)[0]["status"] == "inconclusive"
    assert _result(tmp_path, summary, 0, True)[0]["status"] == "inconclusive"
    result.write_bytes(raw + b" ")
    assert _result(tmp_path, summary, 0, False) == ({"status": "inconclusive"}, None)
