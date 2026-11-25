"""Transport-independent orchestration and immutable terminal evidence."""

import json
from datetime import UTC, datetime
from pathlib import Path

from .core import ARMS, digest, evaluate, finite_number, schedule, validate_manifest


def expected_identity(experiment):
    return {
        "candidate_sha": experiment["candidate"]["sha"],
        "artifact_sha256": experiment["candidate"]["artifact_sha256"],
        "rollout_registry_digest": experiment["rollout"]["registry_digest"],
        "rollout_snapshot_digest": experiment["rollout"]["snapshot_digest"],
        "model": experiment["model"],
        "agent": experiment["agent"],
        "taskset_id": experiment["taskset_id"],
        "environment": experiment["environment"],
    }


def run_experiment(experiment, adapter):
    validate_manifest(experiment)
    plan = schedule(experiment["tasks"], experiment["repeats"], experiment["seed"])
    tasks = {t["id"]: t for t in experiment["tasks"]}
    attempts, mismatches = [], set()
    cancelled = False
    expected = expected_identity(experiment)
    for pair in plan:
        for arm in pair["arms"]:
            row = {
                "pair_id": pair["pair_id"],
                "task_id": pair["task_id"],
                "repeat": pair["repeat"],
                "arm": arm,
            }
            base = dict(row)
            try:
                observed = adapter.attempt(experiment, tasks[pair["task_id"]], arm)
                # Only retain documented telemetry, never arbitrary provider responses.
                for key in (
                    "identity",
                    "optimization_enabled",
                    "success",
                    "tokens",
                    "latency_ms",
                ):
                    if key in observed:
                        row[key] = observed[key]
                identity = row.get("identity", {})
                for key, value in expected.items():
                    if digest(identity.get(key)) != digest(value):
                        mismatches.add(key)
                # Record matching identity values, mask foreign values which may
                # contain provider error text or credentials. The mismatch list
                # explains why the evidence cannot qualify.
                row["identity"] = {
                    key: value if digest(identity.get(key)) == digest(value) else None
                    for key, value in expected.items()
                }
                for key in ("tokens", "latency_ms"):
                    value = row.get(key)
                    if not finite_number(value):
                        row[key] = None
                for key in ("success", "optimization_enabled"):
                    if type(row.get(key)) is not bool:
                        row[key] = None
                if row.get("optimization_enabled") is not (arm == ARMS[1]):
                    mismatches.add("optimization_enabled")
            except KeyboardInterrupt:
                row = dict(base)
                row["error"] = "cancelled"
                cancelled = True
                mismatches.add("unobserved_attempt_identity")
            except Exception as exc:  # noqa: BLE001 -- provider adapters can raise arbitrary SDK exceptions
                row = dict(base)
                # Error messages may contain URLs, credentials or prompts.
                row["error"] = type(exc).__name__
                mismatches.add("unobserved_attempt_identity")
            attempts.append(row)
            if cancelled:
                break
        if cancelled:
            break
    decision = evaluate(attempts, experiment["policy"], expected_pairs=len(plan))
    if mismatches or cancelled:
        decision = {
            "status": "inconclusive",
            "reason": "identity mismatch or cancellation",
        }
    return {
        "schema_version": 1,
        "experiment_id": experiment["experiment_id"],
        "created_at": datetime.now(UTC).isoformat(),
        "manifest_digest": digest(experiment),
        "taskset_digest": digest(experiment["tasks"]),
        "taskset_revision": experiment["taskset_revision"],
        "seed": experiment["seed"],
        "schedule": plan,
        "attempts": attempts,
        "policy": experiment["policy"],
        "decision": decision,
        "cancelled": cancelled,
        "identity_verification": {
            "status": "inconclusive" if mismatches else "pass",
            "mismatches": sorted(mismatches),
        },
    }


def write_evidence(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        result,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    path = directory / (digest(result).split(":")[1] + ".json")
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError("existing evidence does not match its digest")
    return path
