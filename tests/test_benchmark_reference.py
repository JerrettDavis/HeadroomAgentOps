import copy
import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource
from test_benchmark_core import manifest
from test_benchmark_runner import Adapter

from agentops.benchmarks.reference import build_reference
from agentops.benchmarks.runner import run_experiment

PRODUCER = {
    "repository": "JerrettDavis/HeadroomAgentOps",
    "workflow": "release-benchmark.yml",
    "run_id": 1,
    "run_attempt": 1,
    "commit_sha": "e" * 40,
}


def test_export_matches_pinned_upstream_release_contract():
    data = manifest()
    result = run_experiment(data, Adapter())
    result["lane"] = "live"
    reference = build_reference(
        data, result, "https://example.com/result.json", PRODUCER
    )
    root = Path(__file__).parents[1] / "agentops/benchmarks/contracts"
    resources = [json.loads(path.read_text()) for path in root.glob("*.schema.json")]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in resources
    )
    schema = json.loads((root / "benchmark-result-ref.schema.json").read_text())
    jsonschema.Draft202012Validator(
        schema, registry=registry, format_checker=jsonschema.FormatChecker()
    ).validate(reference)
    assert reference["status"] == "pass"


def test_export_rejects_changed_manifest_and_forged_schedule():
    data = manifest()
    result = run_experiment(data, Adapter())
    result["lane"] = "live"
    other = copy.deepcopy(data)
    other["seed"] = 9
    with pytest.raises(ValueError):
        build_reference(other, result, "https://example.com/raw", PRODUCER)
    result["attempts"][0]["pair_id"] = "not-the-scheduled-pair"
    with pytest.raises(ValueError):
        build_reference(data, result, "https://example.com/raw", PRODUCER)


def test_export_does_not_turn_a_simulator_into_live_evidence():
    data = manifest()
    result = run_experiment(data, Adapter())
    result["lane"] = "simulator"
    assert (
        build_reference(data, result, "https://example.com/raw", PRODUCER)["status"]
        == "inconclusive"
    )


def test_export_rejects_credential_bearing_uri():
    data = manifest()
    result = run_experiment(data, Adapter())
    with pytest.raises(ValueError):
        build_reference(data, result, "https://example.com/raw?token=secret", PRODUCER)


@pytest.mark.parametrize("runner", ["docker", "ssh"])
def test_remote_reference_requires_execution_exit_proof(runner):
    data = manifest()
    data["environment"]["runner"] = runner
    result = run_experiment(data, Adapter())
    result["lane"] = "live"
    assert (
        build_reference(data, result, "https://example.com/raw", PRODUCER)["status"]
        == "inconclusive"
    )
    result["execution"] = {
        "transport": runner,
        "status": "exited",
        "exit_code": 0,
        "oom_killed": False,
        "error": False,
    }
    assert (
        build_reference(data, result, "https://example.com/raw", PRODUCER)["status"]
        == "pass"
    )
