"""Copied to an SSH run directory to supervise the benchmark CLI child."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    results = root / "results"
    results.mkdir(exist_ok=True)
    state = results / "worker.json"
    child = None
    cancelled = False
    code = 70
    error = False

    def record(status):
        temporary = state.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "child_pid": child.pid if child else None,
                    "status": status,
                    "exit_code": code if status == "terminal" else None,
                    "error": error,
                    "cancelled": cancelled,
                }
            )
        )
        temporary.replace(state)

    def cancel(signum, frame):
        nonlocal cancelled
        cancelled = True
        if child is not None and child.poll() is None:
            child.terminate()

    record("running")
    try:
        credentials = Path.home() / ".config/headroom-benchmark/env.json"
        if credentials.exists():
            if credentials.stat().st_mode & 0o077:
                raise ValueError("remote credential file must have mode 0600")
            values = json.loads(credentials.read_text())
            if not isinstance(values, dict) or any(
                not isinstance(v, str) for v in values.values()
            ):
                raise ValueError("invalid remote environment")
            os.environ.update(values)
        os.environ["HEADROOM_BENCH_RUNNER"] = "ssh"
        signal.signal(signal.SIGTERM, cancel)
        # A lost SSH connection is observation loss, not an execution cancel.
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        child = subprocess.Popen(
            [sys.executable, "-m", "agentops.benchmarks.cli", *sys.argv[1:]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        record("running")
        if cancelled:
            child.terminate()
        code = child.wait()
    except KeyboardInterrupt:
        cancel(signal.SIGTERM, None)
        code = child.wait() if child is not None else 130
    except Exception:  # noqa: BLE001 -- persist only exit state, never credential/error text
        error = True
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                code = child.wait(timeout=75)
            except subprocess.TimeoutExpired:
                error = True
        record("running" if child is not None and child.poll() is None else "terminal")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
