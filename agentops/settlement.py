"""Reconcile saved review decisions and plan only work requiring new reasoning."""
from concurrent.futures import ThreadPoolExecutor, as_completed

from .discussion import fingerprint
from .disposition import determination, has_determination
from .policy import revision_gate, gate, terminal_ci_refresh
from .reconcile import failure_evidence


def settle_reviews(ledger, github, evidence, snapshot, config, publisher=None,
                   emit=None, acknowledged_notices=(), read_workers=2, numbers=None):
    emit = emit or (lambda event: None)
    if snapshot.get('complete') is not True or snapshot['repo'] != github.repo:
        raise ValueError('Settlement requires a complete matching repository inventory')
    report = {'open_prs': len(snapshot['prs']), 'determined': [], 'existing': [],
              'pending': [], 'held': [], 'errors': [], 'commented': [],
              'independent_review_required': [], 'additional_model_sessions': 0}
    latest = {j['number']: j for j in ledger.jobs() if j['repo'] == github.repo}
    pending = {}
    actor = github.authenticated_user()['login']
    excluded_authors = {a.lower() for a in config.get('exclude_authors', [])} | {actor.lower()}

    def queue(pr, reasons):
        pending[pr['number']] = pr
        report['pending'].append({'number': pr['number'], 'reasons': reasons})

    def process(pr, job, current):
        result = dict(job['result'])
        original = evidence.get(result['reviewed_pr_evidence']['sha256'])
        if original['number'] != job['number'] or original['head_sha'] != job['head_sha']:
            raise ValueError('Saved review identity mismatch')
        checked_original, checked_current = original, current
        if original.get('discussion_hash_version') == current.get('discussion_hash_version') == 2:
            entries = result.get('own_discussion_entries', [])
            checked_original = {**original, 'discussion_hash': fingerprint(original, entries)}
            checked_current = {**current, 'discussion_hash': fingerprint(current, entries)}
        reasons = revision_gate(checked_original, checked_current, job['main_sha'], snapshot['main_sha'])
        if reasons:
            queue(pr, reasons)
            return
        notices = ledger.notices(github.repo, job['number'])
        result['reviewed_notice_ids'] = sorted(set(result.get('reviewed_notice_ids', [])) |
                                               (set(acknowledged_notices) & {n['id'] for n in notices}))
        result['settlement_notice_acknowledgments'] = sorted(set(acknowledged_notices) & {n['id'] for n in notices})
        new_notices = {n['id'] for n in notices} - set(result['reviewed_notice_ids'])
        if new_notices:
            queue(pr, ['New shared evidence requires review'])
            return
        if failure_evidence(current) - failure_evidence(original):
            queue(pr, ['New terminal CI failure requires review'])
            return
        refresh = terminal_ci_refresh(original, current, snapshot['main_sha'])
        if refresh:
            result['ci_refresh'] = refresh
        limits = gate(checked_original, checked_current, job['main_sha'], snapshot['main_sha'], result, notices)
        result['qualification_limits'] = list(dict.fromkeys(result.get('qualification_limits', []) + limits))
        result['determination_required'] = True
        actor = github.authenticated_user()['login']
        action = determination(result, current['author'], actor)
        def record_incomplete_review():
            if pr.get('review_decision') != 'REVIEW_REQUIRED':
                return
            if current['author'].lower() == actor.lower():
                report['independent_review_required'].append({'number':pr['number'],
                    'reason':'Authenticated reviewer is the PR author; GitHub requires another identity'})
            else:
                queue(pr, ['Approval qualification incomplete'])
        if has_determination(current, result, actor):
            if action['event'] == 'COMMENT':
                report['commented'].append({'number':pr['number'],'existing':True})
                record_incomplete_review()
            else:
                report['existing'].append(pr['number'])
            return
        if publisher is None:
            report['held'].append({'number': pr['number'], 'preview_event': action['event']})
            return
        publication = publisher.publish(job, original, {**result, 'actions': [action]}, current=current)
        result['determination_publication'] = publication
        result['own_discussion_entries'] = publication['own_discussion_entries']
        result['previous_evidence_record'] = result.pop('evidence_record', None)
        result['evidence_record'] = evidence.put(result)
        ledger.reclassify(job['id'], job['state'], result)
        if publication['sent']:
            item = {'number': pr['number'], 'event': action['event'], 'receipts':publication['sent']}
            if action['event'] == 'COMMENT':
                report['commented'].append(item)
                record_incomplete_review()
            else:
                report['determined'].append(item)
        if publication['held']:
            report['held'].append({'number': pr['number'], 'reasons': publication['held']})

    with ThreadPoolExecutor(max_workers=read_workers) as pool:
        futures = {}
        for pr in snapshot['prs']:
            if numbers is not None and pr['number'] not in numbers:
                continue
            if pr.get('author','').lower() in excluded_authors:
                report['independent_review_required'].append({'number':pr['number'],
                    'reason':'Author PR assigned to other maintainers; excluded from agent work'})
                continue
            job = latest.get(pr['number'])
            result = (job or {}).get('result') or {}
            if (not job or job['stale'] or job['state'] not in
                    ('ready', 'held', 'reviewed', 'awaiting_ci', 'needs_followup') or
                    not result.get('reviewed_pr_evidence')):
                queue(pr, ['No completed usable review'])
            elif job['head_sha'] != pr['head_sha'] or job['main_sha'] != snapshot['main_sha']:
                queue(pr, ['Head or main changed since review'])
            else:
                futures[pool.submit(github.pull, pr['number'])] = (pr, job)
        emit({'event': 'settlement_inventory', 'open': report['open_prs'], 'pending': len(pending)})
        for future in as_completed(futures):
            pr, job = futures[future]
            try:
                process(pr, job, future.result())
            except Exception as exc:
                report['errors'].append({'number': pr['number'], 'error': str(exc)})
            emit({'event': 'settlement_progress', 'number': pr['number'],
                  'determined': len(report['determined']), 'existing': len(report['existing']),
                  'pending': len(pending), 'held': len(report['held']), 'errors': len(report['errors'])})
    if github.main_sha() != snapshot['main_sha']:
        raise ValueError('Main changed during settlement; refresh inventory before launching')
    identity = ledger.save_snapshot(snapshot)
    run_id = ledger.create_run(identity, config)
    for pr in pending.values():
        identity = ledger.enqueue(run_id, pr, snapshot['main_sha'])
        item = next(p for p in report['pending'] if p['number'] == pr['number'])
        if item['reasons'] == ['Approval qualification incomplete']:
            ledger.set_review_task(identity,'qualification')
    report['run_id'] = run_id
    return report
