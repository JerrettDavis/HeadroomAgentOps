import copy
import json

from test_benchmark_core import manifest

from agentops.benchmarks.core import digest
from agentops.benchmarks.runner import run_experiment, write_evidence


class Adapter:
    def __init__(self, drift=False, broken=False):
        self.drift = drift
        self.broken = broken

    def attempt(self, experiment, task, arm):
        if self.broken:
            raise TimeoutError("must not expose provider secret here")
        identity = {
            "candidate_sha": experiment["candidate"]["sha"],
            "artifact_sha256": experiment["candidate"]["artifact_sha256"],
            "rollout_registry_digest": experiment["rollout"]["registry_digest"],
            "rollout_snapshot_digest": experiment["rollout"]["snapshot_digest"],
            "model": experiment["model"],
            "agent": experiment["agent"],
            "taskset_id": experiment["taskset_id"],
            "environment": copy.deepcopy(experiment["environment"]),
        }
        if self.drift:
            identity["model"] = "wrong"
        return {
            "identity": identity,
            "optimization_enabled": arm == "b_headroom",
            "success": True,
            "tokens": 100 if arm == "a1_passthrough" else 70,
            "latency_ms": 10,
        }


def test_runner_captures_paired_observations_and_taskset_identity():
    data = manifest()
    result = run_experiment(data, Adapter())
    assert result["decision"]["status"] == "pass"
    assert result["taskset_digest"] == digest(data["tasks"])
    assert len(result["attempts"]) == 10


def test_identical_but_wrong_observed_identity_cannot_pass():
    result = run_experiment(manifest(), Adapter(drift=True))
    assert result["decision"]["status"] == "inconclusive"
    assert result["identity_verification"]["status"] == "inconclusive"


def test_provider_failure_is_terminal_evidence_without_exception_message():
    result = run_experiment(manifest(), Adapter(broken=True))
    assert result["decision"]["status"] == "inconclusive"
    assert len(result["attempts"]) == 10
    assert "secret" not in json.dumps(result)


def test_cancellation_preserves_partial_terminal_evidence():
    class Cancelled(Adapter):
        def attempt(self, *args):
            raise KeyboardInterrupt()

    result = run_experiment(manifest(), Cancelled())
    assert result["decision"]["status"] == "inconclusive"
    assert result["cancelled"] is True


def test_content_addressed_evidence_cannot_be_overwritten(tmp_path):
    result = run_experiment(manifest(), Adapter())
    path = write_evidence(result, tmp_path)
    assert path.name == digest(result).split(":")[1] + ".json"
    assert json.loads(path.read_text("utf-8")) == json.loads(json.dumps(result))
    assert write_evidence(result, tmp_path) == path


def test_nonfinite_provider_telemetry_preserves_serializable_evidence(tmp_path):
    class Invalid(Adapter):
        def attempt(self, *args):
            row = super().attempt(*args)
            row["tokens"] = float("nan")
            return row

    result = run_experiment(manifest(), Invalid())
    assert result["decision"]["status"] == "inconclusive"
    write_evidence(result, tmp_path)


def test_unexpected_observed_identity_values_are_not_leaked():
    class Secret(Adapter):
        def attempt(self, *args):
            row = super().attempt(*args)
            row["identity"]["model"] = "secret-from-provider-error"
            row["identity"]["api_key"] = "secret-from-provider-error"
            return row

    result = run_experiment(manifest(), Secret())
    assert result["decision"]["status"] == "inconclusive"
    assert "secret-from-provider-error" not in json.dumps(result)


def test_malformed_identity_cannot_break_terminal_evidence(tmp_path):
    class Invalid(Adapter):
        def attempt(self, *args):
            row = super().attempt(*args)
            row["identity"]["model"] = float("nan")
            return row

    result = run_experiment(manifest(), Invalid())
    assert result["decision"]["status"] == "inconclusive"
    write_evidence(result, tmp_path)
