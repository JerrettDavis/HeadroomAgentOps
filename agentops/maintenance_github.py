"""Complete drift inventory and exact-head workflow observations."""
from urllib.parse import quote


def pr_binding(raw, row, repo, heads):
    return (raw.get('state') == 'open' and raw.get('number') == row['number']
            and raw.get('base', {}).get('ref') == 'main'
            and raw.get('base', {}).get('repo', {}).get('full_name', '').lower() == repo.lower()
            and raw.get('head', {}).get('sha') in heads
            and raw.get('head', {}).get('ref') == row['head_ref']
            and raw.get('head', {}).get('repo', {}).get('full_name', '').lower() == (row.get('head_repo') or '').lower())


def drift_inventory(github):
    main = github.main_sha()
    prs = []
    for raw in github.pages(f'repos/{github.repo}/pulls?state=open&base=main'):
        if raw.get('base', {}).get('ref') != 'main':
            continue
        head = raw['head']
        row = {'number': raw['number'], 'title': raw['title'], 'author': raw['user']['login'],
               'draft': raw.get('draft', False), 'url': raw.get('html_url'),
               'head_sha': head['sha'], 'head_ref': head['ref'],
               'head_repo': (head.get('repo') or {}).get('full_name'), 'main_sha': main,
               'repo': github.repo, 'behind': None, 'reason': ''}
        row['conflict'] = ('conflicts' if raw.get('mergeable') is False else
                           'clean' if raw.get('mergeable') is True else 'unknown')
        permissions = (head.get('repo') or {}).get('permissions', {})
        row['permission'] = ('allowed' if permissions.get('push') or raw.get('maintainer_can_modify') is True else
                             'denied' if permissions.get('push') is False and not raw.get('maintainer_can_modify') else 'unknown')
        try:
            data = github.request('GET', f'repos/{github.repo}/compare/{head["sha"]}...{main}')
            behind = data.get('ahead_by')
            if type(behind) is not int or behind < 0:
                raise ValueError('Comparison incomplete')
            row['behind'] = behind
        except (RuntimeError, ValueError, OSError):
            row['reason'] = 'Comparison unavailable; refresh before updating'
        if row['behind'] != 0:
            prs.append(row)
    return {'main_sha': main, 'prs': prs}


def workflow_runs(github, sha, branch=None):
    endpoint = f'repos/{github.repo}/actions/runs?head_sha={quote(sha, safe="")}'
    if branch:
        endpoint += '&branch=' + quote(branch, safe='')
    return github.pages(endpoint, 'workflow_runs')


def main_health(sha, runs, repo=None):
    latest = {}
    for run in runs:
        if run.get('head_sha') != sha or run.get('head_branch') != 'main':
            continue
        if run.get('event') not in ('push', 'schedule', 'workflow_dispatch'):
            continue
        if repo and run.get('repository', {}).get('full_name', '').lower() != repo.lower():
            continue
        identity = str(run.get('workflow_id', run.get('path', run['id']))) + ':' + run['event']
        order = (run.get('run_number', run['id']), run.get('run_attempt', 1), run['id'])
        if identity not in latest or order > latest[identity][0]:
            latest[identity] = (order, {**run, 'context_key': identity})
    current = [item[1] for item in latest.values()]
    failures = [r for r in current if r.get('conclusion') in ('failure', 'timed_out', 'startup_failure')]
    approval = [r for r in current if r.get('conclusion') == 'action_required' or r.get('status') == 'action_required']
    state = ('broken' if failures else 'approval_needed' if approval else
             'healthy' if current and all(r.get('status') == 'completed' and r.get('conclusion') == 'success'
                                         for r in current) else 'pending')
    return {'head_sha': sha, 'state': state, 'workflows': current, 'failures': failures}
