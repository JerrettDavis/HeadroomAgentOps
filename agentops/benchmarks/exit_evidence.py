"""Bind transport-observed process exit to immutable benchmark evidence."""

from .core import digest
from .runner import write_evidence


def exit_matches(execution, status):
    return (
        isinstance(execution, dict)
        and execution.get("status") == "exited"
        and type(execution.get("exit_code")) is int
        and execution["exit_code"]
        == {"pass": 0, "fail": 1, "inconclusive": 2}.get(status)
        and execution.get("oom_killed") is False
        and execution.get("error") is False
    )


def finalize_exit(raw, execution, output, cancelled=False):
    raw["execution"] = execution
    if cancelled or not exit_matches(execution, raw["decision"]["status"]):
        raw["cancelled"] = bool(cancelled or raw.get("cancelled"))
        raw["decision"] = {
            "status": "inconclusive",
            "reason": "controller cancellation"
            if cancelled
            else "worker exit did not qualify",
            "observed_decision": raw["decision"],
        }
    path = write_evidence(raw, output)
    return {
        "status": raw["decision"]["status"],
        "result_file": path.name,
        "result_sha256": digest(raw),
        "lane": raw["lane"],
    }
