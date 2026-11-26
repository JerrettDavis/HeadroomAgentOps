"""Approval continuity requires a recorded successful approval and unchanged source."""
import json
import sqlite3
from contextlib import closing

from .maintenance_github import workflow_runs


def mechanical_update(path, repo, pr, previous_head, main_sha):
    from .maintenance_store import MaintenanceStore
    if not (path.parent / 'maintenance.sqlite3').is_file():
        return False
    records = MaintenanceStore(path, repo).list('update')
    head = pr['head_sha']
    visited = set()
    while head != previous_head and head not in visited:
        visited.add(head)
        receipt = next((r for r in records if r.get('number') == pr['number']
                       and r.get('new_head') == head and r.get('state') == 'updated'
                       and r.get('equivalent') is True and r.get('main_sha') == main_sha
                       and len(r.get('contribution', '')) == 64
                       and (r.get('receipt') or {}).get('new_head') == head), None)
        if not receipt:
            return False
        head = receipt['head_sha']
    return head == previous_head and bool(visited)


def approval_baseline(path, github, row):
    if not path.exists():
        return []
    try:
        with closing(sqlite3.connect(path)) as db:
            records = db.execute("SELECT a.data FROM actions a JOIN jobs j ON j.id=a.job_id "
                                 "WHERE a.state='sent' AND j.repo=? AND j.number=? AND j.head_sha=?",
                                 (github.repo, row['number'], row['head_sha'])).fetchall()
    except sqlite3.OperationalError:
        return []
    approved = {json.loads(r[0]).get('workflow_id') for r in records
                if json.loads(r[0]).get('event') == 'APPROVE_WORKFLOW'}
    if not approved:
        return []
    current = github.pull(row['number'])
    if current['head_sha'] != row['head_sha']:
        raise ValueError('PR changed while recording workflow approvals')
    return [{'workflow_id': run['workflow_id'], 'path': run.get('path', '').split('@')[0],
             'manifest_hash': current['workflow_manifest_hash'], 'approved_run_id': run['id']}
            for run in current.get('workflows', []) if run['id'] in approved]


def continue_approvals(store, github, row):
    baseline = row.get('approval_baseline', [])
    if not baseline:
        store.update('update', row['key'], {'approval_status': 'No recorded approval provenance; manual approval required'})
        return
    current = github.pull(row['number'])
    if current['head_sha'] != row['new_head']:
        store.update('update', row['key'], {'approval_status': 'PR head changed; approval continuation stopped'})
        return
    matched = 0
    for run in current.get('workflows', []):
        prior = next((item for item in baseline if item['workflow_id'] == run.get('workflow_id')
                      and item['path'] == run.get('path', '').split('@')[0]
                      and item['manifest_hash'] == current.get('workflow_manifest_hash')), None)
        if not prior or run.get('head_sha') != row['new_head']:
            continue
        matched += 1
        if run.get('status') not in ('action_required', 'requested', 'waiting'):
            continue
        identity = f'{row["key"]}:{run["id"]}'
        operation = store.get('approval', identity)
        if operation and operation.get('state') == 'sent':
            continue
        # Repeating the approval endpoint is safe only for an observed run still
        # awaiting approval; queued/running/completed observations reconcile it.
        store.put('approval', identity, {'state': 'sending', 'run_id': run['id']})
        from .ci import CIHandler
        receipt = CIHandler(github)({'repo': github.repo, 'head_sha': row['new_head']}, current,
                                   {'event': 'APPROVE_WORKFLOW', 'workflow_id': run['id']})
        store.update('approval', identity, {'state': 'sent', 'receipt': receipt})
    store.update('update', row['key'], {'approval_status': 'Approval continuity checked' if matched
                                     else 'Waiting for matching workflows; changed sources require manual approval'})
