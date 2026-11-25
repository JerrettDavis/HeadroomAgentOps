import json

from benchmark_fixture import build_fixture
from test_benchmark_core import manifest

from agentops.benchmarks.cli import main
from agentops.benchmarks.core import digest


def test_real_install_proxy_process_and_cli_preserve_simulator_boundary(tmp_path):
    artifact, sha256 = build_fixture(tmp_path)
    config = tmp_path / "runner.json"
    config.write_text(
        json.dumps({"wheelhouse": str(tmp_path), "startup_timeout": 10}),
        encoding="utf-8",
    )
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
            ]
        )
        == 0
    )
    inspection = json.loads((inspection_dir / "inspection.json").read_text())
    experiment = manifest()
    experiment["candidate"]["artifact_sha256"] = sha256
    experiment["environment"] = inspection["environment"]
    experiment["agent"] = inspection["agent"]
    # This tests transport/provenance, not sub-millisecond fixture performance.
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
            ]
        )
        == 2
    )
    summary = json.loads((output / "summary.json").read_text())
    result = json.loads((output / summary["result_file"]).read_text())
    assert result["decision"]["simulated_decision"]["status"] == "pass"
    assert result["decision"]["status"] == "inconclusive"
    assert result["identity_verification"]["status"] == "pass"
    assert summary["result_sha256"] == digest(result)
    assert len(result["attempts"]) == 10
