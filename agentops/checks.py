"""Select current exact-head workflow runs without conflating unrelated checks."""
import re


def effective_ci(current):
    runs=current.get('workflows',[])
    selected={}
    def key(run):
        path=run.get('path','').split('@',1)[0]
        return (run.get('source_repo',''),run.get('workflow_id',path or run.get('id')),
                path,run.get('event'),run.get('head_sha'))
    def order(run):
        return (run.get('created_at') or '',run.get('id',0),run.get('run_attempt',1))
    for run in runs:
        identity=key(run)
        if identity not in selected or order(run)>order(selected[identity]):
            selected[identity]=run
    retained=list(selected.values())
    superseded={(r.get('source_repo',''),str(r.get('id'))) for r in runs
                if r is not selected[key(r)] and r.get('id')!=selected[key(r)].get('id')}
    checks=[]
    for check in current.get('checks',[]):
        match=re.match(r'https://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)/',check.get('details_url',''))
        if match and (match[1],match[2]) in superseded:
            continue
        checks.append(check)
    return checks,retained
