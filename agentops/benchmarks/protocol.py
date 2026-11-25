"""Public Headroom protocol adapters; credentials stay in the proxy environment."""

import json
import time


def completion(request, protocol, model, task):
    body = {
        "model": model,
        "messages": task["messages"],
        "temperature": 0,
        "max_tokens": task.get("max_tokens", 1024),
        "stream": task.get("stream", False),
    }
    if "tools" in task:
        body["tools"] = task["tools"]
    if "tool_choice" in task:
        body["tool_choice"] = task["tool_choice"]
    start = time.perf_counter()
    if protocol in ("anthropic", "bedrock"):
        response = request("/v1/messages", body)
        text = "".join(
            block.get("text", "")
            for block in response.get("content", [])
            if block.get("type") == "text"
        )
        tokens = response.get("usage", {}).get("input_tokens")
        usage = response.get("usage", {})
        if type(tokens) is int:
            for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                value = usage.get(key, 0)
                tokens = tokens + value if type(value) is int and value >= 0 else None
                if tokens is None:
                    break
        tool_names = [
            block.get("name")
            for block in response.get("content", [])
            if block.get("type") == "tool_use"
        ]
    elif protocol in ("openai", "azure"):
        if body["stream"]:
            body["stream_options"] = {"include_usage": True}
        response = request("/v1/chat/completions", body)
        choices = response.get("choices", [])
        text = choices[0].get("message", {}).get("content") if choices else None
        tokens = response.get("usage", {}).get("prompt_tokens")
        tool_names = (
            [
                call.get("function", {}).get("name")
                for call in choices[0].get("message", {}).get("tool_calls", [])
            ]
            if choices
            else []
        )
    else:
        raise ValueError("unsupported proxy protocol")
    success = (
        not task.get("expected") or isinstance(text, str) and task["expected"] in text
    )
    if "expected_tools" in task:
        success = success and sorted(tool_names) == sorted(task["expected_tools"])
    return {
        "success": bool(success),
        "tokens": tokens,
        "latency_ms": (time.perf_counter() - start) * 1000,
        "response_model": response.get("model"),
    }


def decode_sse(lines, protocol):
    """Aggregate a complete SSE response; a truncated stream is an outage."""

    def events():
        data = []
        for chunk in lines:
            for line in chunk.decode("utf-8").splitlines():
                if line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    yield "\n".join(data)
                    data = []
        if data:
            yield "\n".join(data)

    text, tools, usage, model, terminal = "", {}, {}, None, False
    for payload in events():
        if payload == "[DONE]":
            terminal = True
            continue
        if terminal:
            raise ValueError("data after provider stream completion")
        event = json.loads(payload)
        if "error" in event or event.get("type") == "error":
            raise ValueError("provider stream error")
        if protocol in ("openai", "azure"):
            observed = event.get("model")
            if observed:
                if model is not None and observed != model:
                    raise ValueError("model drift within provider stream")
                model = observed
            if event.get("usage"):
                usage.update(event["usage"])
            for choice in event.get("choices", []):
                delta = choice.get("delta", {})
                text += delta.get("content") or ""
                for tool in delta.get("tool_calls", []):
                    entry = tools.setdefault(
                        tool["index"], {"name": "", "arguments": ""}
                    )
                    for key in ("name", "arguments"):
                        entry[key] += tool.get("function", {}).get(key) or ""
        else:
            kind = event.get("type")
            if kind == "message_start":
                observed = event["message"].get("model")
                if model is not None and observed != model:
                    raise ValueError("model drift within provider stream")
                model = observed
                usage.update(event["message"].get("usage", {}))
            elif kind == "message_delta":
                usage.update(event.get("usage", {}))
            elif kind == "content_block_start":
                block = event["content_block"]
                if block.get("type") == "tool_use":
                    tools[event["index"]] = {"name": block.get("name"), "arguments": ""}
                elif block.get("type") == "text":
                    text += block.get("text", "")
            elif (
                kind == "content_block_delta"
                and event.get("delta", {}).get("type") == "text_delta"
            ):
                text += event["delta"]["text"]
            elif kind == "message_stop":
                terminal = True
    if not terminal:
        raise ValueError("truncated provider stream")
    if protocol in ("openai", "azure"):
        return {
            "model": model,
            "usage": usage,
            "choices": [
                {
                    "message": {
                        "content": text,
                        "tool_calls": [
                            {"function": tool} for _, tool in sorted(tools.items())
                        ],
                    }
                }
            ],
        }
    return {
        "model": model,
        "usage": usage,
        "content": [{"type": "text", "text": text}]
        + [
            {"type": "tool_use", "name": tool["name"]}
            for _, tool in sorted(tools.items())
        ],
    }


def observe_runtime(request):
    health = request("/health")
    stats = request("/stats")
    rollout = stats.get("rollout", {})
    if (
        rollout.get("qualification_eligible") is not True
        or rollout.get("unsafe_override") is not False
    ):
        raise ValueError("rollout is not eligible for qualification")
    return {
        "optimization_enabled": health.get("config", {}).get("optimize"),
        "rollout_registry_digest": rollout.get("registry_digest"),
        "rollout_snapshot_digest": rollout.get("snapshot_digest"),
    }
