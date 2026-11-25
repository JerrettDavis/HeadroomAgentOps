"""Deterministic experiment planning and fail-closed paired evaluation."""

import hashlib
import json
import math
import random
import re
import statistics

ARMS = ("a1_passthrough", "b_headroom")


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_manifest(data):
    """Reject ambiguous identities and secret-bearing top-level configuration."""
    required = {
        "schema_version",
        "experiment_id",
        "seed",
        "repeats",
        "candidate",
        "rollout",
        "model",
        "agent",
        "taskset_id",
        "taskset_revision",
        "tasks",
        "environment",
        "policy",
    }
    if not isinstance(data, dict) or set(data) != required:
        raise ValueError("manifest must contain exactly the documented fields")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported manifest version")
    for key in ("experiment_id", "model", "agent", "taskset_id", "taskset_revision"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"{key} must be nonempty")

    def pinned(value, pattern):
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError("invalid immutable identity")

    try:
        if set(data["candidate"]) != {"sha", "artifact_sha256"} or set(
            data["rollout"]
        ) != {"registry_digest", "snapshot_digest"}:
            raise ValueError("unknown candidate or rollout identity fields")
        pinned(data["candidate"]["sha"], r"[0-9a-f]{40}")
        for value in (
            data["candidate"]["artifact_sha256"],
            data["rollout"]["registry_digest"],
            data["rollout"]["snapshot_digest"],
        ):
            pinned(value, r"sha256:[0-9a-f]{64}")
        env = data["environment"]
        if set(env) - {
            "runner",
            "platform",
            "image",
            "image_digest",
            "resource_limits",
            "region",
        }:
            raise ValueError("unknown environment identity fields")
        for key in ("runner", "platform", "image"):
            if not isinstance(env[key], str) or not env[key].strip():
                raise ValueError("missing environment identity")
        if "region" in env and (
            not isinstance(env["region"], str) or not env["region"].strip()
        ):
            raise ValueError("region must be a nonempty string")
        if not isinstance(env["resource_limits"], dict) or not env["resource_limits"]:
            raise ValueError("missing resource limits")
        if set(env["resource_limits"]) != {"cpu", "memory_gib"}:
            raise ValueError("resource limits require cpu and memory_gib")
        for value in env["resource_limits"].values():
            if not finite_number(value) or value <= 0:
                raise ValueError("resource limits must be finite positive numbers")
        if "image_digest" in env:
            pinned(env["image_digest"], r"sha256:[0-9a-f]{64}")
        schedule(data["tasks"], data["repeats"], data["seed"])
        for task in data["tasks"]:
            if set(task) - {
                "id",
                "messages",
                "expected",
                "max_tokens",
                "stream",
                "tools",
                "tool_choice",
                "expected_tools",
            }:
                raise ValueError("unknown task fields")
            if not isinstance(task.get("messages"), list) or not task["messages"]:
                raise ValueError("tasks require messages")
            if (
                not isinstance(task.get("expected", ""), str)
                or not task.get("expected")
                and not task.get("expected_tools")
            ):
                raise ValueError(
                    "tasks require an expected substring or expected tool names"
                )
            if "stream" in task and type(task["stream"]) is not bool:
                raise ValueError("stream must be boolean")
            if "expected_tools" in task and (
                not isinstance(task["expected_tools"], list)
                or any(not isinstance(t, str) or not t for t in task["expected_tools"])
            ):
                raise ValueError("expected_tools must be tool names")
            if (
                type(task.get("max_tokens", 1024)) is not int
                or not 1 <= task.get("max_tokens", 1024) <= 1000000
            ):
                raise ValueError("invalid token budget")
        evaluate([], data["policy"], len(data["tasks"]) * data["repeats"])
        digest(data)
    except (KeyError, TypeError) as exc:
        raise ValueError("malformed experiment manifest") from exc


