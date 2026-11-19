"""Persistent, head-based automatic review scheduling for the local panel."""

import json
import threading
import time

from .evidence import Evidence
from .material_diff import compare_review_diff, hunk_coordinates


class AutoReview:
    def __init__(self, launcher):
        self.launcher = launcher
        self.path = launcher.root / 'auto-review.json'
        self.lock = threading.RLock()
        self.poll_lock = threading.Lock()
        self.data = {'repo': launcher.config['repo'], 'settings': {
            'new_prs': False, 'updated_prs': False, 'non_draft_only': True},
            'seen': {}, 'initialized': False, 'last_poll': None, 'last_run': None,
            'retry_launch': None, 'cancelled_runs': [], 'attention': [], 'eligible': 0}
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding='utf8'))
            if saved['repo'] != launcher.config['repo']:
                raise ValueError('Auto-review state belongs to another repository')
            self.data.update(saved)
        self.error = None
        self.stop = threading.Event()
        self.refresh_thread = None
        self.data['reconciling'] = False

    def refresh_reviews(self, snapshot, settings):
        try:
            refresh = self.launcher.refresh_reviews(snapshot, non_draft_only=settings['non_draft_only'])
            with self.lock:
                self.data['reconcile_errors'] = refresh.get('errors', [])
                self.data['resume_runs'] = sorted(set(self.data.get('resume_runs', []) + refresh.get('resume_runs', [])))
        except Exception as error:
            with self.lock:
                self.data['reconcile_errors'] = [{'number': None, 'error': str(error)}]
        finally:
            with self.lock:
                self.data['reconciling'] = False
                self.save()

    def save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.data), encoding='utf8')
            temporary.replace(self.path)

    def configure(self, request):
        with self.lock:
            if not request or set(request) - self.data['settings'].keys():
                raise ValueError('Unknown auto-review setting')
            if any(type(value) is not bool for value in request.values()):
                raise ValueError('Auto-review toggles must be true or false')
            self.data['settings'].update(request)
            self.save()
            return self.status()

    def status(self):
        with self.lock:
            return {**{key: value for key, value in self.data.items() if key != 'seen'},
                    'settings': dict(self.data['settings']), 'error': self.error}

    def material_update(self, pr, prior, main_sha):
        """Persist scheduling notes, keeping the last actual analysis as baseline.

        Manual launches bypass this automatic-only filter and use --force-fresh.
        A skipped update never becomes a reviewed or approved ledger job.
        """
        notes = self.data.setdefault('review_notes', {})
        evidence = Evidence(self.launcher.root / 'evidence')
        inventory_identity = {key: pr.get(key) for key in
                              ('number', 'head_sha', 'base_sha', 'base_ref', 'head_repo', 'updated_at')}
        cached = notes.get(str(pr['number']), {})
        if (all(pr.get(key) for key in ('base_sha', 'base_ref', 'head_repo', 'updated_at')) and
                cached.get('inventory_identity') == inventory_identity and
                cached.get('main_sha') == main_sha and cached.get('material') is False):
            try:
                recorded = evidence.get(cached['evidence']['sha256'])
                if recorded == {key: value for key, value in cached.items() if key != 'evidence'}:
                    return cached
            except (OSError, ValueError, KeyError, TypeError):
                pass
        candidates = sorted(prior, key=lambda j: j.get('created', 0), reverse=True)
        for job in candidates:
            reference = (job.get('result') or {}).get('evidence_record')
            if not reference or job.get('state') not in (
                    'ready', 'held', 'reviewed', 'awaiting_ci', 'needs_followup', 'stale'):
                continue
            try:
                proof = evidence.get(reference['sha256'])
                previous = evidence.get(proof['reviewed_pr_evidence']['sha256'])
                if (proof['number'] != pr['number'] or previous['number'] != pr['number'] or
                        proof['head_sha'] != job['head_sha'] or
                        previous['head_sha'] != job['head_sha']):
                    continue
                if (previous['head_sha'] == pr['head_sha'] and
                        isinstance(previous.get('files'), list) and pr.get('base_ref') and
                        previous.get('base_ref') == pr['base_ref'] and pr.get('head_repo') and
                        previous.get('head_repo') == pr['head_repo']):
                    # A stopped duplicate cannot create new source at an already
                    # analysed immutable head. Target/main qualification stays separate.
                    record = {'material': False, 'reason': 'unchanged_reviewed_head',
                              'head_sha': pr['head_sha'], 'main_sha': main_sha,
                              'inventory_identity': inventory_identity, 'source_job': job['id'],
                              'prior_review': reference, 'current_inventory': evidence.put(pr),
                              'recorded_at': time.time(), 'scope': 'scheduling_only_no_approval'}
                    notes[str(pr['number'])] = {**record, 'evidence': evidence.put(record)}
                    return record
                current = self.launcher.github.pull(pr['number'])
                if current['head_sha'] != pr['head_sha']:
                    return {'material': True, 'reason': 'head_changed_during_comparison'}
                if not isinstance(previous.get('files'), list) or not isinstance(current.get('files'), list):
                    return {'material': True, 'reason': 'incomplete_diff'}
                # Fetch full content only for changed Python/manifests. Patch-only
                # whitespace guesses can hide literals and significant indentation.
                previous = {**previous, 'files': [dict(f) for f in previous.get('files', [])]}
                current = {**current, 'files': [dict(f) for f in current.get('files', [])]}
                old_files = {f['filename']: f for f in previous['files']}
                for file in current['files']:
                    old_file = old_files.get(file['filename'])
                    if old_file and hunk_coordinates(old_file) != hunk_coordinates(file):
                        for original_pr, item in ((previous, old_file), (current, file)):
                            try:
                                content = self.launcher.github.review_base_content(original_pr, item)
                                if isinstance(content, str):
                                    item['base_content'] = content
                            except (RuntimeError, OSError, ValueError, KeyError):
                                pass
                    if (old_file and old_file.get('patch') != file.get('patch') and
                            (file['filename'].endswith('.py') or
                             file['filename'].split('/')[-1] in ('package.json', 'pyproject.toml'))):
                        for item in (old_file, file):
                            if item.get('sha'):
                                try:
                                    content = self.launcher.github.review_blob(item['sha'])
                                    if isinstance(content, str):
                                        item['content'] = content
                                except (RuntimeError, OSError, ValueError, KeyError):
                                    pass
                decision = compare_review_diff(previous, current)
                record = {**decision, 'head_sha': pr['head_sha'], 'main_sha': main_sha,
                          'inventory_identity': inventory_identity,
                          'source_job': job['id'], 'prior_review': reference,
                          'current_pr': evidence.put(current), 'recorded_at': time.time(),
                          'scope': 'scheduling_only_no_approval'}
                notes[str(pr['number'])] = {**record, 'evidence': evidence.put(record)}
                return record
            except (OSError, ValueError, KeyError, TypeError):
                # An unavailable baseline must never suppress a needed review.
                continue
        return {'material': True, 'reason': 'no_verified_prior_review'}

    def tick(self, background=False):
        # Slow GitHub requests must not block dashboard status or toggle changes.
        with self.poll_lock:
            settings = self.data['settings']
            if not (settings['new_prs'] or settings['updated_prs']):
                return
            if self.data.get('retry_launch'):
                result = self.launcher.retry_launch(self.data['retry_launch'])
                self.data['retry_launch'] = result if result.get('error') else None
                if result.get('cancelled'):
                    # A changed/drafted PR will be planned again from fresh evidence.
                    self.data['cancelled_runs'] = [*self.data.get('cancelled_runs', []), result['run_id']]
                self.save()
                if result.get('error'):
                    raise ValueError(result['error'])
            snapshot = self.launcher.github.snapshot(queue_only=True)
            if snapshot.get('complete') is not True or snapshot.get('repo') != self.data['repo']:
                raise ValueError('Complete repository inventory required for auto-review')
            if (time.time() - (self.data.get('last_reconcile') or 0) >= 300
                    and not (self.refresh_thread and self.refresh_thread.is_alive())):
                self.data['last_reconcile'] = time.time()
                self.data['reconciling'] = True
                if background:
                    self.refresh_thread = threading.Thread(target=self.refresh_reviews,
                        args=(snapshot, dict(settings)), daemon=True, name='review-reconciliation')
                    self.refresh_thread.start()
                else:
                    self.refresh_reviews(snapshot, settings)
            if self.data.get('resume_runs') and (settings['new_prs'] or settings['updated_prs']):
                with self.lock:
                    pending = list(self.data['resume_runs'])
                    self.data['resume_runs'] = []
                remaining = self.launcher.resume_reviews(pending, non_draft_only=settings['non_draft_only'])
                with self.lock:
                    self.data['resume_runs'] = sorted(set(self.data['resume_runs'] + remaining))
            seen = self.data['seen']
            # The ledger also covers a crash after launch but before this file was saved.
            jobs = [j for j in self.launcher.ledger.jobs() if j['repo'] == self.data['repo']
                    and j.get('run_id') not in self.data.get('cancelled_runs', [])]
            runs = {}
            def run_info(job):
                identity = job.get('run_id')
                if identity and identity not in runs:
                    runs[identity] = self.launcher.ledger.get_run(identity)
                return runs.get(identity, {})
            excluded = {a.lower() for a in self.launcher.config.get('exclude_authors', [])}
            selected = []
            current = {}
            attention = [{'number': item.get('number'), 'state': 'reconcile_error',
                          'reasons': [item['error']]} for item in self.data.get('reconcile_errors', [])]
            for pr in snapshot['prs']:
                key = str(pr['number'])
                old = seen.get(key)
                head = pr['head_sha']
                event = old.get('pending') if old else None
                prior = [j for j in jobs if j['number'] == pr['number']]
                required = pr.get('review_decision') == 'REVIEW_REQUIRED'
                if self.data['initialized']:
                    if old is None:
                        event = 'new_prs'
                    elif old['head'] != head and (event != 'new_prs' or not settings['new_prs']):
                        event = 'updated_prs'
                if required and not event:
                    event = 'updated_prs' if prior else 'new_prs'
                current[key] = {'head': head, 'pending': event}
                exact = [j for j in prior if j['head_sha'] == head]
                active = any(j.get('state') in ('queued', 'running') and not j.get('stale')
                             and not run_info(j).get('stopped') for j in exact)
                failures = [j for j in exact if j.get('state') == 'failed' and not j.get('stale')
                            and j.get('main_sha') == snapshot['main_sha']]
                attempted = [j for j in exact if not j.get('stale')
                             and j.get('main_sha') == snapshot['main_sha']
                             and j.get('state') in ('ready', 'held', 'reviewed', 'awaiting_ci', 'needs_followup')
                             and ((j.get('result') or {}).get('require_decision') or
                                  run_info(j).get('config', {}).get('require_decision'))]
                completed = any(not j.get('stale') and j.get('state') not in
                                ('queued', 'running', 'stale', 'failed', 'interrupted', 'cancelled') for j in exact)
                if (not event and not completed and not active and any(
                        j.get('state') == 'queued' and run_info(j).get('stopped') for j in exact)):
                    # A cancelled batch never inspected this head. Re-evaluate
                    # its contribution against real prior analysis before dispatch.
                    event = 'updated_prs'
                    current[key]['pending'] = event
                if active or (completed and not required) or (required and attempted):
                    current[key]['pending'] = None
                    if required and attempted and pr['author'].lower() not in excluded:
                        attention.append({'number': pr['number'], 'head_sha': head,
                                          'state': attempted[-1].get('state'),
                                          'reasons': (attempted[-1].get('result') or {}).get(
                                              'qualification_limits', ['Decision still required'])})
                    continue
                if failures and not active and pr['author'].lower() not in excluded:
                    latest_failure = max(failures, key=lambda j: j.get('heartbeat') or j.get('created', 0))
                    delay = min(3600, 300 * 2 ** min(len(failures) - 1, 4))
                    retry_at = (latest_failure.get('heartbeat') or latest_failure.get('created', 0)) + delay
                    if time.time() < retry_at:
                        attention.append({'number': pr['number'], 'head_sha': head, 'state': 'retry_wait',
                                          'retry_at': retry_at, 'failures': len(failures),
                                          'reasons': ['Repeated worker failure; automatic retry is delayed',
                                                      (latest_failure.get('result') or {}).get('reason',
                                                       (latest_failure.get('result') or {}).get('error', 'Worker failed'))]})
                        continue
                if (event and settings[event] and pr['author'].lower() not in excluded
                        and (not settings['non_draft_only'] or not pr.get('draft', True))):
                    if prior:
                        decision = self.material_update(pr, prior, snapshot['main_sha'])
                        if not decision['material']:
                            current[key]['pending'] = None
                            attention.append({'number': pr['number'], 'head_sha': head,
                                              'state': 'review_reused',
                                              'reasons': [decision['reason']],
                                              'source_job': decision['source_job']})
                            continue
                    selected.append(pr['number'])
            self.data.update(seen=current, initialized=True, last_poll=time.time(),
                             attention=attention, eligible=len(selected))
            self.save()
            if selected and not any(p['running'] for p in self.launcher.status().values()):
                non_draft_only = settings['non_draft_only']
                preview = self.launcher.preview({'source': 'batch', 'prs': ' '.join(map(str, selected)),
                                                 'non_draft_only': non_draft_only,
                                                 'require_decision': True})
                expected = {n: current[str(n)]['head'] for n in selected}
                refreshed = {p['number']: p['head_sha'] for p in preview['prs']}
                selected = [n for n in selected if refreshed.get(n) == expected[n]]
                if not selected:
                    return
                if (settings['non_draft_only'] != non_draft_only or
                        any(not settings[current[str(n)]['pending']] for n in selected)):
                    return
                result = self.launcher.launch({'preview_id': preview['preview_id'], 'selected': selected})
                if result.get('error'):
                    self.data['retry_launch'] = result
                    self.save()
                    raise ValueError(result['error'])
                for number in selected:
                    current[str(number)]['pending'] = None
                self.data['last_run'] = result['run_id']
                self.data['eligible'] -= len(selected)
                self.save()
            self.error = None

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick(background=True)
            except Exception as error:
                self.error = str(error)
            self.stop.wait(60)
