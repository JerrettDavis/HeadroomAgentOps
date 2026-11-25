"""Native Inspect entry point: inspect eval benchmarks/inspect_task.py -T taskset=..."""

from inspect_ai import task

from agentops.benchmarks.cli import load_json
from agentops.benchmarks.interop import inspect_task


@task
def headroom_text(taskset: str):
    return inspect_task(load_json(taskset))