def digest(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def schedule(tasks, repeats, seed):
    if type(repeats) is not int or repeats < 1 or type(seed) is not int:
        raise ValueError("repeats must be positive and seed must be an integer")
    ids = [t["id"] for t in tasks]
    if (
        not ids
        or any(not isinstance(i, str) or not i for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("task IDs must be nonempty and unique")
    rng = random.Random(seed)
    pairs = []
    for task in ids:
        for repeat in range(repeats):
            arms = list(ARMS)
            rng.shuffle(arms)
            pairs.append(
                {
                    "pair_id": digest([task, repeat]),
                    "task_id": task,
                    "repeat": repeat,
                    "arms": arms,
                }
            )
    rng.shuffle(pairs)
    return pairs


def evaluate(rows, policy, expected_pairs):
    """Evaluate complete observations; unavailable evidence never grants a pass.

    Policy version 1 uses paired Student t 95% confidence intervals.
    The producer must additionally verify observed identity against the manifest.
    """
    inconclusive = lambda reason: {"status": "inconclusive", "reason": reason}
    fields = {
        "version",
        "method",
        "minimum_pairs",
        "minimum_savings",
        "maximum_accuracy_regression",
        "maximum_latency_ratio",
        "minimum_accuracy",
        "missing_data_status",
    }
    if not isinstance(policy, dict) or set(policy) != fields:
        raise ValueError("policy must contain exactly the documented rules")
    if type(policy.get("version")) is not int or policy["version"] != 1:
        raise ValueError("unsupported benchmark policy version")
    if policy["method"] != "paired_t_95":
        raise ValueError("unsupported statistical method")
    if policy["missing_data_status"] != "inconclusive":
        raise ValueError("missing data must remain inconclusive")
    if type(expected_pairs) is not int or expected_pairs < 1:
        raise ValueError("expected_pairs must be positive")
    minimum = policy["minimum_pairs"]
    if type(minimum) is not int or minimum < 2:
        raise ValueError("minimum_pairs must be at least two")
    for key in (
        "minimum_savings",
        "maximum_accuracy_regression",
        "maximum_latency_ratio",
        "minimum_accuracy",
    ):
        if not finite_number(policy[key]):
            raise ValueError("policy thresholds must be finite numbers")
    if (
        not 0 <= policy["minimum_savings"] <= 1
        or not 0 <= policy["maximum_accuracy_regression"] <= 1
        or policy["maximum_latency_ratio"] <= 0
        or not 0 <= policy["minimum_accuracy"] <= 1
    ):
        raise ValueError("policy thresholds are out of range")
    pairs = {}
    identity = None
    for row in rows:
        if row.get("error") or row.get("arm") not in ARMS or not row.get("pair_id"):
            return inconclusive("attempt failure or invalid pairing")
        observed = row.get("identity")
        if not isinstance(observed, dict) or not observed:
            return inconclusive("missing observed identity")
        if identity is None:
            identity = observed
        if digest(identity) != digest(observed):
            return inconclusive("identity drift")
        for key in ("tokens", "latency_ms"):
            value = row.get(key)
            if not finite_number(value) or value < 0:
                return inconclusive("missing or invalid telemetry")
        if type(row.get("success")) is not bool:
            return inconclusive("missing accuracy outcome")
        pair = pairs.setdefault(row["pair_id"], {})
        if row["arm"] in pair:
            return inconclusive("duplicate attempt")
        pair[row["arm"]] = row
    if (
        len(pairs) != expected_pairs
        or len(pairs) < minimum
        or any(set(p) != set(ARMS) for p in pairs.values())
    ):
        return inconclusive("incomplete paired attempts")
    savings, accuracy, latency = [], [], []
    for pair in pairs.values():
        a, b = (pair[arm] for arm in ARMS)
        if a["tokens"] <= 0 or a["latency_ms"] <= 0:
            return inconclusive("zero baseline denominator")
        savings.append(1 - b["tokens"] / a["tokens"])
        accuracy.append(int(a["success"]) - int(b["success"]))
        latency.append(b["latency_ms"] / a["latency_ms"])
    if any(
        not math.isfinite(value)
        for values in (savings, accuracy, latency)
        for value in values
    ):
        return inconclusive("derived telemetry overflow")

    def bounds(values):
        mean = statistics.mean(values)
        # Rounded upward t(.975, df), df=1..30; beyond 30 use df=30
        # rather than a normal approximation, retaining a conservative bound.
        critical = (
            12.707,
            4.303,
            3.183,
            2.777,
            2.571,
            2.447,
            2.365,
            2.307,
            2.263,
            2.229,
            2.201,
            2.179,
            2.161,
            2.145,
            2.132,
            2.120,
            2.110,
            2.101,
            2.094,
            2.086,
            2.080,
            2.074,
            2.069,
            2.064,
            2.060,
            2.056,
            2.052,
            2.049,
            2.046,
            2.043,
        )[min(len(values) - 2, 29)]
        margin = critical * statistics.stdev(values) / math.sqrt(len(values))
        return mean - margin, mean + margin

    savings_bounds, accuracy_bounds, latency_bounds = (
        bounds(values) for values in (savings, accuracy, latency)
    )
    if any(
        not math.isfinite(value)
        for interval in (savings_bounds, accuracy_bounds, latency_bounds)
        for value in interval
    ):
        return inconclusive("confidence interval overflow")
    passes = (
        savings_bounds[0] >= policy["minimum_savings"]
        and accuracy_bounds[1] <= policy["maximum_accuracy_regression"]
        and latency_bounds[1] <= policy["maximum_latency_ratio"]
        and all(
            statistics.mean(int(pair[arm]["success"]) for pair in pairs.values())
            >= policy["minimum_accuracy"]
            for arm in ARMS
        )
    )
    return {
        "status": "pass" if passes else "fail",
        "pairs": len(pairs),
        "mean_savings": statistics.mean(savings),
        "savings_interval": savings_bounds,
        "accuracy_regression_interval": accuracy_bounds,
        "latency_ratio_interval": latency_bounds,
        "policy": policy,
    }
