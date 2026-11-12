"""Owned external processes with bounded waits; no shell evaluation."""

import subprocess
import json
import os
import socket
from pathlib import Path


def _started(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:
                return 'missing', None
            return 'unknown', None
        try:
            creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            if not kernel.GetProcessTimes(handle, *map(ctypes.byref, (creation, exit_time, kernel_time, user_time))):
                return 'unknown', None
            if exit_time.dwHighDateTime or exit_time.dwLowDateTime:
                return 'missing', None
            return 'live', str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return 'missing', None
        return 'live', fields[19]
    except FileNotFoundError:
        return 'missing', None
    except (OSError, IndexError):
        return 'unknown', None


def process_identity():
    state, started = _started(os.getpid())
    if state != 'live' or started is None:
        raise RuntimeError('Cannot establish coordinator process identity')
    return {'host': socket.gethostname(), 'pid': os.getpid(), 'started': started}


def owner_state(owner):
    try:
        identity = json.loads(owner)
        if identity['host'] != socket.gethostname() or type(identity['pid']) is not int:
            return 'unknown'
        if not identity.get('started'):
            return 'unknown'
        state, started = _started(identity['pid'])
        if state == 'live' and str(identity.get('started')) != started:
            return 'missing'
        return state
    except (ValueError, KeyError, TypeError):
        return 'unknown'


def command(args, cwd=None, env=None, timeout=120, input=None, combine_output=False):
    completed = subprocess.run([str(a) for a in args], cwd=cwd, env=env, input=input,
                               stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT if combine_output else subprocess.PIPE,
                               encoding='utf8', errors='replace', timeout=timeout)
    if completed.returncode:
        detail = completed.stdout if combine_output else completed.stderr
        raise RuntimeError(f'{args[0]} failed ({completed.returncode}): {detail[-3000:]}')
    return completed.stdout.strip()
