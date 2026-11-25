import hashlib
import sys

import pytest

from agentops.benchmarks.local import proxy_command, proxy_environment, verify_artifact


def test_artifact_digest_is_verified_before_any_install(tmp_path):
    artifact = tmp_path / "candidate.whl"
    artifact.write_bytes(b"candidate")
    wanted = "sha256:" + hashlib.sha256(b"candidate").hexdigest()
    assert verify_artifact(artifact, wanted) == artifact.resolve()
    artifact.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        verify_artifact(artifact, wanted)


def test_arms_use_same_command_except_optimization():
    config = {"proxy_args": ["--backend", "bedrock"]}
    a = proxy_command(sys.executable, 8787, config, "a1_passthrough")
    b = proxy_command(sys.executable, 8787, config, "b_headroom")
    assert a == b + ["--no-optimize"]
    assert "--backend" in a
    assert "-P" in a and "-s" in a and "utf8" in a


@pytest.mark.parametrize(
    "args", [["--port", "9"], ["--host=0.0.0.0"], ["--no-optimize"], ["--workers", "2"]]
)
def test_proxy_config_cannot_override_arm_or_listener(args):
    with pytest.raises(ValueError):
        proxy_command(sys.executable, 8787, {"proxy_args": args}, "b_headroom")


def test_explicit_backend_overrides_inherited_headroom_environment():
    original = {"HEADROOM_BACKEND": "anthropic"}
    env = proxy_environment({"proxy_args": ["--backend", "bedrock"]}, original)
    assert env["HEADROOM_BACKEND"] == "bedrock"
    assert original["HEADROOM_BACKEND"] == "anthropic"
    assert (
        proxy_environment({"proxy_args": ["--backend=bedrock"]}, original)[
            "HEADROOM_BACKEND"
        ]
        == "bedrock"
    )
