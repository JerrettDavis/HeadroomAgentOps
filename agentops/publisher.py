"""Coordinator-only GitHub writes with durable intents and reconciliation."""

import contextlib
import os
import re
from pathlib import Path

from .policy import gate, revision_gate
from .redaction import redact
from .discussion import fingerprint
from .model import digest
from .github import RejectedWrite


@contextlib.contextmanager
def publication_lock(path):
    """An OS lock is released on crash; no stale timestamp grants write ownership."""
    lock_path = Path(str(path) + '.publisher.lock')
    with lock_path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Publisher:
    def __init__(self, github, ledger, candidate_handler=None, ci_handler=None):
        self.github, self.ledger = github, ledger
        self.candidate_handler, self.ci_handler = candidate_handler, ci_handler

    @staticmethod
    def _find_receipt(current, marker, action, actor):
        field = 'comments' if action['kind'] == 'comment' else 'reviews'
        expected = redact(action['body']) + '\n\n' + marker
        expected_state = {'APPROVE': 'APPROVED', 'COMMENT': 'COMMENTED',
                          'REQUEST_CHANGES': 'CHANGES_REQUESTED'}.get(action.get('event'))
        for item in current.get(field, []):
            owner = (item.get('user') or {}).get('login')
            if (item.get('body') == expected and owner == actor and
                    (field != 'reviews' or expected_state and item.get('state') == expected_state)):
                return {'id': item.get('id'), 'url': item.get('html_url'), 'reconciled': True}
        return None

    def publish(self, job, original, result):
        with publication_lock(self.ledger.path):
            return self._publish(job, original, result)

    def _publish(self, job, original, result):
        report = {'sent': [], 'held': [], 'own_discussion_entries': list(result.get('own_discussion_entries', []))}
        def remember(action, identity, receipt):
            if action['kind'] not in ('comment', 'review') or not receipt.get('id'):
                return
            entry = {'kind': action['kind'], 'id': receipt['id'], 'body_hash':
                     digest(redact(action['body']) + '\n\n<!-- headroom-agentops:' + identity + ' -->')}
            if action['kind'] == 'review':
                entry['state'] = {'APPROVE': 'APPROVED', 'COMMENT': 'COMMENTED',
                                  'REQUEST_CHANGES': 'CHANGES_REQUESTED'}[action['event']]
            if entry not in report['own_discussion_entries']:
                report['own_discussion_entries'].append(entry)
        actions = list(result.get('actions', []))
        if not actions and result.get('findings'):
            current = self.github.pull(job['number'])
            existing = '\n'.join(item.get('body') or '' for field in ('comments', 'reviews', 'review_comments')
                                  for item in current.get(field, []))
            normalize = lambda value: re.sub(r'\s+', ' ', redact(value)).strip().lower()
            known = normalize(existing)
            findings = [f for f in result['findings'] if f.get('novel', True) is not False
                        and normalize(f['description']) not in known]
            if findings:
                lines = [f'Review findings for `{job["head_sha"]}` against upstream main `{job["main_sha"]}`.']
                for finding in findings:
                    location = finding['path'] + (':' + str(finding['line']) if finding['line'] > 0 else '')
                    lines.append(f'**{finding["severity"].title()} — `{location}`**\n\n'
                                 f'{finding["description"]}\n\nEvidence: {finding["evidence"]}')
                actions = [{'kind': 'comment', 'body': '\n\n'.join(lines)}]
        for action in actions:
            identity = self.ledger.intent(job['id'], action)
            recorded = self.ledger.action(identity)
            if recorded['state'] == 'sent':
                report['sent'].append(recorded['receipt'])
                remember(action, identity, recorded['receipt'])
                continue
            current = self.github.pull(job['number'])
            raw_current = current
            checked_original = original
            if original.get('discussion_hash_version') == current.get('discussion_hash_version') == 2:
                current = {**current, 'discussion_hash': fingerprint(current, report['own_discussion_entries'])}
                checked_original = {**original, 'discussion_hash': fingerprint(original, report['own_discussion_entries'])}
            live_main = self.github.main_sha()
            marker = f'<!-- headroom-agentops:{identity} -->'
            if action['kind'] in ('comment', 'review'):
                actor = self.github.authenticated_user()['login']
                receipt = self._find_receipt(current, marker, action, actor)
                if receipt:
                    self.ledger.action_state(identity, 'sent', receipt)
                    report['sent'].append(receipt)
                    remember(action, identity, receipt)
                    continue
            if recorded['state'] == 'sending':
                if action['kind'] == 'push' and hasattr(self.candidate_handler, 'reconcile'):
                    receipt = self.candidate_handler.reconcile(job, current, action)
                    if receipt:
                        self.ledger.action_state(identity, 'sent', receipt)
                        report['sent'].append(receipt)
                        continue
                if action['kind'] == 'ci' and hasattr(self.ci_handler, 'reconcile'):
                    receipt = self.ci_handler.reconcile(job, current, action, recorded['receipt'])
                    if receipt:
                        self.ledger.action_state(identity, 'sent', receipt)
                        report['sent'].append(receipt)
                        continue
                # An absent marker immediately after a lost response is not proof of failure.
                report['held'].append({'action': identity, 'reasons': ['Ambiguous prior write; reconciliation required']})
                continue
            reasons = revision_gate(checked_original, current, job['main_sha'], live_main)
            notices = self.ledger.notices(job['repo'], job['number'])
            if ('reviewed_notice_ids' in result and
                    {n['id'] for n in notices} - set(result['reviewed_notice_ids'])):
                reasons.append('New shared evidence requires review before publication')
            kind = action['kind']
            if kind not in ('comment', 'review', 'push', 'ci'):
                reasons.append('Unsupported action; merging is never authorized')
            if kind == 'review':
                event = action.get('event')
                if event not in ('COMMENT', 'APPROVE', 'REQUEST_CHANGES'):
                    reasons.append('Unsupported review event')
                if event == 'APPROVE':
                    if (result.get('outcome') != 'ready' or
                            result.get('publication_qualification', result.get('outcome')) != 'ready'):
                        reasons.append('Approval requires centrally qualified readiness')
                    reasons.extend(gate(checked_original, current, job['main_sha'], live_main, result, notices))
                    if current['author'].lower() == 'jerrettdavis':
                        reasons.append('Owner PR requires independent human review')
                    if current['author'].lower() == actor.lower():
                        reasons.append('Authenticated maintainer cannot approve their own PR')
                if event == 'REQUEST_CHANGES' and current['author'].lower().startswith('dependabot'):
                    reasons.append('Dependabot requires maintainer fixes rather than changes requests')
            if kind in ('push', 'ci'):
                if (kind == 'ci' and 'reviewed_checks_hash' in result and
                        result['reviewed_checks_hash'] != current.get('checks_hash')):
                    reasons.append('CI evidence changed since the execution request')
                source_review = result.get('source_review', {})
                if (source_review.get('manifest_hash') != current.get('workflow_manifest_hash') or
                        not current.get('workflow_manifest_hash') or source_review.get('verdict') != 'clear' or
                        not source_review.get('rationale', '').strip() or not source_review.get('evidence')):
                    reasons.append('Execution/publication requires explicit exact-source provenance review')
                if result.get('publication_qualification') in ('stale', 'needs_followup', 'failed', 'cancelled'):
                    reasons.append('Evidence needs follow-up before code or CI publication')
                if any(n['kind'] in ('security', 'policy') for n in notices):
                    reasons.append('Active execution/publication hold')
                if kind == 'push' and not self.candidate_handler:
                    reasons.append('Candidate publisher is not configured')
                if kind == 'ci' and not self.ci_handler:
                    reasons.append('Screened CI publisher is not configured')
            if reasons:
                receipt = {'action': identity, 'reasons': list(dict.fromkeys(reasons))}
                self.ledger.action_state(identity, 'held', receipt)
                report['held'].append(receipt)
                continue
            before = None
            if kind == 'ci':
                run = next((r for r in current.get('workflows', []) if r['id'] == action.get('workflow_id')), {})
                before = {'run_attempt': run.get('run_attempt', 1), 'status': run.get('status')}
            self.ledger.action_state(identity, 'sending', before)
            try:
                if kind == 'comment':
                    receipt = self.github.request('POST', f'repos/{job["repo"]}/issues/{job["number"]}/comments',
                                                  {'body': redact(action['body']) + '\n\n' + marker})
                elif kind == 'review':
                    receipt = self.github.request('POST', f'repos/{job["repo"]}/pulls/{job["number"]}/reviews',
                                                  {'body': redact(action['body']) + '\n\n' + marker,
                                                   'event': action['event'], 'commit_id': job['head_sha']})
                elif kind == 'push':
                    receipt = self.candidate_handler({**self.ledger.job(job['id']), 'result': result}, raw_current, action)
                else:
                    receipt = self.ci_handler(job, current, action)
            except RejectedWrite as exc:
                receipt = {'action': identity, 'reasons': [str(exc)], 'rejected': True}
                self.ledger.action_state(identity, 'failed', receipt)
                report['held'].append(receipt)
                continue
            self.ledger.action_state(identity, 'sent', receipt)
            report['sent'].append(receipt)
            remember(action, identity, receipt)
            # Never relabel newly fetched evidence as worker-reviewed after our own write.
            if kind == 'push':
                report['held'].append({'action': identity, 'reasons': ['Published candidate requires fresh exact-head qualification']})
                break
        return report
