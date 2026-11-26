"""Private, durable subprocess jobs for the explicit benchmark dashboard actions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TERMINAL = {"completed", "failed", "recoverable", "cancelled", "interrupted"}
_PUBLIC = (
    "id",
    "label",
    "action",
    "state",
    "created_at",
    "finished_at",
    "exit_code",
    "decision",
    "paths",
    "error",
)
_THREAD_LOCK = threading.RLock()
_FIELDS = {
    "source": {"source"},
    "demo": {"design"},
    "inspect": {
        "artifact",
        "config",
        "sha256",
        "transport",
        "image",
        "host",
        "ssh_port",
        "ssh_config",
        "cpus",
        "memory_gib",
        "env",
    },
    "create": {
        "inspection_id",
        "inspection_path",
        "candidate_sha",
        "model",
        "tasks",
        "policy",
        "experiment_id",
        "seed",
        "repeats",
    },
    "run": {
        "artifact",
        "manifest",
        "experiment_id",
        "config",
        "transport",
        "image",
        "host",
        "ssh_port",
        "ssh_config",
        "cpus",
        "memory_gib",
        "env",
    },
    "collect": {"run_id"},
    "export": {"tasks", "format"},
    "import": {"dataset", "taskset_id", "revision"},
}


@contextmanager
def _guard(root):
    with _THREAD_LOCK, (root / "slot.guard").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            if not handle.read(1):
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _now():
    return time.time()


def _write(path, data):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, allow_nan=False), encoding="utf-8")
    # Windows readers briefly deny replacement even though their handles are
    # closed immediately. Keep the atomic write and retry the sharing violation.
    deadline = time.monotonic() + 5
    while True:
        try:
            os.replace(temp, path)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.02)


def _decision(raw):
    if not isinstance(raw, dict):
        return {"status": "inconclusive"}
    result = {
        "status": raw.get("status")
        if raw.get("status") in {"pass", "fail", "inconclusive"}
        else "inconclusive"
    }
    for key in (
        "pairs",
        "mean_savings",
        "savings_interval",
        "accuracy_regression_interval",
        "latency_ratio_interval",
    ):
        value = raw.get(key)
        if (
            type(value) in (int, float)
            and math.isfinite(value)
            or isinstance(value, (list, tuple))
            and len(value) == 2
            and all(type(v) in (int, float) and math.isfinite(v) for v in value)
        ):
            result[key] = value
    if isinstance(raw.get("simulated_decision"), dict):
        result["simulated_decision"] = _decision(raw["simulated_decision"])
    return result


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False

    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return (
                bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                and code.value == 259
            )
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _result(directory, summary, exit_code, cancelled):
    """Only a canonical hashed native result can support a displayed decision."""
    from agentops.benchmarks.core import digest

    inconclusive = ({"status": "inconclusive"}, None)
    name = summary.get("result_file")
    if not isinstance(name, str) or Path(name).name != name:
        return inconclusive
    path = directory / name
    if not path.is_file() or path.is_symlink():
        return inconclusive
    raw = path.read_bytes()
    evidence = json.loads(raw)
    observed = "sha256:" + hashlib.sha256(raw).hexdigest()
    if observed != summary.get("result_sha256") or observed != digest(evidence):
        return inconclusive
    decision = _decision(evidence.get("decision"))
    expected = {"pass": 0, "fail": 1, "inconclusive": 2}.get(decision["status"])
    if (
        cancelled
        or expected != exit_code
        or summary.get("status") != decision["status"]
    ):
        decision["status"] = "inconclusive"
    if summary.get("lane") == "simulator" or evidence.get("lane") == "simulator":
        decision.update(status="inconclusive", lane="simulator")
    return decision, str(path)


class BenchmarkService:
    def __init__(self, ledger_path):
        self.root = Path(ledger_path).resolve().parent / "benchmarks"
        self.root.mkdir(parents=True, exist_ok=True)
        self.slot = self.root / "active.lock"

    def _jobs(self):
        jobs = []
        for path in self.root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text("utf-8"))
                if (
                    job["state"] not in _TERMINAL
                    and time.time() - job["started_epoch"] > 60
                    and not _alive(job.get("supervisor_pid"))
                    and not _alive(job.get("child_pid"))
                ):
                    job.update(
                        state="interrupted",
                        finished_at=_now(),
                        error="Supervisor stopped without terminal proof; evidence remains inconclusive.",
                    )
                    _write(path, job)
                jobs.append(job)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(jobs, key=lambda j: j["created_at"], reverse=True)

    @staticmethod
    def _public(job):
        return {key: job.get(key) for key in _PUBLIC}

    def status(self):
        jobs = self._jobs()
        return {
            "jobs": [self._public(j) for j in jobs],
            "active": any(j["state"] not in _TERMINAL for j in jobs),
            "defaults": {
                "tasks": str(_ROOT / "benchmarks/tasksets/protocol-smoke-v1.json")
                if (_ROOT / "benchmarks/tasksets/protocol-smoke-v1.json").is_file()
                else "",
                "policy": str(_ROOT / "benchmarks/policies/smoke-v1.json")
                if (_ROOT / "benchmarks/policies/smoke-v1.json").is_file()
                else "",
                "config": "",
            },
        }

    def _prior(self, value, action=None):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError("Invalid job identifier")
        matches = [
            j
            for j in self._jobs()
            if j["id"] == value and (action is None or j["action"] == action)
        ]
        if not matches:
            raise ValueError("Referenced job is unavailable")
        return matches[0]

    def _argv(self, payload, directory):
        if isinstance(payload, dict) and payload.get("action") == "source":
            from .benchmark_resolver import validate_source
            if set(payload) != {"action", "source"}:
                raise ValueError("Source preparation requires only a source selection")
            source = validate_source(payload["source"])
            return (["resolve-source", "--source", json.dumps(source), "--output", str(directory)],
                    {"source": str(directory / "source.json"), "inspection": None,
                     "manifest": None, "result": None, "dataset": None})
        if isinstance(payload, dict) and payload.get("action") == "demo":
            from .benchmark_studio import validate_design
            if set(payload) != {"action", "design"}:
                raise ValueError("Demo requires only an experiment design")
            design = validate_design(payload["design"])
            return (["demo", "--design", json.dumps(design), "--output", str(directory)],
                    {"inspection": None, "manifest": None, "result": None, "dataset": None})
        if not isinstance(payload, dict) or payload.get("action") not in _FIELDS:
            raise ValueError("Unknown benchmark action")
        action = payload["action"]
        if set(payload) - (_FIELDS[action] | {"action"}):
            raise ValueError("Unknown benchmark field")
        data = dict(payload)
        for key, value in data.items():
            if key == "env":
                if (
                    not isinstance(value, list)
                    or len(value) > 64
                    or not all(
                        isinstance(v, str)
                        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v)
                        for v in value
                    )
                ):
                    raise ValueError("Environment entries must be variable names only")
            elif key in {"seed", "repeats", "ssh_port"}:
                if type(value) is not int or not (
                    0 <= value <= 65535 if key != "repeats" else 1 <= value <= 1000
                ):
                    raise ValueError("Invalid numeric field")
            elif key in {"cpus", "memory_gib"}:
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 < value <= 1024
                ):
                    raise ValueError("Invalid resource limit")
            elif (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 4096
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("Invalid text field")

        def file(key):
            value = data.get(key)
            if not value or not Path(value).expanduser().resolve().is_file():
                raise ValueError("Required input file is unavailable: " + key)
            data[key] = str(Path(value).expanduser().resolve())
            return data[key]

        def need(key):
            if key not in data:
                raise ValueError("Missing required field: " + key)
            return data[key]

        def options(keys):
            result = []
            for key in keys:
                if key in data:
                    result += ["--" + key.replace("_", "-"), str(data[key])]
            return result

        paths = {"inspection": None, "manifest": None, "result": None, "dataset": None}
        if action in {"inspect", "run"}:
            file("artifact")
            for key in ("config", "ssh_config"):
                if key in data:
                    file(key)
            if data.get("transport", "local") not in {"local", "docker", "ssh"}:
                raise ValueError("Invalid transport")
            if "host" in data and not re.fullmatch(
                r"[A-Za-z0-9_.@:\[\]-]+", data["host"]
            ):
                raise ValueError("Invalid SSH host")
            if action == "inspect":
                actual = (
                    "sha256:"
                    + hashlib.sha256(Path(data["artifact"]).read_bytes()).hexdigest()
                )
                if "sha256" in data and data["sha256"].removeprefix(
                    "sha256:"
                ) != actual.removeprefix("sha256:"):
                    raise ValueError("Artifact SHA256 does not match")
                data["sha256"] = actual
                argv = ["inspect"]
                paths["inspection"] = str(directory / "inspection.json")
            else:
                if "experiment_id" in data:
                    if "manifest" in data:
                        raise ValueError("Choose a manifest or experiment job")
                    data["manifest"] = self._prior(data["experiment_id"], "create")[
                        "paths"
                    ]["manifest"]
                argv = ["run", file("manifest")]
            argv += options(
                (
                    "artifact",
                    "config",
                    "sha256",
                    "transport",
                    "image",
                    "host",
                    "ssh_port",
                    "ssh_config",
                    "cpus",
                    "memory_gib",
                )
            ) + ["--output", str(directory)]
            for name in data.get("env", []):
                argv += ["--env", name]
        elif action == "create":
            if "inspection_id" in data:
                if "inspection_path" in data:
                    raise ValueError("Choose an inspection path or job")
                data["inspection_path"] = self._prior(data["inspection_id"], "inspect")[
                    "paths"
                ]["inspection"]
            data["inspection"] = file("inspection_path")
            for key in ("tasks", "policy"):
                file(key)
            for key in ("candidate_sha", "model", "experiment_id"):
                need(key)
            paths["manifest"] = str(directory / "manifest.json")
            argv = (
                ["init"]
                + options(
                    (
                        "inspection",
                        "candidate_sha",
                        "model",
                        "tasks",
                        "policy",
                        "experiment_id",
                        "seed",
                        "repeats",
                    )
                )
                + ["--output", paths["manifest"]]
            )
        elif action == "collect":
            prior = self._prior(need("run_id"))
            receipts = [
                self.root / prior["id"] / name
                for name in ("docker-receipt.json", "remote-receipt.json")
            ]
            receipt = next((p for p in receipts if p.is_file()), None)
            if receipt is None:
                raise ValueError("No collectable transport receipt exists")
            argv = ["collect", str(receipt), "--output", str(directory)]
            if prior["action"] == "inspect":
                paths["inspection"] = str(directory / "inspection.json")
        elif action == "export":
            if need("format") not in {"inspect", "aiperf"}:
                raise ValueError("Invalid dataset format")
            paths["dataset"] = str(directory / "dataset.jsonl")
            argv = [
                "export-dataset",
                file("tasks"),
                "--format",
                data["format"],
                "--output",
                paths["dataset"],
            ]
        else:
            paths["dataset"] = str(directory / "taskset.json")
            argv = [
                "import-inspect",
                file("dataset"),
                "--taskset-id",
                need("taskset_id"),
                "--revision",
                need("revision"),
                "--output",
                paths["dataset"],
            ]
        return argv, paths

    def submit(self, payload):
        with _guard(self.root):
            return self._submit(payload)

    def _submit(self, payload):
        identifier = uuid.uuid4().hex
        directory = self.root / identifier
        argv, paths = self._argv(payload, directory)
        try:
            fd = os.open(self.slot, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            jobs = self._jobs()
            if any(j["state"] not in _TERMINAL for j in jobs):
                raise ValueError("A benchmark job is already active")
            try:
                owner = self.slot.read_text("utf-8")
                if owner and any(
                    j["id"] == owner and j["state"] in _TERMINAL for j in jobs
                ):
                    self.slot.unlink()
                else:
                    raise ValueError("Benchmark slot unavailable; inspect local state")
            except OSError:
                raise ValueError("Benchmark slot unavailable") from None
            return self._submit(payload)
        with os.fdopen(fd, "w") as stream:
            stream.write(identifier)
        directory.mkdir(mode=0o700)
        job = {
            "id": identifier,
            "action": payload["action"],
            "state": "queued",
            "created_at": _now(),
            "finished_at": None,
            "exit_code": None,
            "decision": None,
            "paths": paths,
            "error": None,
            "argv": argv,
            "started_epoch": time.time(),
        }
        if payload["action"] in {"demo", "source"}:
            settings = json.loads(argv[2])
            job["label"] = settings["label"] if payload["action"] == "demo" else (
                f'{settings["repository"]} @ {settings["sha"][:10]} · {settings["kind"]}'
            )
        _write(directory / "job.json", job)
        try:
            flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                if os.name == "nt"
                else 0
            )
            with (directory / "supervisor.log").open("ab") as log:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "agentops.benchmark_service",
                        str(self.root),
                        identifier,
                    ],
                    cwd=_ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    creationflags=flags,
                    start_new_session=os.name != "nt",
                    close_fds=True,
                )
        except OSError:
            job.update(
                state="failed",
                finished_at=_now(),
                error="Unable to start benchmark supervisor.",
            )
            _write(directory / "job.json", job)
            self.slot.unlink(missing_ok=True)
        return self._public(job)

    def cancel(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"id"}:
            raise ValueError("Cancellation requires a job identifier")
        job = self._prior(payload["id"])
        if job["state"] not in _TERMINAL:
            if (
                job.get("supervisor_pid")
                and not _alive(job["supervisor_pid"])
                and _alive(job.get("child_pid"))
            ):
                raise ValueError(
                    "Supervisor unavailable; owned work may still be running. Inspect local evidence before recovery."
                )
            (self.root / job["id"] / "cancel").touch()
        return self._public(job)


def _supervise(root, identifier):
    directory = root / identifier
    path = directory / "job.json"
    job = json.loads(path.read_text("utf-8"))
    job.update(state="running", supervisor_pid=os.getpid())
    _write(path, job)
    child = None
    cancelled = False
    try:
        with (directory / "command.log").open("ab") as log:
            flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE
                if os.name == "nt"
                else 0
            )
            startup = None
            if os.name == "nt":
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
            if (directory / "cancel").exists():
                job.update(state="cancelled", finished_at=_now())
                return
            child = subprocess.Popen(
                [sys.executable, "-m", "agentops.benchmarks.cli", *job["argv"]],
                cwd=_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=flags,
                startupinfo=startup,
                start_new_session=os.name != "nt",
            )
            job["child_pid"] = child.pid
            _write(path, job)
            requested_at = None
            while child.poll() is None:
                if (directory / "cancel").exists():
                    cancelled = True
                    if requested_at is None:
                        requested_at = time.monotonic()
                        if os.name == "nt":
                            import ctypes

                            kernel = ctypes.windll.kernel32
                            if kernel.AttachConsole(child.pid):
                                try:
                                    kernel.GenerateConsoleCtrlEvent(
                                        signal.CTRL_BREAK_EVENT, child.pid
                                    )
                                finally:
                                    kernel.FreeConsole()
                        else:
                            os.killpg(child.pid, signal.SIGTERM)
                    elif time.monotonic() - requested_at > 70:
                        child.kill()
                time.sleep(0.1)
            job["exit_code"] = child.returncode
        summary_path = directory / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text("utf-8"))
            job["decision"], job["paths"]["result"] = _result(
                directory, summary, child.returncode, cancelled
            )
        receipts = any(
            (directory / name).is_file()
            for name in ("docker-receipt.json", "remote-receipt.json")
        )
        evidence_complete = job["paths"]["result"] is not None and child.returncode in (
            0,
            1,
            2,
        )
        job["state"] = (
            "cancelled"
            if cancelled
            else (
                "completed"
                if child.returncode == 0 or evidence_complete
                else "recoverable"
                if receipts
                else "failed"
            )
        )
        if job["state"] == "failed":
            job["error"] = (
                "Benchmark command failed; private diagnostics are retained locally."
            )
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()
        job.update(
            state="interrupted",
            error="Supervisor could not establish terminal proof; evidence remains inconclusive.",
        )
    finally:
        job["finished_at"] = _now()
        _write(path, job)
        slot = root / "active.lock"
        try:
            with _guard(root):
                if slot.read_text("utf-8") == identifier:
                    slot.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    _supervise(Path(sys.argv[1]), sys.argv[2])
