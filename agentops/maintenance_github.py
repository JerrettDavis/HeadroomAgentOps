"""Complete drift inventory and exact-head workflow observations."""
from urllib.parse import quote


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


def main_health(sha, runs):
    latest = {}
    for run in runs:
        if run.get('head_sha') != sha or run.get('head_branch') != 'main':
            continue
        identity = run.get('workflow_id', run.get('path', run['id']))
        order = (run.get('run_number', run['id']), run.get('run_attempt', 1), run['id'])
        if identity not in latest or order > latest[identity][0]:
            latest[identity] = (order, run)
    current = [item[1] for item in latest.values()]
    failures = [r for r in current if r.get('conclusion') in ('failure', 'timed_out', 'startup_failure')]
    approval = [r for r in current if r.get('conclusion') == 'action_required' or r.get('status') == 'action_required']
    state = ('broken' if failures else 'approval_needed' if approval else
             'healthy' if current and all(r.get('status') == 'completed' and r.get('conclusion') == 'success'
                                         for r in current) else 'pending')
    return {'head_sha': sha, 'state': state, 'workflows': current, 'failures': failures}
