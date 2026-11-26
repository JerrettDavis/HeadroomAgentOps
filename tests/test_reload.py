import json
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentops import reload
from agentops.launch import Launcher
from agentops.ledger import Ledger


@pytest.fixture
def state(tmp_path, monkeypatch):
    root = tmp_path / 'state'
    ledger = Ledger(root / 'ledger.sqlite3')
    snapshot = {'repo': 'o/r', 'main_sha': 'a' * 40, 'prs': []}
    sid = ledger.save_snapshot(snapshot)
    runs = []
    for _ in range(2):
        run = ledger.create_run(sid, {'repo': 'o/r', 'publish': True, 'concurrency': 2,
                                     'exclude_authors': ['owner'], 'require_decision': True})
        ledger.enqueue(run, {'repo': 'o/r', 'number': 1, 'head_sha': 'b' * 40}, 'a' * 40)
        runs.append(run)
    watch = {'new_prs': True, 'updated_prs': False, 'non_draft_only': True}
    (root / 'auto-review.json').write_text(json.dumps({'repo': 'o/r', 'settings': watch, 'seen': {'1': 'kept'}}))
    processes = [{'host': socket.gethostname(), 'pid': 101 + i, 'started': 'creation',
                  'parent_pid': 1, 'role': 'coordinator', 'run_id': run} for i, run in enumerate(runs)]
    processes.append({'host': socket.gethostname(), 'pid': 100, 'started': 'panel',
                      'parent_pid': 1, 'role': 'panel', 'run_id': None})
    monkeypatch.setattr(reload, 'discover', lambda root: list(processes))
    monkeypatch.setattr(reload, '_reviewers', lambda: [])
    monkeypatch.setattr(reload, 'owner_state', lambda identity: 'live')
    monkeypatch.setattr(reload, '_source', lambda: {'root': 'verified-source', 'commit': 'a' * 40})
    return root, ledger, runs, watch, processes


def test_capture_keeps_superseded_queued_jobs_without_mutating_settings(state):
    root, ledger, runs, watch, _ = state
    before = (root / 'auto-review.json').read_bytes()
    path = reload.capture(root, 'o/r')
    saved = json.loads(path.read_text())
    assert set(saved['runs']) == set(runs)
    assert len(saved['queued']) == 2
    assert saved['watch_settings'] == watch
    assert (root / 'auto-review.json').read_bytes() == before
    assert len(ledger.jobs()) == 2 and all(j['state'] == 'queued' for j in ledger.jobs())


def test_capture_rejects_live_job_and_creates_no_receipt(state):
    root, ledger, runs, _, _ = state
    ledger.claim('owner', 2, runs[0])
    with pytest.raises(ValueError, match='active'):
        reload.capture(root, 'o/r')
    assert not (root / 'coordinator-reload.json').exists()


def test_consume_exact_saved_runs_registers_and_restores_watch(state, monkeypatch):
    root, ledger, runs, watch, _ = state
    path = reload.capture(root, 'o/r')
    value = json.loads(path.read_text())
    value['status'] = 'ready'
    path.write_text(json.dumps(value))
    auto = json.loads((root / 'auto-review.json').read_text())
    auto['settings'] = {**watch, 'new_prs': False, 'updated_prs': False}
    (root / 'auto-review.json').write_text(json.dumps(auto))
    monkeypatch.setattr(reload, 'owner_state', lambda identity: 'missing')
    monkeypatch.setattr(reload, 'discover', lambda root: [])
    launcher = Launcher(root / 'ledger.sqlite3', {'repo': 'o/r'}, github=Mock())
    called = []
    def spawn(result):
        called.append(result['run_id'])
        process = Mock(pid=300 + len(called))
        process.poll.return_value = None
        launcher.processes[result['run_id']] = process
        return result
    monkeypatch.setattr(launcher, '_spawn', spawn)
    monkeypatch.setattr(reload, '_identity', lambda pid: {'host': socket.gethostname(), 'pid': pid, 'started': 'new'})
    result = launcher.resume_saved_runs()
    assert set(called) == set(runs) and result['status'] == 'consumed'
    assert json.loads((root / 'auto-review.json').read_text())['settings'] == watch
    assert all(j['state'] == 'queued' for j in ledger.jobs())
    assert launcher.resume_saved_runs()['status'] == 'absent'
    assert len(called) == 2
    launcher.github.snapshot.assert_not_called()


