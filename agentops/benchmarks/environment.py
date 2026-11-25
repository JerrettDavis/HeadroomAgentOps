"""Observe worker identity without trusting experiment declarations.

The standalone probe runs under the installed candidate's interpreter. Local
limits describe available resources; Docker limits are enforced by the engine
and observed through cgroups. Trust in host/engine remains with the producer.
"""

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


def _probe():
    cpu = float(
        len(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else os.cpu_count() or 1
    )
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                *[
                    (name, ctypes.c_ulonglong)
                    for name in (
                        "total",
                        "available",
                        "page",
                        "available_page",
                        "virtual",
                        "available_virtual",
                        "extended",
                    )
                ],
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("cannot observe physical memory")
        memory = status.total
    else:
        memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    root = Path("/sys/fs/cgroup")
    for path in (root / "memory.max", root / "memory/memory.limit_in_bytes"):
        if path.exists():
            value = path.read_text().strip()
            if value.isdigit():
                memory = min(memory, int(value))
    quota = root / "cpu.max"
    if quota.exists():
        maximum, period = quota.read_text().split()
        if maximum != "max":
            cpu = min(cpu, int(maximum) / int(period))
    elif (root / "cpu/cpu.cfs_quota_us").exists():
        maximum = int((root / "cpu/cpu.cfs_quota_us").read_text())
        period = int((root / "cpu/cpu.cfs_period_us").read_text())
        if maximum > 0:
            cpu = min(cpu, maximum / period)
    packages = []
    for dist in importlib.metadata.distributions():
        records = sorted(
            (str(f), str(f.hash))
            for f in dist.files or []
            if f.hash and ".." not in f.parts and not str(f).endswith("direct_url.json")
        )
        packages.append([dist.metadata["Name"], dist.version, records])
    with open(sys.executable, "rb") as stream:
        interpreter = hashlib.file_digest(stream, "sha256").hexdigest()
    fingerprint = hashlib.sha256(
        json.dumps([interpreter, sorted(packages)], separators=(",", ":")).encode()
    ).hexdigest()
    machine = platform.machine().lower()
    machine = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
    image_digest = os.environ.get(
        "HEADROOM_BENCH_IMAGE_DIGEST", "sha256:" + fingerprint
    )
    return {
        "runner": os.environ.get("HEADROOM_BENCH_RUNNER", "local"),
        "platform": platform.system().lower() + "/" + machine,
        "image": f"python:{platform.python_version()};packages:sha256:{fingerprint}",
        "image_digest": image_digest,
        "resource_limits": {"cpu": cpu, "memory_gib": memory / 1024**3},
    }


def describe_environment(python):
    result = subprocess.run(
        [str(python), "-I", str(Path(__file__).resolve())],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def agent_identity():
    payload = b"".join(
        p.name.encode() + b"\0" + p.read_bytes()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    )
    return "headroom-agentops/http-v1@sha256:" + hashlib.sha256(payload).hexdigest()


if __name__ == "__main__":
    print(json.dumps(_probe(), sort_keys=True))
