"""One-use idle coordinator handoff; never creates plans or changes job states.

Capture is read-only apart from the private receipt. Activation is explicitly
destructive and must only be called during an authorized maintenance window.
The new panel consumes before starting any automatic scheduling threads.
"""

import contextlib
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

from .process import _started, owner_state


def _save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        if os.name != 'nt':
            os.chmod(temporary, 0o600)
        json.dump(value, stream)
    temporary.replace(path)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _identity(pid):
    state, started = _started(pid)
    if state != 'live' or started is None:
        raise ValueError('Process identity cannot be verified')
    return {'host': socket.gethostname(), 'pid': pid, 'started': started}


def _command(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def discover(root):
    """Return only typed process metadata. Raw command lines never leave memory."""
    root = Path(root).resolve()
    if os.name != 'nt':
        raise ValueError('Automatic process discovery currently requires Windows')
    script = ('Get-CimInstance Win32_Process | Where-Object { $_.Name -match "python" '
              '-and $_.CommandLine -match "agentops" } | Select-Object '
              'ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress')
    result = _command(['powershell', '-NoProfile', '-NonInteractive', '-Command', script])
    if result.returncode:
        raise ValueError('Process inspection unavailable')
    rows = json.loads(result.stdout or '[]')
    if isinstance(rows, dict):
        rows = [rows]
    candidates = []
    for row in rows:
        command = row.get('CommandLine') or ''
        if not re.search(r'-m\s+agentops\b', command):
            continue
        state = re.search(r'--state\s+("[^"]+"|\S+)', command)
        if not state:
            continue
        value = state[1].strip('"')
        if Path(value).is_absolute():
            if Path(value).resolve() != root:
                continue
            direct = True
        elif value != root.name:
            continue
        else:
            direct = str(root.parent).lower() in command.lower()
        plan = re.search(r'--plan\s+([a-f0-9]{32})(?:\s|$)', command)
        role = 'coordinator' if plan else ('panel' if re.search(r'\bpanel\b', command) else 'unknown')
        if role == 'unknown':
            raise ValueError('Unrecognized process uses the shared review state')
        candidates.append(({**_identity(int(row['ProcessId'])), 'parent_pid': int(row['ParentProcessId']),
                            'role': role, 'run_id': plan[1] if plan else None}, direct))
    selected = [process for process, direct in candidates if direct]
    while True:
        known = {p['pid'] for p in selected}
        children = [p for p, _ in candidates if p['pid'] not in known and p['parent_pid'] in known]
        if not children:
            break
        selected.extend(children)
    return selected


def _reviewers():
    result = _command(['docker', 'ps', '--filter', 'label=agentops.session', '--format', '{{.ID}}'])
    if result.returncode:
        raise ValueError('Reviewer container inspection unavailable')
    return result.stdout.split()


def _source():
    directory = Path(__file__).resolve().parent.parent
    commit = _command(['git', '-C', str(directory), 'rev-parse', 'HEAD'])
    clean = _command(['git', '-C', str(directory), 'status', '--porcelain', '--untracked-files=no'])
    if commit.returncode or clean.returncode or clean.stdout.strip() or not re.fullmatch(r'[a-f0-9]{40}', commit.stdout.strip()):
        raise ValueError('Coordinator reload requires a clean committed source checkpoint')
    return {'root': str(directory), 'commit': commit.stdout.strip()}


@contextlib.contextmanager
def _locked(root):
    connection = sqlite3.connect(Path(root) / 'ledger.sqlite3', timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _snapshot(connection, repo):
    if connection.execute("SELECT count(*) FROM jobs WHERE state IN ('running','starting')").fetchone()[0]:
        raise ValueError('Reload requires zero active jobs')
    rows = [dict(row) for row in connection.execute("SELECT id,run_id,repo,number,head_sha,main_sha,state,stale "
                                                    "FROM jobs WHERE state='queued' ORDER BY id")]
    if any(row['repo'] != repo for row in rows):
        raise ValueError('Queued jobs belong to another repository')
    runs = {}
    for identity in sorted({row['run_id'] for row in rows}):
        run = connection.execute('SELECT config,stopped FROM runs WHERE id=?', (identity,)).fetchone()
        config = json.loads(run['config'])
        if run['stopped'] or config.get('repo') != repo:
            raise ValueError('Queued run is stopped or repository differs')
        runs[identity] = _digest(config)
    return rows, runs


def _check(connection, receipt):
    rows, runs = _snapshot(connection, receipt['repo'])
    if rows != receipt['queued'] or runs != receipt['runs']:
        raise ValueError('Queued jobs or saved run configuration changed after capture')
    if _reviewers():
        raise ValueError('Reviewer containers are active')


def capture(root, repo):
    """Capture exact queued assignments and process identities without stopping anything."""
    root = Path(root).resolve()
    path = root / 'coordinator-reload.json'
    if path.exists():
        raise ValueError('An unconsumed coordinator handoff already exists')
    with _locked(root) as connection:
        if path.exists():
            raise ValueError('An unconsumed coordinator handoff already exists')
        queued, runs = _snapshot(connection, repo)
        if _reviewers():
            raise ValueError('Reviewer containers are active')
        processes = discover(root)
        if any(p['run_id'] not in runs for p in processes if p['role'] == 'coordinator'):
            raise ValueError('A coordinator outside the queued run set is still active')
        if not any(p['role'] == 'panel' for p in processes):
            raise ValueError('Cannot identify the existing panel')
        if {p['run_id'] for p in processes if p['role'] == 'coordinator'} != set(runs):
            raise ValueError('Queued run controllers do not match the captured process set')
        for process in processes:
            if owner_state(json.dumps(process)) != 'live':
                raise ValueError('Captured process identity is not live')
        auto = json.loads((root / 'auto-review.json').read_text(encoding='utf-8'))
        if auto.get('repo') != repo:
            raise ValueError('Auto-review repository differs')
        receipt = {'schema': 1, 'status': 'captured', 'root': str(root), 'repo': repo,
                   'queued': queued, 'runs': runs, 'processes': processes,
                   'watch_settings': auto['settings'], 'created': time.time(), 'launched': {}, 'source': _source()}
        _save(path, receipt)
    return path


def _load(path, root):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if value.get('schema') != 1 or Path(value.get('root', '')).resolve() != Path(root).resolve():
        raise ValueError('Invalid coordinator handoff identity')
    return value


def _terminate(process):
    # Revalidate immediately before the OS call; never terminate a reused PID.
    if owner_state(json.dumps(process)) != 'live':
        raise ValueError('Refusing to terminate an unverified process')
    if os.name != 'nt':
        raise ValueError('Automatic process termination currently requires Windows')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(1 | 0x1000, False, process['pid'])
    if not handle:
        raise ValueError('Cannot open verified process for idle reload')
    try:
        if owner_state(json.dumps(process)) != 'live' or not kernel.TerminateProcess(handle, 0):
            raise ValueError('Idle process termination failed')
    finally:
        kernel.CloseHandle(handle)


def activate(root):
    """GO-only: stop captured idle controllers and old panel under the claim lock.

    First disable new_prs/updated_prs through the existing panel API. No run is
    marked stopped; original watch settings are restored by the new panel.
    """
    root = Path(root).resolve()
    path = root / 'coordinator-reload.json'
    receipt = _load(path, root)
    if receipt['status'] != 'captured':
        raise ValueError('Handoff is not awaiting activation')
    if receipt.get('source') != _source():
        raise ValueError('Source checkpoint changed before activation')
    with _locked(root) as connection:
        _check(connection, receipt)
        auto = json.loads((root / 'auto-review.json').read_text(encoding='utf-8'))
        expected = {**receipt['watch_settings'], 'new_prs': False, 'updated_prs': False}
        if auto.get('settings') != expected:
            raise ValueError('Disable only automatic watch triggers before activation')
        if sorted(discover(root), key=lambda p: p['pid']) != sorted(receipt['processes'], key=lambda p: p['pid']):
            raise ValueError('Process identities or run mapping changed after capture')
        # Children first; panel last. Claim lock prevents new jobs throughout.
        children = {p['parent_pid'] for p in receipt['processes']}
        ordered = sorted(receipt['processes'], key=lambda p: (p['role'] == 'panel', p['pid'] in children))
        for process in ordered:
            state = owner_state(json.dumps(process))
            if state == 'live':
                _terminate(process)
            elif state != 'missing':
                raise ValueError('Process state became unobservable; maintenance incomplete')
        for _ in range(50):
            if all(owner_state(json.dumps(p)) == 'missing' for p in receipt['processes']):
                break
            time.sleep(.1)
        else:
            raise ValueError('Old processes did not exit; handoff cannot be consumed')
        _check(connection, receipt)
        receipt['status'] = 'ready'
        _save(path, receipt)
    return {'status': 'ready', 'run_ids': sorted(receipt['runs']), 'queued_count': len(receipt['queued'])}


def consume(launcher, handoff_path=None):
    """New panel startup, BEFORE AutoReview: register exact saved plans once."""
    root = launcher.root.resolve()
    path = Path(handoff_path) if handoff_path else root / 'coordinator-reload.json'
    if path.resolve() != root / 'coordinator-reload.json':
        raise ValueError('Handoff must be in the current state directory')
    if not path.exists():
        return {'status': 'absent', 'run_ids': []}
    receipt = _load(path, root)
    if receipt['status'] != 'ready' or receipt['repo'] != launcher.config['repo']:
        raise ValueError('Handoff is not ready for this panel')
    if receipt.get('source') != _source():
        raise ValueError('Panel source differs from the verified handoff checkpoint')
    if any(owner_state(json.dumps(p)) != 'missing' for p in receipt['processes']):
        raise ValueError('Old controllers are still alive or unobservable')
    with launcher.lock, _locked(root) as connection:
        _check(connection, receipt)
        others = [p for p in discover(root) if p['role'] == 'coordinator']
        if others:
            raise ValueError('Another coordinator appeared before startup handoff')
        auto_path = root / 'auto-review.json'
        auto = json.loads(auto_path.read_text(encoding='utf-8'))
        expected = {**receipt['watch_settings'], 'new_prs': False, 'updated_prs': False}
        if auto.get('settings') != expected:
            raise ValueError('Automatic watch settings changed before handoff')
        (root / 'launches').mkdir(exist_ok=True)
        attempted = set()
        try:
            for run_id in sorted(receipt['runs']):
                attempted.add(run_id)
                result = launcher._spawn({'run_id': run_id,
                    'selected': [j['number'] for j in receipt['queued'] if j['run_id'] == run_id],
                    'log': str(root / 'launches' / (run_id + '.log'))})
                process = launcher.processes.get(run_id)
                if result.get('error') or process is None or process.poll() is not None:
                    raise ValueError('A saved coordinator could not be started; automatic watches remain disabled')
                receipt['launched'][run_id] = _identity(process.pid)
                _save(path, receipt)
        except BaseException:
            receipt['status'] = 'partial'
            _save(path, receipt)
            # No child can claim while this transaction holds the writer lock.
            # Roll back only freshly launched, command-verified controllers.
            try:
                fresh = [p for p in discover(root) if p['role'] == 'coordinator' and p['run_id'] in attempted]
                parents = {p['parent_pid'] for p in fresh}
                for process in sorted(fresh, key=lambda p: p['pid'] in parents):
                    if owner_state(json.dumps(process)) == 'live':
                        _terminate(process)
                for _ in range(50):
                    if all(owner_state(json.dumps(p)) == 'missing' for p in fresh):
                        break
                    time.sleep(.1)
                else:
                    raise ValueError('Fresh controllers could not be rolled back')
                for identity in attempted:
                    launcher.processes.pop(identity, None)
                receipt.update(status='ready', launched={})
                _save(path, receipt)
            except Exception:
                pass  # Partial receipt blocks retry until an operator verifies ownership.
            raise
        auto['settings'] = receipt['watch_settings']
        _save(auto_path, auto)
        receipt['status'] = 'consumed'
        _save(path, receipt)
        archive = path.with_name('coordinator-reload-consumed-' + str(time.time_ns()) + '.json')
        path.replace(archive)
    return {'status': 'consumed', 'run_ids': sorted(receipt['runs']), 'queued_count': len(receipt['queued'])}
