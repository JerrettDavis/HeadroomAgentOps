"""Bounded dispatch loop; reasoning belongs only to fresh per-PR workers."""

import json
import os
import socket
import time
import uuid
from pathlib import Path

from .harness import validate_result
from .model import canonical, REVIEW_POLICY_VERSION
from .discussion import fingerprint
from .planning import packet
from .policy import gate, revision_gate, terminal_ci_refresh
from .process import process_identity, owner_state
from .reconcile import reconcile_jobs, failure_evidence


class Coordinator:
    def __init__(self, ledger, github, worker, evidence, concurrency=2, poll_seconds=5,
                 timeout_seconds=3600, packet_budget=24576, publisher=None, emit=None, force_fresh=False):
        if not 1 <= concurrency <= 4:
            raise ValueError('Concurrency must be 1–4')
        if timeout_seconds <= 0 or packet_budget <= 0:
            raise ValueError('Budgets must be positive')
        self.ledger, self.github, self.worker, self.evidence = ledger, github, worker, evidence
        self.concurrency, self.poll_seconds = concurrency, poll_seconds
        self.timeout_seconds, self.packet_budget = timeout_seconds, packet_budget
        self.publisher, self.emit = publisher, emit or (lambda event: None)
        self.force_fresh = force_fresh
        self.owner = canonical({**process_identity(), 'nonce': uuid.uuid4().hex})

    def run(self, run_id):
        run = self.ledger.get_run(run_id)
        snapshot = self.ledger.get_snapshot(run['snapshot_id'])
        live_main = self.github.main_sha()
        if live_main != snapshot['main_sha']:
            self.ledger.invalidate(snapshot['repo'], live_main,
                                   {p['number']: p['head_sha'] for p in snapshot['prs']})
            return self.ledger.status(run_id)
        active = self._restore_active(run_id)
        if not self.ledger.get_run(run_id)['stopped']:
            reconciliation = reconcile_jobs(self.ledger, self.github, self.evidence, run_id,
                                            retry_changed=True, publisher=self.publisher)
            self.emit({'event': 'reconciled', **reconciliation})
        while True:
            stopped = bool(self.ledger.get_run(run_id)['stopped'])
            if not stopped:
                while len(active) < self.concurrency:
                    job = self.ledger.claim(self.owner, self.concurrency, run_id)
                    if not job:
                        break
                    try:
                        original = self.github.pull(job['number'])
                        if original['head_sha'] != job['head_sha'] or self.github.main_sha() != job['main_sha']:
                            self.ledger.finish(job['id'], self.owner, 'stale', {'reason': 'Revision changed before dispatch'})
                            continue
                        notices = self.ledger.notices(job['repo'], job['number'])
                        reused = self._reuse(job, original, notices)
                        if reused:
                            state, result = reused
                            self.ledger.finish(job['id'], self.owner, state, result)
                            self.emit({'event': 'reused', 'number': job['number'], 'state': state,
                                       'source_job': result['reused_from']})
                            continue
                        text = packet(original, snapshot, notices, self.evidence, self.packet_budget)
                        prepared = self.worker.prepare({**job, 'timeout_seconds': self.timeout_seconds}, original,
                                                       self.evidence, text)
                        pending = {'container': 'agentops-' + prepared['session_id'],
                                   'session_id': prepared['session_id'], 'directory': prepared['directory'],
                                   'started_at': time.time()}
                        if prepared.get('model_relay'):
                            pending['model_relay'] = prepared['model_relay']
                        # Persist deterministic container identity before creating/starting it.
                        self.ledger.attach_handle(job['id'], self.owner, pending)
                        handle = self.worker.start(prepared)
                        handle['started_at'] = pending['started_at']
                        self.ledger.attach_handle(job['id'], self.owner, handle)
                        for notice in notices:
                            self.ledger.ack_notice(notice['id'], handle['session_id'])
                        active[job['id']] = {'job': job, 'original': original, 'handle': handle,
                                              'started': time.monotonic(), 'notice_ids': {n['id'] for n in notices}}
                        self.emit({'event': 'started', 'number': job['number'], 'session': handle['session_id']})
                    except Exception as exc:
                        # If create/start had an ambiguous response, do not release the slot.
                        persisted = self.ledger.job(job['id'])
                        handle = persisted['handle']
                        observation = self.worker.observe(handle) if handle else {'state': 'missing'}
                        if observation['state'] in ('live', 'unknown'):
                            active[job['id']] = {'job': job, 'original': locals().get('original', {}),
                                'handle': handle, 'started': time.monotonic(), 'notice_ids': set()}
                            self.emit({'event': 'observation_required', 'number': job['number'], 'error': str(exc)})
                        else:
                            self.ledger.finish(job['id'], self.owner, 'failed', {'error': str(exc)})
            for identity, item in list(active.items()):
                job, handle = item['job'], item['handle']
                observation = self.worker.observe(handle)
                self.ledger.heartbeat(identity, self.owner)
                notices = self.ledger.notices(job['repo'], job['number'])
                self._deliver_notices(handle, notices)
                timed_out = time.monotonic() - item['started'] >= self.timeout_seconds
                if observation['state'] == 'live' and (stopped or timed_out):
                    self.worker.cancel(handle)
                    observation = self.worker.observe(handle)
                if observation['state'] in ('unknown', 'live'):
                    continue
                result = {}
                try:
                    if stopped or timed_out:
                        state = 'cancelled' if stopped else 'needs_followup'
                        result = {'reason': 'Stopped' if stopped else 'Session time budget exhausted'}
                    elif observation['state'] == 'missing' or observation.get('exit_code', 0) != 0:
                        state, result = 'failed', {'reason': 'Worker missing or exited unsuccessfully', 'observation': observation}
                        if observation['state'] != 'missing' and hasattr(self.worker, 'diagnostics'):
                            result['diagnostic'] = self.worker.diagnostics(handle)
                    else:
                        result = self.worker.collect(handle, job)
                        validate_result(result, {**job, 'session_id': handle['session_id']})
                        if hasattr(self.worker, 'archive_candidate'):
                            result['candidate_archive'] = self.worker.archive_candidate(handle, job, self.evidence)
                        if hasattr(self.worker, 'metrics'):
                            result['execution'] = self.worker.metrics(handle)
                        current, main_sha = self.github.pull(job['number']), self.github.main_sha()
                        notices = self.ledger.notices(job['repo'], job['number'])
                        revision_reasons = revision_gate(item['original'], current, job['main_sha'], main_sha)
                        refresh = terminal_ci_refresh(item['original'], current, main_sha)
                        if refresh:
                            result['ci_refresh'] = refresh
                        new_ids = {n['id'] for n in notices} - item['notice_ids']
                        acknowledged = set(result['coverage'].get('notices', []))
                        qualification = gate(item['original'], current, job['main_sha'], main_sha, result, notices)
                        if revision_reasons:
                            state = 'stale'
                        elif new_ids - acknowledged:
                            state = 'needs_followup'
                            qualification.append('New relevant shared evidence requires review')
                        elif result['outcome'] == 'ready' and qualification:
                            state = 'held'
                        else:
                            state = result['outcome']
                        result = {**result, 'qualification_limits': qualification,
                                  'review_policy_version': REVIEW_POLICY_VERSION,
                                  'publication_qualification': state,
                                  'reviewed_pr_evidence': self.evidence.put(item['original']),
                                  'reviewed_discussion_hash': item['original'].get('discussion_hash'),
                                  'reviewed_checks_hash': item['original'].get('checks_hash')}
                        result['reviewed_base_ref'] = item['original'].get('base_ref')
                        result['reviewed_head_ref'] = item['original'].get('head_ref')
                        result['reviewed_head_repo'] = item['original'].get('head_repo')
                        result['reviewed_notice_ids'] = sorted(item['notice_ids'] |
                            (acknowledged & {n['id'] for n in notices}))
                        for notice in result.get('notices', []):
                            # Worker suggestions never acquire centrally verified baseline/policy status.
                            authored_notice = self.ledger.notice(job['repo'], 'suggestion', notice['body'],
                                {'job': identity, 'session': handle['session_id'], 'evidence': notice['evidence'],
                                 'proposed_kind': notice['kind']},
                                None if notice.get('scope') == 'repository' else [job['number']])
                            result['reviewed_notice_ids'].append(authored_notice)
                        result['reviewed_notice_ids'] = sorted(set(result['reviewed_notice_ids']))
                        if self.publisher and state != 'stale':
                            publication = self.publisher.publish(job, item['original'], result)
                            result['publication'] = publication
                            result['own_discussion_entries'] = publication['own_discussion_entries']
                            if publication['held'] and state == 'ready':
                                state = 'held'
                        result['evidence_record'] = self.evidence.put(result)
                    if hasattr(self.worker, 'cleanup'):
                        try:
                            self._cleanup_terminal(handle, job, result,
                                                   state in ('failed', 'cancelled') or timed_out)
                        except Exception as exc:
                            result['cleanup_error'] = str(exc)
                    self.ledger.finish(identity, self.owner, state, result)
                    self.emit({'event': 'finished', 'number': job['number'], 'state': state})
                except Exception as exc:
                    failure = {**result, 'error': str(exc), 'publication_qualification': 'failed'}
                    if hasattr(self.worker, 'diagnostics'):
                        try:
                            failure['diagnostic'] = self.worker.diagnostics(handle)
                        except Exception as diagnostic_error:
                            failure['diagnostic_error'] = str(diagnostic_error)
                    if hasattr(self.worker, 'cleanup'):
                        try:
                            self._cleanup_terminal(handle, job, failure, True)
                        except Exception as cleanup_error:
                            failure['cleanup_error'] = str(cleanup_error)
                    self.ledger.finish(identity, self.owner, 'failed', failure)
                    self.emit({'event': 'failed', 'number': job['number'], 'error': str(exc)})
                del active[identity]
            pending = any(j['state'] == 'queued' and not j['stale'] for j in self.ledger.jobs(run_id))
            if not active and (stopped or not pending):
                return self.ledger.status(run_id)
            # Slots occupied by other coordinators remain occupied; do not infer process death.
            time.sleep(self.poll_seconds)

    def _cleanup_terminal(self, handle, job, result, failed):
        if hasattr(self.worker, 'archive_candidate'):
            preserve = False
            try:
                archive = self.worker.archive_candidate(handle, job, self.evidence)
                if archive:
                    result['candidate_archive'] = archive
                elif failed:
                    preserve = True
            except Exception as exc:
                result['artifact_recovery_error'] = str(exc)
                preserve = True
            if preserve:
                result['recovery_checkout_retained'] = handle['directory']
            self.worker.cleanup(handle, preserve_checkout=preserve)
        else:
            self.worker.cleanup(handle)

    def _reuse(self, job, current, notices):
        if self.force_fresh:
            return None
        for prior in self.ledger.prior_reviews(job):
            reference = (prior.get('result') or {}).get('evidence_record')
            if not reference:
                continue
            try:
                proof = self.evidence.get(reference['sha256'])
            except (OSError, ValueError, KeyError) as exc:
                self.emit({'event': 'cached_evidence_rejected', 'number': job['number'], 'reason': str(exc)})
                continue
            if not isinstance(proof, dict):
                continue
            if proof.get('review_policy_version') != REVIEW_POLICY_VERSION or not proof.get('reviewed_pr_evidence'):
                continue
            if any(limit in ('New relevant shared evidence requires review', 'New shared evidence requires review')
                   for limit in proof.get('qualification_limits', [])):
                continue
            try:
                original = self.evidence.get(proof['reviewed_pr_evidence']['sha256'])
            except (OSError, ValueError, KeyError) as exc:
                self.emit({'event': 'cached_evidence_rejected', 'number': job['number'], 'reason': str(exc)})
                continue
            if not isinstance(original, dict):
                continue
            checked = current
            if original.get('discussion_hash_version') == current.get('discussion_hash_version') == 2:
                entries = proof.get('own_discussion_entries', [])
                original = {**original, 'discussion_hash': fingerprint(original, entries)}
                checked = {**current, 'discussion_hash': fingerprint(current, entries)}
            if revision_gate(original, checked, job['main_sha'], job['main_sha']):
                continue
            if set(proof.get('reviewed_notice_ids', [])) != {n['id'] for n in notices}:
                continue
            pending_push = any(action['kind'] == 'push' and
                self.ledger.action(self.ledger.intent(job['id'], action))['state'] != 'sent'
                for action in proof.get('actions', []))
            if pending_push and not proof.get('candidate_archive'):
                continue
            archive_source = None
            if proof.get('candidate_archive'):
                try:
                    archive = self.evidence.get(proof['candidate_archive']['sha256'])
                except (OSError, ValueError, KeyError):
                    continue
                archive_source = (proof.get('candidate_archive_source_job') or
                                  proof.get('reused_from') or prior['id'])
                if (archive.get('job_id') != archive_source or
                        any(archive.get(key) != job[key] for key in ('head_sha', 'main_sha'))):
                    continue
            refresh = terminal_ci_refresh(original, checked, job['main_sha'])
            if failure_evidence(checked) - failure_evidence(original):
                continue
            result = {**proof, 'reused_from': prior['id'], 'reused_evidence': reference,
                      'execution': {'reused': True, 'usage': None, 'duration_seconds': 0}}
            if archive_source:
                result['candidate_archive_source_job'] = archive_source
            result.pop('evidence_record', None)
            if refresh:
                result['ci_refresh'] = refresh
            limits = gate(original, checked, job['main_sha'], job['main_sha'], result, notices)
            state = prior['state'] if prior['state'] == 'needs_followup' else ('held' if limits else prior['state'])
            if prior['state'] != 'needs_followup' and proof.get('outcome') in ('ready', 'awaiting_ci') and not limits:
                state = 'ready'
                result['worker_outcome'] = proof.get('worker_outcome', proof['outcome'])
                result['outcome'] = 'ready'
            result.update(qualification_limits=limits, publication_qualification=state)
            if self.publisher:
                result['publication'] = self.publisher.publish(job, original, result)
                result['own_discussion_entries'] = result['publication']['own_discussion_entries']
                if result['publication']['held'] and state == 'ready':
                    state = 'held'
            result['evidence_record'] = self.evidence.put(result)
            return state, result
        return None

    def _restore_active(self, run_id):
        active = {}
        for job in self.ledger.jobs(run_id):
            if job['state'] != 'running' or owner_state(job['owner']) != 'missing':
                continue
            handle = job['handle']
            if not handle:
                self.ledger.recover(job['id'], lambda h: owner_state(job['owner']))
                continue
            observation = self.worker.observe(handle)
            if observation['state'] == 'missing':
                self.ledger.recover(job['id'], lambda h: 'missing')
                continue
            if observation['state'] == 'unknown':
                continue
            if not self.ledger.adopt(job['id'], self.owner, owner_state):
                continue
            packet_data = json.loads((Path(handle['directory']) / 'input' / 'packet.json').read_text(encoding='utf8'))
            original = self.evidence.get(packet_data['evidence']['sha256'])
            elapsed = max(0, time.time() - handle.get('started_at', time.time()))
            active[job['id']] = {'job': job, 'handle': handle, 'original': original,
                'started': time.monotonic() - elapsed,
                'notice_ids': {n['id'] for n in packet_data.get('notices', [])}}
            self.emit({'event': 'adopted', 'number': job['number'], 'session': handle['session_id']})
        return active

    @staticmethod
    def _deliver_notices(handle, notices):
        directory = Path(handle['directory']) / 'input'
        if directory.is_dir():
            temporary = directory / '.notices.tmp'
            temporary.write_text(canonical(notices), encoding='utf8')
            temporary.replace(directory / 'notices.json')
