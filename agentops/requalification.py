"""Mechanically clear a sole inherited-workflow review hold using saved tested analysis."""
from .approval_baseline import attach_approval_baseline
from .discussion import fingerprint
from .github import read_pull
from .policy import gate, revision_gate, terminal_ci_refresh


def requalify_inherited_review(ledger,github,evidence,publisher,identity):
    job=ledger.job(identity)
    result=dict(job.get('result') or {})
    report={'number':job['number'],'additional_model_sessions':0,'approved':False}
    approval_intent=next((a for a in result.get('actions',[]) if a['kind']=='review' and a.get('event')=='APPROVE'),None)
    if (job['state'] not in ('held','needs_followup') or not result.get('require_decision') or
            (not approval_intent and set(result.get('qualification_limits',[]))!={'Exact-source provenance review is incomplete'}) or
            any(f.get('severity') in ('critical','important','question') for f in result.get('findings',[]))):
        return {**report,'reason':'Not a sole inherited provenance hold'}
    original=evidence.get(result['reviewed_pr_evidence']['sha256'])
    current,main=read_pull(github,job['number']),github.main_sha()
    entries=result.get('own_discussion_entries',[])
    checked_original,checked_current=original,current
    if original.get('discussion_hash_version')==current.get('discussion_hash_version')==2:
        checked_original={**original,'discussion_hash':fingerprint(original,entries)}
        checked_current={**current,'discussion_hash':fingerprint(current,entries)}
    reasons=revision_gate(checked_original,checked_current,job['main_sha'],main)
    if reasons:
        return {**report,'reasons':reasons}
    attach_approval_baseline(github,evidence,current,main,result)
    refresh=terminal_ci_refresh(original,current,main)
    if refresh:
        result['ci_refresh']=refresh
    reasons=gate(checked_original,checked_current,job['main_sha'],main,result,ledger.notices(job['repo'],job['number']))
    if reasons:
        return {**report,'reasons':reasons}
    result.update(outcome='ready',publication_qualification='ready',qualification_limits=[])
    action=approval_intent or {'kind':'review','event':'APPROVE','body':
        'The code review and recorded local validation are complete. The sole remaining approval hold '
        'was inherited workflow execution provenance; a coordinator-verified review-only baseline now '
        'resolves that review hold. Nonblocking notes in the prior review remain. This approval does '
        'not authorize CI execution or merge the PR.'}
    publication=publisher.publish(job,original,{**result,'actions':[action]})
    result['requalification_publication']=publication
    result['previous_review_actions']=result.get('actions',[])
    result['actions']=[action]
    result['publication']=publication
    result['own_discussion_entries']=publication['own_discussion_entries']
    result['previous_evidence_record']=result.pop('evidence_record',None)
    result['requalification_reason']='Verified review baseline resolves sole inherited provenance hold'
    result['evidence_record']=evidence.put(result)
    state='ready' if publication.get('decision')=='APPROVE' else 'held'
    if state=='held':
        result['publication_qualification']='held'
        result['qualification_limits']=[reason for item in publication['held'] for reason in item['reasons']]
    ledger.reclassify(identity,state,result)
    return {**report,'approved':state=='ready','publication':publication}
