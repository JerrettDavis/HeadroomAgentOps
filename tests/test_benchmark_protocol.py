import pytest

from agentops.benchmarks.protocol import completion, decode_sse, observe_runtime


def test_openai_and_azure_use_candidate_proxy_and_report_actual_usage():
    calls = []

    def request(path, body=None):
        calls.append((path, body))
        return {
            "model": "model",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 77},
        }

    for protocol in ("openai", "azure"):
        result = completion(
            request,
            protocol,
            "model",
            {"messages": [{"role": "user", "content": "hi"}], "expected": "hello"},
        )
        assert result["tokens"] == 77
        assert result["success"] is True
        assert calls[-1][0] == "/v1/chat/completions"
        assert calls[-1][1]["temperature"] == 0


def test_bedrock_uses_anthropic_proxy_protocol_not_direct_provider():
    def request(path, body=None):
        assert path == "/v1/messages"
        assert body["max_tokens"] == 1024
        return {
            "model": "model",
            "content": [{"type": "text", "text": "hello"}],
            "usage": {"input_tokens": 40},
        }

    result = completion(
        request,
        "bedrock",
        "model",
        {"messages": [{"role": "user", "content": "hi"}], "expected": "hello"},
    )
    assert result["tokens"] == 40


def test_missing_usage_remains_missing():
    result = completion(
        lambda *args: {"choices": [{"message": {"content": "hello"}}]},
        "openai",
        "model",
        {"messages": [], "expected": "hello"},
    )
    assert result["tokens"] is None


def test_runtime_observation_reads_effective_state():
    def request(path, body=None):
        if path == "/health":
            return {"config": {"optimize": False}}
        assert path == "/stats"
        return {
            "rollout": {
                "registry_digest": "registry",
                "snapshot_digest": "snapshot",
                "qualification_eligible": True,
                "unsafe_override": False,
            }
        }

    assert observe_runtime(request) == {
        "optimization_enabled": False,
        "rollout_registry_digest": "registry",
        "rollout_snapshot_digest": "snapshot",
    }


def test_unsafe_rollout_is_not_qualifiable():
    with pytest.raises(ValueError):
        observe_runtime(
            lambda path: (
                {"config": {"optimize": True}}
                if path == "/health"
                else {"rollout": {"qualification_eligible": False}}
            )
        )


def test_openai_stream_retains_usage_and_tool_calls():
    events = [
        b'data: {"model":"m","choices":[{"delta":{"content":"he"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"llo","tool_calls":[{"index":0,"function":{"name":"lookup","arguments":"{}"}}]}}]}\n',
        b'data: {"choices":[],"usage":{"prompt_tokens":7}}\n',
        b"data: [DONE]\n",
    ]
    response = decode_sse([line + b"\n" for line in events], "openai")

    def request(path, body):
        assert body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["tools"] == [{"type": "function"}]
        return response

    result = completion(
        request,
        "openai",
        "m",
        {
            "messages": [],
            "expected": "hello",
            "stream": True,
            "tools": [{"type": "function"}],
            "expected_tools": ["lookup"],
        },
    )
    assert result["success"] is True
    assert result["tokens"] == 7


def test_anthropic_stream_reads_final_input_usage_without_fabricating_it():
    events = [
        b"event: message_start\n",
        b'data: {"type":"message_start","message":{"model":"m","usage":{"input_tokens":8}}}\n',
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hello"}}\n',
        b'data: {"type":"message_stop"}\n',
    ]
    response = decode_sse([line + b"\n" for line in events], "anthropic")
    assert response["usage"]["input_tokens"] == 8
    assert response["content"] == [{"type": "text", "text": "hello"}]


def test_truncated_stream_never_qualifies():
    with pytest.raises(ValueError):
        decode_sse(
            [b'data: {"model":"m","choices":[{"delta":{"content":"hello"}}]}\n'],
            "openai",
        )


def test_streamed_tool_optional_null_fields_are_supported():
    response = decode_sse(
        [
            b'data: {"model":"m","choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"lookup","arguments":"{"}}]}}]}\n\n',
            b'data: {"model":null,"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":null,"arguments":"}"}}]}}]}\n\n',
            b"data: [DONE]\n\n",
        ],
        "azure",
    )
    assert response["model"] == "m"
    assert response["choices"][0]["message"]["tool_calls"][0]["function"] == {
        "name": "lookup",
        "arguments": "{}",
    }


def test_model_drift_within_a_stream_is_inconclusive():
    with pytest.raises(ValueError):
        decode_sse(
            [
                b'data: {"model":"other","choices":[]}\n\n',
                b'data: {"model":"expected","choices":[]}\n\n',
                b"data: [DONE]\n\n",
            ],
            "openai",
        )
