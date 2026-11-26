"""Approval continuity requires a recorded successful approval and unchanged source."""
import json
import sqlite3
import time
from contextlib import closing

from .maintenance_policy import mutation_gate
from .model import digest


def execution_manifest(github, sha):
    commit = github.request('GET', f'repos/{github.repo}/git/commits/{sha}')
    tree = github.request('GET', f'repos/{github.repo}/git/trees/{commit["tree"]["sha"]}?recursive=1')
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'), list):
        raise ValueError('Execution source tree is incomplete; manual approval required')
    return digest(sorted(({k: item.get(k) for k in ('path', 'sha', 'type', 'mode')}
                          for item in tree['tree'] if item.get('path', '').startswith('.github/')
                          or item.get('path') == '.gitmodules'), key=lambda item: item['path']))


def awaiting_approval(run):
    return (run.get('status') in ('action_required', 'requested', 'waiting') or
            run.get('conclusion') == 'action_required')


def run_context(run, row, sha, repo):
    if (run.get('head_sha') != sha or run.get('source_repo') != repo or
            run.get('event') != 'pull_request' or run.get('head_branch') != row.get('head_ref')):
        return None
    prs = run.get('pull_requests', [])
    if len(prs) != 1:
        return None
    pr = prs[0]
    if (pr.get('number') != row['number'] or pr.get('head', {}).get('sha') != sha or
            pr.get('head', {}).get('ref') != row.get('head_ref') or pr.get('base', {}).get('ref') != 'main'):
        return None
    path = run.get('path', '')
    if not path.startswith('.github/workflows/') or '@' not in path:
        return None
    return {'source_repo': repo, 'event': 'pull_request', 'number': row['number'],
            'path': path, 'head_ref': row['head_ref'], 'base_ref': 'main'}


def clear_source(source, manifest):
    return (source.get('manifest_hash') == manifest and source.get('verdict') == 'clear'
            and bool(source.get('rationale', '').strip()) and bool(source.get('evidence')))


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
                       and r.get('new_head') == head and r.get('state') in ('updated', 'publishing')
                       and r.get('equivalent') is True and (len(visited) > 1 or r.get('main_sha') == main_sha)
                       and len(r.get('contribution', '')) == 64
                       and ((r.get('receipt') or {}).get('new_head') == head or
                            (r.get('state') == 'publishing' and
                             (r.get('prepared') or {}).get('new_head') == head and
                             (r.get('prepared') or {}).get('contribution') == r.get('contribution')))), None)
        if not receipt:
            return False
        head = receipt['head_sha']
    return head == previous_head and bool(visited)


def approval_baseline(path, github, row):
    from .maintenance_store import MaintenanceStore
    records = []
    try:
        if path.exists():
            with closing(sqlite3.connect(path)) as db:
                records = db.execute("SELECT a.data,j.result FROM actions a JOIN jobs j ON j.id=a.job_id "
                                 "WHERE a.state='sent' AND j.repo=? AND j.number=? AND j.head_sha=?",
                                 (github.repo, row['number'], row['head_sha'])).fetchall()
    except sqlite3.OperationalError:
        pass
    approved = {json.loads(r[0]).get('workflow_id'): json.loads(r[1] or '{}').get('source_review', {})
                for r in records if json.loads(r[0]).get('event') == 'APPROVE_WORKFLOW'}
    store = MaintenanceStore(path, github.repo)
    for prior in store.list('approval'):
        if (prior.get('state') == 'sent' and prior.get('number') == row['number']
                and prior.get('head_sha') == row['head_sha']):
            approved[prior['run_id']] = prior.get('source_review', {})
    if not approved:
        return []
    current = github.pull(row['number'])
    if current['head_sha'] != row['head_sha']:
        raise ValueError('PR changed while recording workflow approvals')
    source_tree = execution_manifest(github, row['head_sha'])
    return [{'workflow_id': run['workflow_id'], 'path': run.get('path', '').split('@')[0],
             'manifest_hash': current['workflow_manifest_hash'], 'approved_run_id': run['id'],
             'context': context, 'source_review': approved[run['id']], 'execution_manifest': source_tree}
            for run in current.get('workflows', []) if run['id'] in approved
            and (context := run_context(run, row, row['head_sha'], github.repo))
            and clear_source(approved[run['id']], current['workflow_manifest_hash'])]


def continue_approvals(store, github, row, config=None, path=None):
    config = config or {'repo': github.repo, 'publish': True}
    path = path or store.path.parent / 'ledger.sqlite3'
    mutation_gate(path, config, row, ci=True)
    baseline = row.get('approval_baseline', [])
    if not baseline:
        store.update('update', row['key'], {'approval_terminal': True,
            'approval_status': 'No recorded approval provenance; manual approval required'})
        return
    if time.time() > row.get('approval_deadline', float('inf')):
        store.update('update', row['key'], {'approval_terminal': True, 'approval_status': 'Approval wait expired; check workflows manually'})
        return
    current = github.pull(row['number'])
    if (current['head_sha'] != row['new_head'] or current.get('state') != 'open' or
            current.get('base_ref') != 'main' or current.get('head_ref') != row.get('head_ref') or
            current.get('head_repo') != row.get('head_repo')):
        store.update('update', row['key'], {'approval_terminal': True,
                                          'approval_status': 'PR head changed; approval continuation stopped'})
        return
    source_tree = execution_manifest(github, row['new_head'])
    matched = set()
    for run in current.get('workflows', []):
        prior = next((item for item in baseline if item['workflow_id'] == run.get('workflow_id')
                      and item['path'] == run.get('path', '').split('@')[0]
                      and item['manifest_hash'] == current.get('workflow_manifest_hash')
                      and item.get('context') == run_context(run, row, row['new_head'], github.repo)
                      and item.get('execution_manifest') == source_tree
                      and clear_source(item.get('source_review', {}), current.get('workflow_manifest_hash'))), None)
        if not prior or run.get('head_sha') != row['new_head']:
            continue
        matched.add(run['workflow_id'])
        identity = f'{row["key"]}:{run["id"]}'
        operation = store.get('approval', identity)
        if not awaiting_approval(run):
            if operation and operation.get('state') == 'sending' and run.get('status') in ('queued', 'in_progress', 'completed'):
                store.update('approval', identity, {'state': 'sent', 'receipt': {'reconciled': True, 'run_id': run['id']}})
            continue
        if operation and operation.get('state') == 'sent':
            continue
        # Repeating the approval endpoint is safe only for an observed run still
        # awaiting approval; queued/running/completed observations reconcile it.
        mutation_gate(path, config, row, ci=True)
        store.put('approval', identity, {'state': 'sending', 'run_id': run['id'], 'number': row['number'],
                  'head_sha': row['new_head'], 'source_review': prior['source_review'], 'context': prior['context'],
                  'execution_manifest': source_tree})
        from .ci import CIHandler
        screened = {**current, 'workflows': [{**r, 'status': 'action_required'} if r['id'] == run['id']
                                           and awaiting_approval(r) else r for r in current.get('workflows', [])]}
        receipt = CIHandler(github)({'repo': github.repo, 'head_sha': row['new_head']}, screened,
                                   {'event': 'APPROVE_WORKFLOW', 'workflow_id': run['id']})
        store.update('approval', identity, {'state': 'sent', 'receipt': receipt})
    terminal = {item['workflow_id'] for item in baseline} <= matched
    store.update('update', row['key'], {'approval_terminal': terminal, 'approval_status': 'Approval continuity checked' if matched
                                     else 'Waiting for matching workflows; changed sources require manual approval'})
