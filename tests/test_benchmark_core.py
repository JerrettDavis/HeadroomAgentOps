import copy
import math

import pytest

from agentops.benchmarks.core import digest, evaluate, schedule, validate_manifest


def test_schedule_pairs_every_task_repeat_and_is_reproducible():
    tasks = [{"id": "one"}, {"id": "two"}]
    first = schedule(tasks, repeats=5, seed=42)
    assert first == schedule(tasks, repeats=5, seed=42)
    assert first != schedule(tasks, repeats=5, seed=43)
    assert len(first) == 10
    assert {(p["task_id"], p["repeat"]) for p in first} == {
        (t["id"], r) for t in tasks for r in range(5)
    }
    assert all(set(p["arms"]) == {"a1_passthrough", "b_headroom"} for p in first)


def test_digest_is_canonical_and_rejects_nonfinite_numbers():
    assert digest({"b": 1, "a": 2}) == digest({"a": 2, "b": 1})
    with pytest.raises(ValueError):
        digest({"bad": math.nan})


def test_schedule_rejects_duplicate_tasks_and_invalid_repeats():
    with pytest.raises(ValueError):
        schedule([{"id": "same"}, {"id": "same"}], 2, 1)
    with pytest.raises(ValueError):
        schedule([{"id": "one"}], True, 1)


def observations():
    return [
        {
            "pair_id": str(i),
            "arm": arm,
            "identity": {"artifact": "same"},
            "success": True,
            "tokens": tokens,
            "latency_ms": 10,
        }
        for i in range(5)
        for arm, tokens in [("a1_passthrough", 100), ("b_headroom", 70)]
    ]


POLICY = {
    "version": 1,
    "method": "paired_t_95",
    "minimum_pairs": 5,
    "minimum_savings": 0.2,
    "maximum_accuracy_regression": 0.0,
    "maximum_latency_ratio": 1.2,
    "minimum_accuracy": 0.9,
    "missing_data_status": "inconclusive",
}


def test_complete_paired_observations_pass_policy():
    result = evaluate(observations(), POLICY, expected_pairs=5)
    assert result["status"] == "pass"
    assert result["mean_savings"] == pytest.approx(0.3)


@pytest.mark.parametrize("damage", ["missing", "drift", "nan", "duplicate", "error"])
def test_incomplete_or_untrusted_evidence_is_inconclusive(damage):
    rows = copy.deepcopy(observations())
    if damage == "missing":
        rows.pop()
    elif damage == "drift":
        rows[0]["identity"]["artifact"] = "other"
    elif damage == "nan":
        rows[0]["tokens"] = float("nan")
    elif damage == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    else:
        rows[0]["error"] = "provider unavailable"
    assert evaluate(rows, POLICY, expected_pairs=5)["status"] == "inconclusive"


def test_real_regression_fails():
    rows = observations()
    for row in rows:
        if row["arm"] == "b_headroom":
            row["success"] = False
    assert evaluate(rows, POLICY, expected_pairs=5)["status"] == "fail"


def test_both_arms_failing_tasks_cannot_qualify():
    rows = observations()
    for row in rows:
        row["success"] = False
    assert evaluate(rows, POLICY, 5)["status"] == "fail"


def manifest():
    return {
        "schema_version": 1,
        "experiment_id": "test",
        "seed": 42,
        "repeats": 5,
        "candidate": {"sha": "a" * 40, "artifact_sha256": "sha256:" + "b" * 64},
        "rollout": {
            "registry_digest": "sha256:" + "c" * 64,
            "snapshot_digest": "sha256:" + "d" * 64,
        },
        "model": "pinned-model",
        "agent": "http-v1",
        "taskset_id": "tasks-v1",
        "taskset_revision": "v1",
        "tasks": [
            {
                "id": "one",
                "messages": [{"role": "user", "content": "hello"}],
                "expected": "hello",
            }
        ],
        "environment": {
            "runner": "local",
            "platform": "linux/amd64",
            "image": "bench@sha256:" + "e" * 64,
            "resource_limits": {"cpu": 2, "memory_gib": 4},
        },
        "policy": dict(POLICY),
    }


def test_manifest_validates_pinned_identities():
    validate_manifest(manifest())


@pytest.mark.parametrize(
    "field,value",
    [("seed", True), ("repeats", 0), ("model", ""), ("schema_version", 2)],
)
def test_manifest_rejects_invalid_experiment(field, value):
    data = manifest()
    data[field] = value
    with pytest.raises(ValueError):
        validate_manifest(data)


def test_manifest_rejects_unpinned_artifact_and_secret_fields():
    data = manifest()
    data["candidate"]["artifact_sha256"] = "latest"
    with pytest.raises(ValueError):
        validate_manifest(data)


@pytest.mark.parametrize(
    "key,value",
    [
        ("version", True),
        ("minimum_savings", 2),
        ("maximum_accuracy_regression", -1),
        ("maximum_latency_ratio", 0),
        ("method", "guess"),
    ],
)
def test_policy_rejects_ambiguous_rules(key, value):
    policy = dict(POLICY, **{key: value})
    with pytest.raises(ValueError):
        evaluate(observations(), policy, 5)


def test_identity_comparison_distinguishes_booleans_from_numbers():
    rows = observations()
    for row in rows:
        row["identity"] = {"cpu": 1}
    rows[0]["identity"] = {"cpu": True}
    assert evaluate(rows, POLICY, 5)["status"] == "inconclusive"


def test_derived_telemetry_overflow_is_inconclusive():
    rows = observations()
    for row in rows:
        row["tokens"] = 1e-308 if row["arm"] == "a1_passthrough" else 1e308
    assert evaluate(rows, POLICY, 5)["status"] == "inconclusive"


def test_confidence_interval_overflow_is_inconclusive():
    rows = observations()
    for row in rows:
        row["latency_ms"] = (
            (1.7e308 if row["pair_id"] == "0" else 1)
            if row["arm"] == "b_headroom"
            else 1
        )
    assert evaluate(rows, POLICY, 5)["status"] == "inconclusive"


def test_nested_manifest_identity_rejects_extra_secret_fields():
    data = manifest()
    data["environment"]["api_key"] = "secret"
    with pytest.raises(ValueError):
        validate_manifest(data)


def test_unbounded_json_integer_telemetry_is_inconclusive():
    rows = observations()
    rows[0]["tokens"] = 10**500
    assert evaluate(rows, POLICY, 5)["status"] == "inconclusive"
    data = manifest()
    data["api_key"] = "never embed secrets"
    with pytest.raises(ValueError):
        validate_manifest(data)
