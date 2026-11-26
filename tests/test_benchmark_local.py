import hashlib
import sys

import pytest

from agentops.benchmarks.local import proxy_command, proxy_environment, verify_artifact


def test_selected_provider_binding_overrides_ambient_backend_without_changing_legacy_configs():
    from agentops.benchmarks.local import proxy_environment
    inherited={'HEADROOM_BACKEND':'bedrock','ANTHROPIC_API_KEY':'private'}
    assert proxy_environment({'provider_binding':'anthropic'},inherited)['HEADROOM_BACKEND']=='anthropic'
    assert proxy_environment({},inherited)['HEADROOM_BACKEND']=='bedrock'


@pytest.mark.parametrize('ambient',['bedrock','anyllm'])
@pytest.mark.parametrize('binding,expected',[('openai','anthropic'),('azure','anthropic'),('anthropic','anthropic'),('bedrock','bedrock')])
def test_measured_binding_overrides_ambient_and_configured_translation(ambient,binding,expected):
    observed=proxy_environment({'provider_binding':binding,'proxy_args':['--backend','bedrock']},
        {'HEADROOM_BACKEND':ambient,'OPENAI_TARGET_API_URL':'https://selected.example/v1'})
    assert observed['HEADROOM_BACKEND']==expected
    assert observed['OPENAI_TARGET_API_URL']=='https://selected.example/v1'


def test_azure_resource_endpoint_binds_supported_v1_compatible_target():
    from agentops.benchmarks.local import proxy_environment
    observed=proxy_environment({'provider_binding':'azure'},{'AZURE_OPENAI_ENDPOINT':'https://resource.openai.azure.com/'})
    assert observed['OPENAI_TARGET_API_URL']=='https://resource.openai.azure.com/openai'


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
