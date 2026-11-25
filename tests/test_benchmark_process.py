import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows job-object ownership regression")
def test_controller_crash_closes_last_job_handle_and_stops_children(tmp_path):
    from ctypes import wintypes

    from agentops.benchmarks.process import WindowsJob

    root = Path(__file__).parents[1]
    pidfile, jobfile = tmp_path / "pid", tmp_path / "job"
    child = "import os,sys,time;from pathlib import Path;Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(60)"
    controller = """import os,pathlib,subprocess,sys,time
sys.path.insert(0,sys.argv[1])
from agentops.benchmarks.process import WindowsJob
job=WindowsJob();pathlib.Path(sys.argv[3]).write_text(job.name)
wrapper=pathlib.Path(sys.argv[1])/'agentops/benchmarks/process.py'
subprocess.Popen([sys.executable,'-I',str(wrapper),job.name,sys.executable,'-c',sys.argv[4],sys.argv[2]])
deadline=time.monotonic()+10
while not pathlib.Path(sys.argv[2]).exists():
 if time.monotonic()>deadline: job.terminate();sys.exit(1)
 time.sleep(0.01)
os._exit(0)
"""
    subprocess.run(
        [
            sys.executable,
            "-c",
            controller,
            str(root),
            str(pidfile),
            str(jobfile),
            child,
        ],
        check=True,
        timeout=20,
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, int(pidfile.read_text()))
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel.CloseHandle(handle)
    try:
        remaining = WindowsJob(jobfile.read_text())
    except OSError:
        return
    try:
        remaining.terminate()
    finally:
        remaining.close()
    pytest.fail("controller crash left the named job alive")
