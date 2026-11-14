"""Persistent, head-based automatic review scheduling for the local panel."""

import json
import threading
import time


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

    def tick(self):
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
            attention = []
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
                attempted = [j for j in exact if not j.get('stale')
                             and j.get('main_sha') == snapshot['main_sha']
                             and j.get('state') in ('ready', 'held', 'reviewed', 'awaiting_ci', 'needs_followup')
                             and ((j.get('result') or {}).get('require_decision') or
                                  run_info(j).get('config', {}).get('require_decision'))]
                completed = any(not j.get('stale') and j.get('state') not in
                                ('stale', 'failed', 'interrupted', 'cancelled') for j in exact)
                if active or (completed and not required) or (required and attempted):
                    current[key]['pending'] = None
                    if required and attempted and pr['author'].lower() not in excluded:
                        attention.append({'number': pr['number'], 'head_sha': head,
                                          'state': attempted[-1].get('state'),
                                          'reasons': (attempted[-1].get('result') or {}).get(
                                              'qualification_limits', ['Decision still required'])})
                    continue
                if (event and settings[event] and pr['author'].lower() not in excluded
                        and (not settings['non_draft_only'] or not pr.get('draft', True))):
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
                self.tick()
            except Exception as error:
                self.error = str(error)
            self.stop.wait(60)
