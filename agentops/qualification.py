"""Avoid spending one model session per PR on an identical provenance blocker."""


def record_shared_audit(ledger,evidence,job,result,artifact=None):
    expected=(job.get('result') or {}).get('shared_provenance_hash')
    source=result.get('source_review',{})
    if not expected or source.get('manifest_hash')!=expected or source.get('verdict')!='held':
        return None
    proof={'repo':job['repo'],'source_job':job['id'],'head_sha':job['head_sha'],
           'main_sha':job['main_sha'],'source_review':source}
    if artifact is not None:
        proof['audit_artifact']=evidence.put(artifact)
    reference=evidence.put(proof)
    ledger.record_workflow_audit(job['repo'],expected,reference)
    return reference


def qualification_pilot(ledger, evidence, run_id):
    groups, selected, blocked = {}, [], []
    jobs = ledger.jobs(run_id)
    for job in jobs:
        if job['state'] != 'queued':
            raise ValueError('Pilot selection requires an entirely unclaimed plan')
        prior = ledger.context_review(job)
        proof = (prior or {}).get('result') or {}
        reference = proof.get('evidence_record')
        if reference:
            proof = evidence.get(reference['sha256'])
        source = proof.get('source_review', {})
        if ((job.get('result') or {}).get('review_task') != 'qualification' or
                source.get('verdict') != 'held' or not source.get('manifest_hash') or
                'Exact-source provenance review is incomplete' not in proof.get('qualification_limits', [])):
            selected.append(job['number'])
            continue
        score = (len(proof.get('findings', [])), len(proof.get('qualification_limits', [])), job['number'])
        key = (job['repo'], source['manifest_hash'])
        if key not in groups or score < groups[key][0]:
            groups[key] = (score,job['number'])
    for (repo,manifest),(_,number) in groups.items():
        reference=ledger.workflow_audit(repo,manifest)
        audit=evidence.get(reference['sha256']) if reference else None
        if (audit and audit.get('source_review',{}).get('manifest_hash')==manifest and
                audit['source_review'].get('verdict')=='held'):
            blocked.append(manifest)
        else:
            selected.append(number)
    selected = sorted(selected)
    for (_,manifest),(_,number) in groups.items():
        job = next(j for j in jobs if j['number'] == number)
        if number in selected:
            ledger.set_review_task(job['id'],'qualification',manifest)
    deferred = sorted(j['number'] for j in jobs if j['number'] not in selected)
    target = ledger.split_pending_run(run_id, selected) if deferred else run_id
    return {'run_id':target,'deferred_run_id':run_id,'selected':selected,'deferred':deferred,
            'shared_provenance_groups':len(groups),'blocked_manifest_hashes':blocked}
