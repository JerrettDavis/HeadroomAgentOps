"""Produce the upstream release reference after rechecking raw evidence.

Producer metadata is an identity claim, not authentication. Consumers must
verify the GitHub artifact attestation and then the raw-result digest.
"""

import os
import re
from datetime import datetime
from urllib.parse import urlsplit

from .core import ARMS, digest, evaluate, schedule, validate_manifest
from .exit_evidence import exit_matches
from .runner import expected_identity


def github_producer():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("release references require a GitHub Actions producer context")
    return {
        "repository": os.environ["GITHUB_REPOSITORY"],
        "workflow": os.environ["GITHUB_WORKFLOW_REF"]
        .split("@", 1)[0]
        .rsplit("/", 1)[-1],
        "run_id": int(os.environ["GITHUB_RUN_ID"]),
        "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
        "commit_sha": os.environ["GITHUB_SHA"],
    }


def build_reference(experiment, result, result_uri, producer):
    validate_manifest(experiment)
    uri = urlsplit(result_uri)
    if (
        uri.scheme != "https"
        or not uri.hostname
        or uri.username
        or uri.password
        or uri.query
        or uri.fragment
    ):
        raise ValueError(
            "result URI must be HTTPS without embedded credentials, query or fragment"
        )
    if set(producer) != {
        "repository",
        "workflow",
        "run_id",
        "run_attempt",
        "commit_sha",
    }:
        raise ValueError("invalid producer fields")
    if (
        not re.fullmatch(r"[^/\s]+/[^/\s]+", producer["repository"])
        or not producer["workflow"]
    ):
        raise ValueError("invalid producer repository/workflow")
    if not re.fullmatch(r"[a-f0-9]{40}", producer["commit_sha"]):
        raise ValueError("invalid producer commit")
    if any(
        type(producer[key]) is not int or producer[key] < 1
        for key in ("run_id", "run_attempt")
    ):
        raise ValueError("invalid producer run identity")
    plan = schedule(experiment["tasks"], experiment["repeats"], experiment["seed"])
    if (
        result["manifest_digest"] != digest(experiment)
        or result["taskset_digest"] != digest(experiment["tasks"])
        or digest(result["schedule"]) != digest(plan)
        or digest(result["policy"]) != digest(experiment["policy"])
        or result["experiment_id"] != experiment["experiment_id"]
        or type(result["seed"]) is not int
        or result["seed"] != experiment["seed"]
        or result["taskset_revision"] != experiment["taskset_revision"]
    ):
        raise ValueError("raw evidence does not bind the declared experiment")
    created = datetime.fromisoformat(result["created_at"])
    if created.tzinfo is None:
        raise ValueError("result timestamp needs a timezone")
    expected_attempts = [
        (pair["pair_id"], pair["task_id"], pair["repeat"], arm)
        for pair in plan
        for arm in pair["arms"]
    ]
    actual = [
        (row["pair_id"], row["task_id"], row["repeat"], row["arm"])
        for row in result["attempts"]
    ]
    if digest(actual) != digest(expected_attempts[: len(actual)]) or len(actual) > len(
        expected_attempts
    ):
        raise ValueError("attempt identities/order do not match the seeded schedule")
    identity = expected_identity(experiment)
    mismatches = set()
    for row in result["attempts"]:
        for key, value in identity.items():
            if digest(row.get("identity", {}).get(key)) != digest(value):
                mismatches.add(key)
        if row.get("optimization_enabled") is not (row["arm"] == ARMS[1]):
            mismatches.add("optimization_enabled")
    if len(actual) != len(expected_attempts):
        mismatches.add("incomplete_attempts")
    verification = {
        "status": "inconclusive" if mismatches else "pass",
        "mismatches": sorted(mismatches),
    }
    recomputed = evaluate(result["attempts"], experiment["policy"], len(plan))
    status = recomputed["status"]
    if (
        mismatches
        or result.get("lane") != "live"
        or result.get("cancelled")
        or result["decision"]["status"] == "inconclusive"
        or (
            experiment["environment"]["runner"] in ("docker", "ssh")
            and (
                result.get("execution", {}).get("transport")
                != experiment["environment"]["runner"]
                or not exit_matches(result.get("execution"), status)
            )
        )
    ):
        status = "inconclusive"
    elif result["decision"]["status"] != status:
        raise ValueError("raw decision disagrees with policy evaluation")
    arms = [
        {**identity, "name": arm, "optimization_enabled": arm == ARMS[1]}
        for arm in ARMS
    ]
    return {
        "schema_version": 1,
        "result_id": "benchmark-" + digest(result).split(":")[1],
        "created_at": result["created_at"],
        "result_uri": result_uri,
        "result_sha256": digest(result),
        "status": status,
        "identity_verification": verification,
        "producer": producer,
        "experiment": {
            "experiment_id": experiment["experiment_id"],
            "taskset_id": experiment["taskset_id"],
            "taskset_digest": digest(experiment["tasks"]),
            "attempts_per_arm": experiment["repeats"],
            "a1": arms[0],
            "b": arms[1],
        },
    }
