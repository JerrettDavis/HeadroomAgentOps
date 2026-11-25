"""Windows process containment for candidate proxies and their helpers.

The wrapper assigns itself to the named job BEFORE spawning any candidate code.
This avoids the venv-launcher race inherent in assigning a running launcher.
"""

import subprocess
import sys
import time
import uuid
from typing import ClassVar


class WindowsJob:
    def __init__(self, name=None):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.OpenJobObjectW.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel.OpenJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self.kernel.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.name = name or "Local\\HeadroomBenchmark-" + uuid.uuid4().hex
        self.handle = (
            self.kernel.OpenJobObjectW(0x001F003F, False, self.name)
            if name
            else self.kernel.CreateJobObjectW(None, self.name)
        )
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if not name:

            class Basic(ctypes.Structure):
                _fields_ = [
                    ("process_time", ctypes.c_int64),
                    ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD),
                    ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t),
                    ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD),
                ]

            class Extended(ctypes.Structure):
                _fields_ = [
                    ("basic", Basic),
                    ("io", ctypes.c_uint64 * 6),
                    ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t),
                    ("peak_job_memory", ctypes.c_size_t),
                ]

            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                self.close()
                raise ctypes.WinError(ctypes.get_last_error())

    def assign_current(self):
        if not self.kernel.AssignProcessToJobObject(
            self.handle, self.kernel.GetCurrentProcess()
        ):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def terminate(self):
        if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        # TerminateJobObject is asynchronous. Wait for every member, not just
        # the wrapper, before deleting SQLite stores and loaded native modules.
        from ctypes import wintypes

        class Accounting(self.ctypes.Structure):
            _fields_: ClassVar[list[tuple[str, object]]] = [
                ("times", self.ctypes.c_int64 * 4),
                ("faults", wintypes.DWORD),
                ("total", wintypes.DWORD),
                ("active", wintypes.DWORD),
                ("terminated", wintypes.DWORD),
            ]

        deadline = time.monotonic() + 15
        while self.handle:
            state = Accounting()
            if not self.kernel.QueryInformationJobObject(
                self.handle,
                1,
                self.ctypes.byref(state),
                self.ctypes.sizeof(state),
                None,
            ):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
            if state.active == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("candidate process job did not terminate")
            time.sleep(0.01)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


if __name__ == "__main__":
    job = WindowsJob(sys.argv[1])
    try:
        job.assign_current()
        # Keep the controller as sole owner: a hard controller exit must close
        # the last handle and kill this wrapper and every candidate helper.
        job.close()
        raise SystemExit(subprocess.call(sys.argv[2:]))
    finally:
        job.close()
