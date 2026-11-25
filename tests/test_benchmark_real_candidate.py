"""Optional wire validation against an actual wheel and a hermetic provider.

This validates the packaged proxy, not model quality. It always uses simulator
lane so fake-provider observations cannot become release performance evidence.
"""

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_benchmark_core import manifest

from agentops.benchmarks.cli import main
from agentops.benchmarks.local import LocalAdapter
from agentops.benchmarks.runner import run_experiment


@pytest.mark.skipif(
    not os.environ.get("HEADROOM_BENCH_CANDIDATE_WHEEL"),
    reason="set HEADROOM_BENCH_CANDIDATE_WHEEL for actual packaged Headroom proxy validation",
)
def test_packaged_candidate_uses_supported_runtime_provenance_and_protocol(
    tmp_path, monkeypatch
):
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.respond({"object": "list", "data": []})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.respond(
                {
                    "id": "chatcmpl-fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "hello"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 1,
                        "total_tokens": 101,
                    },
                }
            )

    artifact = Path(os.environ["HEADROOM_BENCH_CANDIDATE_WHEEL"]).resolve()
    sha256 = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(
        "OPENAI_TARGET_API_URL", f"http://127.0.0.1:{provider.server_port}"
    )
    monkeypatch.setenv("_BENCH_TEST_PROVIDER_KEY", "hermetic-not-a-real-provider-key")
    config = tmp_path / "runner.json"
    config.write_text(
        json.dumps(
            {
                "protocol": "openai",
                "api_key_env": "_BENCH_TEST_PROVIDER_KEY",
                "diagnostics_dir": str(tmp_path / "diagnostics"),
                "install_timeout": 1200,
                "startup_timeout": 120,
            }
        ),
        encoding="utf-8",
    )
    try:
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
        data = manifest()
        data["candidate"]["artifact_sha256"] = sha256
        data["environment"] = inspection["environment"]
        data["agent"] = inspection["agent"]
        data["rollout"] = {
            "registry_digest": inspection["runtime"]["rollout_registry_digest"],
            "snapshot_digest": inspection["runtime"]["rollout_snapshot_digest"],
        }
        data["model"] = "gpt-4o-mini-2024-07-18"
        data["policy"].update(minimum_savings=0, maximum_latency_ratio=1000)
        source = tmp_path / "experiment.json"
        source.write_text(json.dumps(data), encoding="utf-8")
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
                    "--lane",
                    "simulator",
                ]
            )
            == 2
        )
        summary = json.loads((output / "summary.json").read_text())
        result = json.loads((output / summary["result_file"]).read_text())
        assert result["identity_verification"]["status"] == "pass"
        assert result["decision"]["simulated_decision"]["status"] == "pass"
        assert all(row["success"] for row in result["attempts"])
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(
    not os.environ.get("HEADROOM_BENCH_CANDIDATE_WHEEL"),
    reason="actual wheel required for Azure/Bedrock wire validation",
)
def test_packaged_candidate_azure_routing_and_signed_bedrock(tmp_path, monkeypatch):
    seen = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            data = b'{"object":"list","data":[]}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            bedrock = "/model/" in self.path
            seen.append(
                {
                    "path": self.path,
                    "azure_key": self.headers.get("api-key") == "hermetic-key",
                    "signed": self.headers.get("Authorization", "").startswith(
                        "AWS4-HMAC-SHA256 "
                    ),
                }
            )
            if bedrock and self.path.endswith("/converse"):
                response = {
                    "output": {
                        "message": {"role": "assistant", "content": [{"text": "hello"}]}
                    },
                    "stopReason": "end_turn",
                    "usage": {
                        "inputTokens": 100,
                        "outputTokens": 1,
                        "totalTokens": 101,
                    },
                    "metrics": {"latencyMs": 1},
                }
            elif bedrock:
                response = {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-20250514",
                    "content": [{"type": "text", "text": "hello"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 100, "output_tokens": 1},
                }
            else:
                response = {
                    "id": "chatcmpl_fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini-2024-07-18",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "hello"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 1,
                        "total_tokens": 101,
                    },
                }
            data = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("_BENCH_AZURE_TEST_KEY", "hermetic-key")
    monkeypatch.setenv("OPENAI_TARGET_API_URL", endpoint + "/openai")
    monkeypatch.setenv("BEDROCK_TARGET_API_URL", endpoint)
    # Anthropic-to-Bedrock translation uses the packaged LiteLLM runtime
    # endpoint; BEDROCK_TARGET_API_URL alone controls direct Bedrock passthrough.
    monkeypatch.setenv("AWS_BEDROCK_RUNTIME_ENDPOINT", endpoint)
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", endpoint)
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK", endpoint)
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "hermetic-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "hermetic-secret-key")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    artifact = Path(os.environ["HEADROOM_BENCH_CANDIDATE_WHEEL"]).resolve()
    sha256 = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    data = manifest()
    data["candidate"]["artifact_sha256"] = sha256
    config = {
        "protocol": "azure",
        "api_key_env": "_BENCH_AZURE_TEST_KEY",
        "extras": ["proxy", "bedrock"],
        "install_timeout": 1200,
        "startup_timeout": 120,
        "request_timeout": 30,
        "diagnostics_dir": str(tmp_path / "diagnostics"),
    }
    try:
        adapter = LocalAdapter(data, artifact, config, tmp_path / "candidate")
        for protocol in ("azure", "bedrock"):
            if protocol == "bedrock":
                config.update(
                    protocol="bedrock",
                    proxy_args=[
                        "--backend",
                        "bedrock",
                        "--bedrock-region",
                        "us-east-1",
                    ],
                    request_model="anthropic.claude-sonnet-4-20250514-v1:0",
                )
                config.pop("api_key_env", None)
                # Headroom translates the Converse response back to Anthropic
                # format and reports the original requested Bedrock model ID.
                data["model"] = "anthropic.claude-sonnet-4-20250514-v1:0"
            else:
                config["request_model"] = "deployment-alias"
                data["model"] = "gpt-4o-mini-2024-07-18"
            observed = adapter.probe()
            data["environment"] = observed["environment"]
            data["agent"] = observed["agent"]
            data["rollout"] = {
                "registry_digest": observed["runtime"]["rollout_registry_digest"],
                "snapshot_digest": observed["runtime"]["rollout_snapshot_digest"],
            }
            data["policy"].update(
                minimum_pairs=2, minimum_savings=0, maximum_latency_ratio=1000
            )
            data["repeats"] = 2
            result = run_experiment(data, adapter)
            assert result["identity_verification"]["status"] == "pass", result[
                "identity_verification"
            ]
            assert result["decision"]["status"] == "pass", result["decision"]
        assert any(
            row["path"] == "/openai/v1/chat/completions" and row["azure_key"]
            for row in seen
        )
        assert any("/model/" in row["path"] and row["signed"] for row in seen)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