def test_consume_refuses_changed_plan_before_spawn(state, monkeypatch):
    root, ledger, runs, _, _ = state
    path = reload.capture(root, 'o/r')
    value = json.loads(path.read_text())
    value['status'] = 'ready'
    path.write_text(json.dumps(value))
    monkeypatch.setattr(reload, 'owner_state', lambda identity: 'missing')
    with ledger.connect(immediate=True) as db:
        config = {**ledger.get_run(runs[0])['config'], 'concurrency': 4}
        db.execute('UPDATE runs SET config=? WHERE id=?', (json.dumps(config), runs[0]))
    launcher = Launcher(root / 'ledger.sqlite3', {'repo': 'o/r'}, github=Mock())
    monkeypatch.setattr(launcher, '_spawn', Mock())
    with pytest.raises(ValueError, match='changed'):
        launcher.resume_saved_runs()
    launcher._spawn.assert_not_called()


def test_activate_aborts_if_watch_still_enabled_without_termination(state, monkeypatch):
    root, _, _, _, _ = state
    reload.capture(root, 'o/r')
    terminate = Mock()
    monkeypatch.setattr(reload, '_terminate', terminate)
    with pytest.raises(ValueError, match='watch'):
        reload.activate(root)
    terminate.assert_not_called()


def test_consume_refuses_live_old_controller_without_spawning(state, monkeypatch):
    root, _, _, _, _ = state
    path = reload.capture(root, 'o/r')
    receipt = json.loads(path.read_text())
    receipt['status'] = 'ready'
    path.write_text(json.dumps(receipt))
    launcher = Launcher(root / 'ledger.sqlite3', {'repo': 'o/r'}, github=Mock())
    monkeypatch.setattr(launcher, '_spawn', Mock())
    with pytest.raises(ValueError, match='alive'):
        launcher.resume_saved_runs()
    launcher._spawn.assert_not_called()


def test_consume_spawn_failure_keeps_watches_disabled_and_receipt_recoverable(state, monkeypatch):
    root, _, _, watch, _ = state
    path = reload.capture(root, 'o/r')
    receipt = json.loads(path.read_text())
    receipt['status'] = 'ready'
    path.write_text(json.dumps(receipt))
    auto = json.loads((root / 'auto-review.json').read_text())
    auto['settings'] = {**watch, 'new_prs': False, 'updated_prs': False}
    (root / 'auto-review.json').write_text(json.dumps(auto))
    monkeypatch.setattr(reload, 'owner_state', lambda identity: 'missing')
    monkeypatch.setattr(reload, 'discover', lambda root: [])
    launcher = Launcher(root / 'ledger.sqlite3', {'repo': 'o/r'}, github=Mock())
    monkeypatch.setattr(launcher, '_spawn', lambda result: {**result, 'error': 'spawn failed'})
    with pytest.raises(ValueError, match='could not be started'):
        launcher.resume_saved_runs()
    assert json.loads(path.read_text())['status'] == 'ready'
    assert json.loads((root / 'auto-review.json').read_text())['settings']['new_prs'] is False


def test_activate_terminates_exact_idle_set_without_touching_queued_jobs(state, monkeypatch):
    root, ledger, runs, watch, processes = state
    path = reload.capture(root, 'o/r')
    auto = json.loads((root / 'auto-review.json').read_text())
    auto['settings'] = {**watch, 'new_prs': False, 'updated_prs': False}
    (root / 'auto-review.json').write_text(json.dumps(auto))
    killed = []
    monkeypatch.setattr(reload, '_terminate', lambda process: killed.append(process['pid']))
    monkeypatch.setattr(reload, 'owner_state', lambda identity: 'missing' if json.loads(identity)['pid'] in killed else 'live')
    result = reload.activate(root)
    assert result['status'] == 'ready' and set(result['run_ids']) == set(runs)
    assert killed[-1] == 100 and set(killed) == {p['pid'] for p in processes}
    assert all(j['state'] == 'queued' for j in ledger.jobs())
    assert json.loads(path.read_text())['status'] == 'ready'


def test_activate_rejects_job_claim_after_capture_before_any_termination(state, monkeypatch):
    root, ledger, runs, _, _ = state
    reload.capture(root, 'o/r')
    ledger.claim('new-owner', 2, runs[0])
    terminate = Mock()
    monkeypatch.setattr(reload, '_terminate', terminate)
    with pytest.raises(ValueError, match='active'):
        reload.activate(root)
    terminate.assert_not_called()
