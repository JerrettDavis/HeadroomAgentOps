"""Replay saved feedback through the publisher without launching model workers."""


def replay_feedback(ledger, evidence, publisher, repo, run_id=None, numbers=None):
    report = {'eligible': [], 'published': [], 'held': [], 'errors': [],
              'additional_model_sessions': 0}
    seen = set()
    for job in reversed(ledger.jobs(run_id)):
        key = (job['repo'], job['number'])
        if key in seen:
            continue
        seen.add(key)
        if (job['repo'] != repo or job['stale'] or job['state'] not in
                ('ready', 'reviewed', 'held', 'awaiting_ci', 'needs_followup') or
                numbers is not None and job['number'] not in numbers):
            continue
        result = job.get('result') or {}
        actions = [a for a in result.get('actions', []) if a['kind'] in ('comment', 'review')]
        if not actions and not result.get('findings'):
            continue
        try:
            reference = result.get('reviewed_pr_evidence')
            if not reference:
                raise ValueError('Saved review has no immutable PR evidence')
            original = evidence.get(reference['sha256'])
            if original['number'] != job['number'] or original['head_sha'] != job['head_sha']:
                raise ValueError('Saved review identity mismatch')
            if not actions:
                retained = {f['description'] for f in result.get('findings', [])}
                for attempt in reversed(ledger.attempts(job['id'])):
                    prior = attempt.get('result') or {}
                    reference = prior.get('reviewed_pr_evidence')
                    if not reference or prior.get('main_sha', job['main_sha']) != job['main_sha']:
                        continue
                    source = evidence.get(reference['sha256'])
                    if any(source.get(k) != original.get(k) for k in
                           ('number', 'head_sha', 'base_sha', 'base_ref', 'head_ref', 'head_repo')):
                        continue
                    for action in prior.get('actions', []):
                        if (action['kind'] == 'comment' and action.get('body') in retained and
                                action not in actions):
                            actions.append(action)
            if not actions and not any(f.get('novel', True) is not False
                                       for f in result.get('findings', [])):
                continue
            report['eligible'].append(job['number'])
            if publisher is None:
                continue
            publication = publisher.publish(job, original, {**result, 'actions': actions})
            updated = {k: v for k, v in result.items() if k != 'evidence_record'}
            if result.get('evidence_record'):
                updated['previous_evidence_record'] = result['evidence_record']
            updated['feedback_publication'] = publication
            updated['own_discussion_entries'] = publication['own_discussion_entries']
            updated['evidence_record'] = evidence.put(updated)
            ledger.reclassify(job['id'], job['state'], updated)
            item = {'number': job['number'], 'publication': publication}
            if publication['sent']:
                report['published'].append(item)
            if publication['held']:
                report['held'].append(item)
        except Exception as exc:
            report['errors'].append({'number': job['number'], 'error': str(exc)})
    return report
