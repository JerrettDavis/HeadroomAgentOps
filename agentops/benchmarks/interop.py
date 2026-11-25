"""Explicit text-dataset bridges; external scores are never release evidence."""

from copy import deepcopy


def _text_task(task):
    allowed = {"id", "messages", "expected", "max_tokens", "stream"}
    if not isinstance(task, dict) or set(task) - allowed:
        raise ValueError("interchange supports text tasks without tools only")
    if not isinstance(task.get("id"), str) or not task["id"].strip():
        raise ValueError("tasks require stable nonempty string ids")
    if not isinstance(task.get("expected"), str) or not task["expected"]:
        raise ValueError("interchange requires one nonempty literal substring target")
    messages = task.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("tasks require text messages")
    for message in messages:
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] not in ("system", "user", "assistant")
            or not isinstance(message["content"], str)
        ):
            raise ValueError("only system/user/assistant text messages are supported")
    if (
        type(task.get("max_tokens", 1024)) is not int
        or not 1 <= task.get("max_tokens", 1024) <= 1_000_000
    ):
        raise ValueError("invalid token budget")
    if "stream" in task and type(task["stream"]) is not bool:
        raise ValueError("stream must be boolean")


def _taskset(source):
    if not isinstance(source, dict) or set(source) != {
        "taskset_id",
        "revision",
        "tasks",
    }:
        raise ValueError("expected a taskset, not an experiment or result")
    for key in ("taskset_id", "revision"):
        if not isinstance(source[key], str) or not source[key].strip():
            raise ValueError("taskset identity and revision must be nonempty strings")
    if not isinstance(source["tasks"], list) or not source["tasks"]:
        raise ValueError("taskset must contain tasks")
    ids = set()
    for task in source["tasks"]:
        _text_task(task)
        if task["id"] in ids:
            raise ValueError("duplicate task id")
        ids.add(task["id"])


def export_dataset(source, format):
    """Export Inspect Sample JSONL or AIPerf single_turn records, without execution."""
    _taskset(source)
    rows = []
    for task in source["tasks"]:
        if format == "inspect":
            rows.append(
                {
                    "id": task["id"],
                    "input": deepcopy(task["messages"]),
                    "target": task["expected"],
                    "metadata": {
                        "headroom": {
                            k: task[k] for k in ("max_tokens", "stream") if k in task
                        }
                    },
                }
            )
        elif format == "aiperf":
            if len(task["messages"]) != 1 or task["messages"][0]["role"] != "user":
                raise ValueError(
                    "AIPerf single_turn export requires a single user message"
                )
            rows.append(
                {
                    "text": task["messages"][0]["content"],
                    "output_length": task.get("max_tokens", 1024),
                }
            )
        else:
            raise ValueError("unsupported dataset format")
    return rows


def import_inspect(rows, taskset_id, revision):
    """Import the literal-target text subset, rejecting custom grading/execution."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("expected nonempty Inspect Sample records")
    tasks = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {
            "id",
            "input",
            "target",
            "metadata",
        }:
            raise ValueError("unsupported Inspect Sample fields")
        sample_id = row.get("id")
        if type(sample_id) not in (str, int):
            raise ValueError("Inspect samples require explicit stable ids")
        metadata = row.get("metadata", {})
        if not isinstance(metadata, dict) or set(metadata) - {"headroom"}:
            raise ValueError("custom metadata requires an explicit dataset adapter")
        controls = metadata.get("headroom", {})
        if not isinstance(controls, dict) or set(controls) - {"max_tokens", "stream"}:
            raise ValueError("unknown Headroom sample controls")
        messages = row.get("input")
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        tasks.append(
            {
                "id": str(sample_id),
                "messages": deepcopy(messages),
                "expected": row.get("target"),
                **controls,
            }
        )
    result = {"taskset_id": taskset_id, "revision": revision, "tasks": tasks}
    _taskset(result)
    return result


def inspect_task(source):
    """Build a native Inspect task using the optional SDK, without model judges.

    Inspect owns its generation/streaming and logs. This task measures text
    quality; native stream assertions and release qualification stay in our runner.
    """
    from inspect_ai import Task
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.model import (
        ChatMessageAssistant,
        ChatMessageSystem,
        ChatMessageUser,
    )
    from inspect_ai.scorer import includes
    from inspect_ai.solver import solver

    rows = export_dataset(source, "inspect")
    types = {
        "system": ChatMessageSystem,
        "user": ChatMessageUser,
        "assistant": ChatMessageAssistant,
    }
    samples = [
        Sample(
            id=row["id"],
            input=[types[m["role"]](content=m["content"]) for m in row["input"]],
            target=row["target"],
            metadata=row["metadata"],
        )
        for row in rows
    ]

    @solver
    def native_budget():
        async def solve(state, generate):
            return await generate(
                state, max_tokens=state.metadata["headroom"].get("max_tokens", 1024)
            )

        return solve

    return Task(
        name=source["taskset_id"],
        version=source["revision"],
        dataset=MemoryDataset(samples),
        solver=native_budget(),
        scorer=includes(ignore_case=False),
    )
