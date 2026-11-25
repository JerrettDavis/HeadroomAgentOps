import sys

from agentops.benchmarks.environment import agent_identity, describe_environment


def test_environment_is_observed_not_copied_from_manifest():
    observed = describe_environment(sys.executable)
    assert observed["runner"] == "local"
    assert observed["platform"].count("/") == 1
    assert observed["image_digest"].startswith("sha256:")
    assert observed["resource_limits"]["cpu"] > 0
    assert observed["resource_limits"]["memory_gib"] > 0
    assert observed == describe_environment(sys.executable)
    assert agent_identity().startswith("headroom-agentops/http-v1@sha256:")
