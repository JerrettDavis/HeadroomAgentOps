"""Requalify completed worker output after a coordinator read-transport failure."""

import json
from pathlib import Path

from .discussion import fingerprint
from .harness import validate_result
from .model import REVIEW_POLICY_VERSION
from .model import digest
from .policy import gate, revision_gate, terminal_ci_refresh
from .publisher import Publisher
from .redaction import redact
from .worker import safe_job_path
from .github import read_pull
from .approval_baseline import attach_approval_baseline


def recover_job(ledger, github, evidence, worker, identity, publisher=None):
    job = ledger.job(identity)
    previous = job.get('result') or {}
    error = previous.get('error') or previous.get('recovered_from_transport_error', '')
    recovered_stale = job['state'] == 'stale' and bool(previous.get('recovered_from_transport_error'))
    eligible = (job['state'] == 'failed' and not job['stale']) or recovered_stale
    if (not eligible or job['repo'] != github.repo or
            not (error.startswith('GitHub GET ') or
                 error=='PR changed during detail collection; retry with fresh evidence') or
            previous.get('execution', {}).get('exit_code') != 0):
        raise ValueError('Recovery requires completed output interrupted by a GitHub read failure')
    handle = job['handle']
    directory = safe_job_path(worker.root, handle['session_id'])
    if Path(handle['directory']).resolve() != directory:
        raise ValueError('Recovery path escaped the owned job directory')
    observation = worker.observe(handle)
    if observation['state'] not in ('exited', 'missing') or observation.get('exit_code', 0) != 0:
        raise ValueError('Recovery requires a confirmed terminal successful worker')
    input_dir = directory / 'input'
    packet_path = input_dir / 'packet.json'
    if packet_path.is_symlink() or packet_path.resolve().parent != input_dir or input_dir.resolve().parent != directory:
        raise ValueError('Recovery packet escaped the owned input directory')
    packet = json.loads(packet_path.read_text(encoding='utf8'))
    if packet['repo'] != job['repo'] or packet['main_sha'] != job['main_sha']:
        raise ValueError('Recovery packet identity mismatch')
    original = evidence.get(packet['raw_api_evidence']['sha256'])
    if original['number'] != job['number'] or original['head_sha'] != job['head_sha']:
        raise ValueError('Recovery evidence identity mismatch')
    result = validate_result(worker.collect(handle, job), {**job, 'session_id': handle['session_id']})
    result = {**result, 'execution': previous['execution'],
              'candidate_archive': previous.get('candidate_archive'),
              'own_discussion_entries': previous.get('own_discussion_entries', [])}
    authored_notices = set()
    for notice in result.get('notices', []):
        authored_notices.add(ledger.notice(job['repo'], 'suggestion', notice['body'],
            {'job': identity, 'session': handle['session_id'], 'evidence': notice['evidence'],
             'proposed_kind': notice['kind']}, None if notice.get('scope') == 'repository' and notice.get('kind')!='baseline' else [job['number']]))
    current, main_sha = read_pull(github,job['number']), github.main_sha()
    result['require_decision']=bool(ledger.get_run(job['run_id'])['config'].get('require_decision'))
    result['determination_required']=True
    if result['require_decision']:
        attach_approval_baseline(github,evidence,current,main_sha,result)
    # A write may have succeeded before a later GET failed. Verify journal receipts
    # against authenticated ownership and exact body/type before excluding them.
    actor = None
    for action in result.get('actions', []):
        if action['kind'] not in ('comment', 'review'):
            continue
        action_id = ledger.intent(identity, action)
        recorded = ledger.action(action_id)
        if recorded['state'] not in ('sent', 'sending'):
            continue
        if actor is None:
            actor = github.authenticated_user()['login']
        marker = '<!-- headroom-agentops:' + action_id + ' -->'
        receipt = Publisher._find_receipt(current, marker, action, actor)
        if not receipt or (recorded['state'] == 'sent' and
                (recorded.get('receipt') or {}).get('id') != receipt['id']):
            continue
        entry = {'kind': action['kind'], 'id': receipt['id'],
                 'body_hash': digest(redact(action['body']) + '\n\n' + marker)}
        if action['kind'] == 'review':
            entry['state'] = {'APPROVE': 'APPROVED', 'COMMENT': 'COMMENTED',
                              'REQUEST_CHANGES': 'CHANGES_REQUESTED'}[action['event']]
        if entry not in result['own_discussion_entries']:
            result['own_discussion_entries'].append(entry)
    checked_original, checked_current = original, current
    if original.get('discussion_hash_version') == current.get('discussion_hash_version') == 2:
        entries = result['own_discussion_entries']
        checked_original = {**original, 'discussion_hash': fingerprint(original, entries)}
        checked_current = {**current, 'discussion_hash': fingerprint(current, entries)}
    notices = ledger.notices(job['repo'], job['number'])
    refresh = terminal_ci_refresh(checked_original, checked_current, main_sha)
    if refresh:
        result['ci_refresh'] = refresh
    limits = gate(checked_original, checked_current, job['main_sha'], main_sha, result, notices)
    state = result['outcome']
    if revision_gate(checked_original, checked_current, job['main_sha'], main_sha):
        state = 'stale'
    elif state == 'ready' and limits:
        state = 'held'
    if job['stale'] and state in ('ready', 'reviewed'):
        state = 'held'
        limits.append('Previously stale evidence requires fresh independent qualification')
    context = evidence.get(packet['context_evidence']['sha256']) if packet.get('context_evidence') else packet
    initial_ids = {n['id'] for n in context.get('notices', [])}
    if state != 'stale' and ({n['id'] for n in notices} - initial_ids -
                            set(result['coverage'].get('notices', [])) - authored_notices):
        state = 'needs_followup'
        limits.append('New shared evidence requires review')
    result.update(review_policy_version=REVIEW_POLICY_VERSION, qualification_limits=limits,
        publication_qualification='needs_followup' if job['stale'] else state, recovered_from_transport_error=error,
        reviewed_pr_evidence=evidence.put(original), reviewed_discussion_hash=original['discussion_hash'],
        reviewed_checks_hash=original['checks_hash'], reviewed_base_ref=original.get('base_ref'),
        reviewed_head_ref=original.get('head_ref'), reviewed_head_repo=original.get('head_repo'),
        reviewed_notice_ids=sorted(initial_ids | authored_notices |
            (set(result['coverage'].get('notices', [])) & {n['id'] for n in notices})))
    if publisher and state != 'stale':
        result['publication'] = publisher.publish(job, original, result)
        result['own_discussion_entries'] = result['publication']['own_discussion_entries']
        if state == 'ready' and result['publication']['held']:
            state = 'held'
    preserve = not bool(result.get('candidate_archive'))
    if result.get('candidate_archive'):
        evidence.get(result['candidate_archive']['sha256'])
    if preserve:
        result['recovery_checkout_retained'] = str(directory)
    try:
        worker.cleanup(handle, preserve_checkout=preserve)
    except Exception as exc:
        result['cleanup_error'] = str(exc)
        result['recovery_checkout_retained'] = str(directory)
    result['evidence_record'] = evidence.put(result)
    ledger.reclassify(identity, state, result)
    return {'number': job['number'], 'state': state, 'additional_model_sessions': 0}
