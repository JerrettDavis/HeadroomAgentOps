"""Refresh CI mechanically; redispatch only when new evidence needs reasoning."""

import datetime

from .policy import gate, revision_gate, terminal_ci_refresh
from .discussion import fingerprint
from .model import canonical
from .checks import effective_ci


def failure_evidence(pr):
    """Ignore pending progress while retaining changed terminal failure evidence."""
    failures = []
    checks,workflows=effective_ci(pr)
    for field,items in (('checks',checks),('workflows',workflows)):
        for item in items:
            if item.get('status') == 'completed' and item.get('conclusion') not in ('success', 'neutral', 'skipped'):
                failures.append((field, canonical(item)))
    seen = set()
    for item in pr.get('statuses', []):
        if item['context'] in seen:
            continue
        seen.add(item['context'])
        if item.get('state') in ('error', 'failure'):
            failures.append(('statuses', canonical(item)))
    return set(failures)


def reconcile_jobs(ledger, github, evidence, run_id=None, retry_changed=False, publisher=None):
    report = {'ready': [], 'queued': [], 'held': [], 'stale': [], 'errors': []}
    current_by_number = {}
    for job in ledger.jobs(run_id):
        result = job.get('result')
        if (job['repo'] != github.repo or job['stale'] or job['state'] not in
                ('ready', 'reviewed', 'held', 'awaiting_ci', 'needs_followup') or not result or
                result.get('outcome') not in ('ready', 'awaiting_ci')):
            continue
        try:
            if job['number'] not in current_by_number:
                current_by_number[job['number']] = github.pull(job['number'])
            current = current_by_number[job['number']]
            main_sha = github.main_sha()
            if current['head_sha'] != job['head_sha'] or main_sha != job['main_sha']:
                ledger.reclassify(job['id'], 'stale', {**result, 'qualification_limits': ['Revision changed']})
                report['stale'].append(job['number'])
                continue
            required = ('reviewed_discussion_hash', 'reviewed_checks_hash', 'reviewed_base_ref',
                        'reviewed_head_ref', 'reviewed_head_repo')
            if any(key not in result for key in required):
                report['held'].append(job['number'])
                continue
            original = {**current, 'discussion_hash': result['reviewed_discussion_hash'],
                        'checks_hash': result['reviewed_checks_hash'], 'base_ref': result['reviewed_base_ref'],
                        'head_ref': result['reviewed_head_ref'], 'head_repo': result['reviewed_head_repo']}
            if result.get('reviewed_pr_evidence'):
                original = evidence.get(result['reviewed_pr_evidence']['sha256'])
            if original.get('discussion_hash_version') == current.get('discussion_hash_version') == 2:
                entries = result.get('own_discussion_entries', [])
                original = {**original, 'discussion_hash': fingerprint(original, entries)}
                current = {**current, 'discussion_hash': fingerprint(current, entries)}
            notices = ledger.notices(job['repo'], job['number'])
            new_notices = {n['id'] for n in notices} - set(result.get('reviewed_notice_ids', []))
            refresh = terminal_ci_refresh(original, current, main_sha)
            updated = {key: value for key, value in result.items() if key != 'evidence_record'}
            if result.get('evidence_record'):
                updated['previous_evidence_record'] = result['evidence_record']
            updated.pop('ci_refresh', None)
            if refresh:
                updated['ci_refresh'] = refresh
            limits = gate(original, current, job['main_sha'], main_sha, updated, notices)
            reasoning_changed = (bool(revision_gate(original, current, job['main_sha'], main_sha)) or
                                 bool(new_notices) or
                                 bool(failure_evidence(current) - failure_evidence(original)))
            if not limits and not new_notices:
                state = 'ready'
                updated['worker_outcome'] = result.get('worker_outcome', result['outcome'])
                updated['outcome'] = 'ready'
                report['ready'].append(job['number'])
            elif reasoning_changed:
                state = 'needs_followup'
                if new_notices:
                    limits.append('New shared evidence requires review')
            else:
                state = 'awaiting_ci' if job['state'] == 'awaiting_ci' else 'held'
                report['held'].append(job['number'])
            updated.update(qualification_limits=limits, publication_qualification=state,
                           reconciled_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
            if publisher and state == 'ready':
                updated['publication'] = publisher.publish(job, original, updated)
                updated['own_discussion_entries'] = updated['publication']['own_discussion_entries']
                if updated['publication']['held']:
                    state = 'held'
            updated['evidence_record'] = evidence.put(updated)
            ledger.reclassify(job['id'], state, updated)
            if state == 'needs_followup' and retry_changed:
                try:
                    ledger.retry(job['id'])
                    report['queued'].append(job['number'])
                except ValueError:
                    report['held'].append(job['number'])
        except Exception as exc:
            report['errors'].append({'number': job['number'], 'error': str(exc)})
    return report
