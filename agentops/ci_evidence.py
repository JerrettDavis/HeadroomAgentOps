"""Fetch immutable failed-job evidence for reviewers without giving workers GitHub credentials."""
import json
import re
from .checks import effective_ci
from .model import digest,canonical
from .process import command
from .redaction import redact


def collect_failed_ci(github,evidence,current):
    checks,_=effective_ci(current)
    failures=[c for c in checks if c.get('status')=='completed' and c.get('conclusion')=='failure']
    if not failures:
        return None
    key=digest([github.repo,current['head_sha'],sorted(c['id'] for c in failures)])
    directory=evidence.root.parent/'ci-evidence'
    directory.mkdir(exist_ok=True)
    index=directory/(key+'.json')
    if index.exists():
        reference=json.loads(index.read_text(encoding='utf8'))
        evidence.get(reference['sha256'])
        return reference
    records=[]
    for check in failures:
        match=re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/actions/runs/(\d+)/job/(\d+)',
                           check.get('details_url',''))
        if not match or match[1]!=github.repo:
            continue
        metadata=github.request('GET',f'repos/{github.repo}/actions/jobs/{match[3]}')
        if metadata.get('head_sha')!=current['head_sha'] or str(metadata.get('run_id'))!=match[2]:
            raise ValueError('Failed CI job does not match the reviewed head/run')
        text=redact(command(['gh','api',f'repos/{github.repo}/actions/jobs/{match[3]}/logs'],timeout=120))
        lines=text.splitlines()
        chosen=set()
        for number,line in enumerate(lines):
            if re.search(r'##\[error\]|FAILED |FAIL:|AssertionError|short test summary',line):
                chosen.update(range(max(0,number-6),min(len(lines),number+15)))
        selected=sorted(chosen)[:220] if chosen else list(range(max(0,len(lines)-100),len(lines)))
        full=evidence.put({'job_id':metadata['id'],'head_sha':current['head_sha'],'log':text})
        records.append({'check_id':check['id'],'job_id':metadata['id'],'run_id':metadata['run_id'],
                        'name':check['name'],'metadata':metadata,'full_log':full,
                        'excerpt':'\n'.join(f'{n+1}: {lines[n]}' for n in selected)})
    reference=evidence.put({'repo':github.repo,'head_sha':current['head_sha'],'failed_jobs':records})
    index.write_text(canonical(reference),encoding='utf8')
    return reference
