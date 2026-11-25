import json

from test_benchmark_core import manifest
from test_benchmark_runner import Adapter

from agentops.benchmarks.cli import main
from agentops.benchmarks.runner import run_experiment


def test_cli_plan_is_reproducible_without_provider_calls(tmp_path):
    source = tmp_path / "experiment.json"
    source.write_text(json.dumps(manifest()), encoding="utf-8")
    out = tmp_path / "plan.json"
    assert main(["plan", str(source), "--output", str(out)]) == 0
    first = out.read_bytes()
    assert main(["plan", str(source), "--output", str(out)]) == 0
    assert first == out.read_bytes()


def test_bad_candidate_preserves_inconclusive_terminal_evidence(tmp_path):
    source = tmp_path / "experiment.json"
    source.write_text(json.dumps(manifest()), encoding="utf-8")
    artifact = tmp_path / "candidate.whl"
    artifact.write_bytes(b"tampered")
    output = tmp_path / "results"
    assert (
        main(["run", str(source), "--artifact", str(artifact), "--output", str(output)])
        == 2
    )
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "inconclusive"
    result = json.loads((output / summary["result_file"]).read_text())
    assert result["setup_error"] == "ValueError"
    assert result["identity_verification"]["status"] == "inconclusive"


def test_reference_cli_rejects_noncanonical_raw_file(tmp_path, monkeypatch):
    data = manifest()
    result = run_experiment(data, Adapter())
    source, raw, output = (
        tmp_path / "manifest.json",
        tmp_path / "raw.json",
        tmp_path / "reference.json",
    )
    source.write_text(json.dumps(data), encoding="utf-8")
    raw.write_text(json.dumps(result, indent=2), encoding="utf-8")
    monkeypatch.setattr(
        "agentops.benchmarks.reference.github_producer",
        lambda: {
            "repository": "test/repo",
            "workflow": "benchmark.yml",
            "run_id": 1,
            "run_attempt": 1,
            "commit_sha": "a" * 40,
        },
    )
    assert (
        main(
            [
                "reference",
                str(source),
                "--result",
                str(raw),
                "--uri",
                "https://example.com/raw",
                "--output",
                str(output),
            ]
        )
        == 64
    )
    assert not output.exists()
