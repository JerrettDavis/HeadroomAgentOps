"""Durable PR maintenance and main-CI incident orchestration."""
import json
import threading
import time
from pathlib import Path

from .maintenance_github import drift_inventory, main_health, workflow_runs, pr_binding
from .maintenance_git import MaintenanceGit
from .maintenance_store import MaintenanceStore
from .process import owner_state, process_identity
from .redaction import redact
from .publisher import publication_lock


class MaintenanceService:
    def __init__(self, path, config, github, *, engine=None, agent=None):
        self.path, self.config, self.github = Path(path), dict(config), github
        self.store = MaintenanceStore(path, config['repo'])
        self.engine = engine or MaintenanceGit(self.path.resolve().parent / 'maintenance-git', config['repo'])
        self.agent = agent
        self.stop = threading.Event()
        self.lock, self.poll_lock = threading.Lock(), threading.Lock()
        self.owner = json.dumps(process_identity(), sort_keys=True)
        self.worker_thread = None
        self.refresh_thread = None
        self.recover()

    def dispatch(self, action, request):
        actions = {'update': self.update_selected, 'remediate': self.remediate,
                   'retry': self.retry, 'settings': self.configure, 'fix-main': self.fix_main,
                   'authorize-source': self.authorize_source}
        if action == 'refresh':
            if not self.refresh_thread or not self.refresh_thread.is_alive():
                self.refresh_thread = threading.Thread(target=self.refresh, daemon=True,
                                                       name='maintenance-inventory')
                self.refresh_thread.start()
        elif action in actions:
            if action in ('update', 'remediate', 'retry', 'authorize-source'):
                with publication_lock(self.path):
                    actions[action](request)
            else:
                actions[action](request)
        else:
            raise ValueError('Unknown maintenance action')
        if not self.worker_thread or not self.worker_thread.is_alive():
            self.worker_thread = threading.Thread(target=self.process_pending, daemon=True,
                                                  name='maintenance-work')
            self.worker_thread.start()
        return self.status()

    def recover(self):
        for kind in ('update', 'repair'):
            for row in self.store.list(kind):
                if row.get('state') in ('running', 'publishing', 'agent_running'):
                    state = owner_state(row.get('owner', ''))
                    if state == 'missing':
                        # Prepared pushes and containers have durable identities and
                        # are reconciled, never restarted because observation expired.
                        next_state = ('agent_queued' if kind == 'repair' or row.get('task') == 'rebase' else
                                      'queued' if row.get('prepared') else 'interrupted')
                        self.store.update(kind, row['key'], {'state': next_state,
                            'reason': 'Prior coordinator exited; retained execution will be reconciled'})

    def configure(self, values):
        self.store.configure(values)
        return self.status()

    def mutation_gate(self, row=None, ci=False):
        from .maintenance_policy import mutation_gate
        mutation_gate(self.path, self.config, row, ci=ci)

    def refresh(self):
        try:
            inventory = drift_inventory(self.github)
            excluded = {a.lower() for a in self.config.get('exclude_authors', [])}
            for row in inventory['prs']:
                row['excluded'] = row['author'].lower() in excluded
            self.store.put('inventory', 'current', {**inventory, 'fetched': time.time(), 'error': None})
        except Exception as error:
            self.store.update('inventory', 'current', {'error': redact(str(error))})
        return self.status()

    def status(self):
        inventory = self.store.get('inventory', 'current') or {'prs': []}
        settings = self.store.settings()
        updates = self.store.list('update')
        latest = {}
        for row in updates:
            latest.setdefault(row['number'], row)
        inventory = {**inventory, 'prs': [{**r, 'last_update': latest.get(r['number'], {}).get('state')}
                                         for r in inventory.get('prs', [])]}
        incidents = self.store.list('incident')
        def public(row):
            return {k: v for k, v in row.items() if k not in
                    ('checkout', 'prepared', 'handle', 'assignment', 'owner', 'approval_baseline', 'packet')}
        active = [r for r in incidents if r.get('state') != 'resolved']
        monitor = self.store.get('monitor', 'main') or {'state': 'unknown'}
        return {'settings': {k: settings[k] for k in ('watch', 'autofix', 'reapprove')},
                'inventory': inventory, 'fresh': bool(inventory.get('fetched') and not inventory.get('error')
                                                     and time.time() - inventory['fetched'] < 300),
                'updates': [public(r) for r in updates],
                'secondary': [public(r) for r in updates if r.get('state') in
                              ('needs_remediation', 'interrupted', 'agent_failed', 'source_held')],
                'monitor': monitor, 'incidents': [public({**r, **({'repair_state': repair['state'],
                    **{k: repair[k] for k in ('candidate', 'source_manifest', 'protected_paths', 'summary',
                                              'validation', 'reason') if k in repair}}
                    if (repair := self.store.get('repair', r['key'])) else {})}) for r in incidents],
                'alert': settings['watch'] and not settings['autofix'] and bool(active)}

    def authorize_source(self, request):
        row = self.store.get('repair', request.get('key', ''))
        if (not row or row.get('state') != 'source_held' or request.get('candidate') != row.get('candidate')
                or request.get('source_manifest') != row.get('source_manifest')):
            raise ValueError('Source candidate changed; refresh and review the current repair')
        self.mutation_gate(row)
        if not self.store.transition('repair', row['key'], ('source_held',), {'state': 'agent_queued',
            'source_authorization': {'candidate': row['candidate'], 'source_manifest': row['source_manifest'],
                                     'actor': 'local dashboard maintainer', 'at': time.time()}}):
            raise ValueError('Source repair was already claimed')
        return self.status()

    def source_review(self, key):
        row = self.store.get('repair', key)
        if not row or row.get('state') != 'source_held' or not row.get('prepared'):
            raise ValueError('No retained source repair for this incident')
        checkout = self.engine.owned_checkout(row['prepared']['checkout'])
        self.engine._sha(row['main_sha'])
        self.engine._sha(row['candidate'])
        diff = self.engine.git(['diff', '--no-ext-diff', '--no-textconv', '--no-color',
            row['main_sha'], row['candidate'], '--', '.github', '.gitmodules'], checkout)
        return {k: row.get(k) for k in ('candidate', 'source_manifest', 'summary', 'validation', 'protected_paths')} | {
            'diff': diff[:200000], 'truncated': len(diff) > 200000}

    def update_selected(self, request):
        self.mutation_gate()
        numbers = request.get('numbers')
        if (not isinstance(numbers, list) or not numbers or len(numbers) > 1000 or
                any(type(n) is not int or n <= 0 for n in numbers)):
            raise ValueError('Select valid PR numbers')
        inventory = self.store.get('inventory', 'current') or {}
        if inventory.get('error') or time.time() - inventory.get('fetched', 0) >= 300:
            raise ValueError('A fresh inventory is required; refresh before submitting')
        rows = {r['number']: r for r in inventory.get('prs', [])}
        if any(n not in rows or not rows[n].get('behind') for n in numbers):
            raise ValueError('Refresh and select PRs with a known positive drift')
        reapprove = request.get('reapprove', self.store.settings()['reapprove'])
        if type(reapprove) is not bool:
            raise ValueError('Reapproval must be boolean')
        for number in set(numbers):
            row = rows[number]
            key = f'{number}:{row["head_sha"]}:{row["main_sha"]}'
            self.store.claim('update', key, {**row, 'state': 'queued', 'reapprove': reapprove,
                                             'created': time.time(), 'approval_deadline': time.time() + 86400})
        return self.status()

    def remediate(self, request):
        self.mutation_gate()
        keys = request.get('keys')
        if not isinstance(keys, list) or not keys or len(keys) > 1000:
            raise ValueError('Select remediation entries')
        for key in keys:
            row = self.store.get('update', key)
            if not row or row.get('state') not in ('needs_remediation', 'interrupted', 'agent_failed'):
                raise ValueError('Entry is not available for remediation')
        for key in keys:
            row = self.store.get('update', key)
            if not self.store.transition('update', key, ('needs_remediation', 'interrupted', 'agent_failed'),
                    {'state': 'agent_queued', 'task': 'rebase',
                     'retry_requested': row.get('state') in ('interrupted', 'agent_failed')}):
                raise ValueError('Entry was already claimed by another maintenance request')
        return self.status()

    def retry(self, request):
        key = request.get('key', '')
        row = self.store.get('update', key) or {}
        self.mutation_gate(row)
        if row.get('task') == 'rebase':
            return self.remediate({'keys': [key]})
        if not self.store.transition('update', key, ('needs_remediation', 'interrupted', 'agent_failed'),
                                     {'state': 'queued', 'reason': ''}):
            raise ValueError('Entry is not available for retry')
        return self.status()

    def fix_main(self, request):
        self.mutation_gate()
        key = request.get('key')
        incident = self.store.get('incident', key) if key else next(
            (r for r in self.store.list('incident') if r.get('state') != 'resolved'), None)
        if not incident or incident.get('state') == 'resolved':
            raise ValueError('No unresolved main CI incident')
        if not incident.get('failures'):
            raise ValueError('Workflow approval is needed; code repair is not appropriate')
        if self.github.main_sha() != incident['head_sha']:
            raise ValueError('Main changed; wait for current CI before repairing this incident')
        health = main_health(incident['head_sha'], workflow_runs(self.github, incident['head_sha'], 'main'), self.github.repo)
        if not health['failures']:
            raise ValueError('No actionable current-main failure; wait for the current rerun')
        key = incident['key']
        with publication_lock(self.path):
            if request.get('retry'):
                self.store.transition('repair', key, ('agent_failed', 'interrupted', 'publication_unknown'),
                                       {'state': 'agent_queued', 'retry_requested': True})
            self.store.claim('repair', key, {**incident, 'state': 'agent_queued', 'task': 'main',
                                           'main_sha': incident['head_sha'], 'repo': self.config['repo']})
        return self.status()

    def _approvals_before(self, row):
        from .maintenance_workflows import approval_baseline
        return approval_baseline(self.path, self.github, row)

    def _update(self, row):
        self.mutation_gate(row)
        key = row['key']
        prepared = row.get('prepared')
        if not prepared:
            current = self.github.request('GET', f'repos/{self.github.repo}/pulls/{row["number"]}')
            if (not pr_binding(current, row, self.github.repo, {row['head_sha']}) or
                    self.github.main_sha() != row['main_sha']):
                raise ValueError('PR or main changed; refresh and retry')
            try:
                baseline = self._approvals_before(row) if row.get('reapprove') else []
            except (RuntimeError, ValueError, OSError, KeyError):
                baseline = []
                self.store.update('update', key, {'approval_status': 'Approval provenance unavailable; manual approval required'})
            prepared = self.engine.prepare(row)
            self.store.update('update', key, {'prepared': prepared, 'approval_baseline': baseline,
                                             **{k: v for k, v in prepared.items() if k != 'checkout'}})
        self.store.update('update', key, {'state': 'publishing'})
        current = self.github.request('GET', f'repos/{self.github.repo}/pulls/{row["number"]}')
        # Reconcile an already-published exact candidate even when main has since
        # advanced; do not repeat a write against a closed/retargeted PR.
        if (not pr_binding(current, row, self.github.repo, {row['head_sha'], prepared['new_head']}) or
                (current['head']['sha'] != prepared['new_head'] and self.github.main_sha() != row['main_sha'])):
            raise ValueError('PR or main changed before publication; refresh and retry')
        self.mutation_gate({**row, 'author': current.get('user', {}).get('login', row.get('author'))})
        receipt = self.engine.publish({**row, **prepared,
            'reconcile_only': current['head']['sha'] == prepared['new_head']})
        self.store.update('update', key, {'state': 'updated', 'receipt': receipt, 'reason': '',
                                         'new_head': prepared['new_head'], 'equivalent': True})

    def _agent(self, kind, row):
        self.mutation_gate(row)
        if kind == 'repair':
            health = main_health(row['main_sha'], workflow_runs(self.github, row['main_sha'], 'main'), self.github.repo)
            if self.github.main_sha() != row['main_sha'] or not health['failures']:
                self.store.update(kind, row['key'], {'state': 'resolved_without_pr',
                    'reason': 'Main changed or current rerun no longer fails; no agent launched'})
                return
        if self.agent is None:
            from .maintenance_agent import MaintenanceAgent
            self.agent = MaintenanceAgent(self.path, self.config, self.github)
        result = self.agent.execute(row, lambda data: self.store.update(kind, row['key'], data))
        self.store.update(kind, row['key'], {**result,
            'state': 'remediated' if kind == 'update' else
                     'pr_closed' if result.get('pr_state') == 'closed' else 'pr_opened', 'reason': '', 'equivalent': False})
        if kind == 'repair':
            self.store.update('incident', row['key'], {**result, 'repair_state': 'pr_opened'})

    def process_pending(self):
        if not self.lock.acquire(blocking=False):
            return
        changed = False
        try:
            for kind in ('update', 'repair'):
                for row in reversed(self.store.list(kind)):
                    if self.stop.is_set():
                        return
                    state = row.get('state')
                    if state not in ('queued', 'agent_queued'):
                        continue
                    if not self.store.transition(kind, row['key'], (state,),
                            {'state': 'running' if state == 'queued' else 'agent_running', 'owner': self.owner}):
                        continue
                    try:
                        if state == 'queued':
                            self._update(row)
                            changed = True
                        else:
                            self._agent(kind, row)
                            changed = kind == 'update' or changed
                    except Exception as error:
                        from .maintenance_agent import ObservationPending, SourcePublicationHeld, PublicationUnknown
                        if isinstance(error, ObservationPending):
                            self.store.update(kind, row['key'], {'state': 'agent_queued', 'reason': str(error)})
                            continue
                        if isinstance(error, SourcePublicationHeld):
                            self.store.update(kind, row['key'], {'state': 'source_held', 'reason': str(error)})
                            continue
                        if isinstance(error, PublicationUnknown):
                            self.store.update(kind, row['key'], {'state': 'publication_unknown', 'reason': str(error)})
                            continue
                        self.store.update(kind, row['key'], {'state': 'needs_remediation' if state == 'queued'
                                                              else 'agent_failed', 'reason': redact(str(error))})
                        if kind == 'repair':
                            self.store.update('incident', row['key'], {'repair_state': 'agent_failed',
                                                                      'reason': redact(str(error))})
        finally:
            self.lock.release()
        if changed:
            self.refresh()

    def _monitor_main(self):
        sha = self.github.main_sha()
        health = main_health(sha, workflow_runs(self.github, sha, 'main'), self.github.repo)
        self.store.put('monitor', 'main', {**health, 'last_poll': time.time(), 'error': None})
        relevant = {r['context_key'] for r in health['workflows']}
        if health['state'] in ('broken', 'approval_needed'):
            self.store.claim('incident', sha, {'head_sha': sha, 'state': health['state'], 'created': time.time()})
            prior = self.store.get('incident', sha)
            coverage = set(prior.get('coverage', [])) | relevant
            failures = {r.get('context_key', str(r.get('workflow_id'))): r for r in prior.get('failures', [])}
            for run in health['workflows']:
                identity = run['context_key']
                if run.get('conclusion') in ('failure', 'timed_out', 'startup_failure'):
                    failures[identity] = run
                elif run.get('conclusion') == 'success':
                    failures.pop(identity, None)
            # Keep all observed workflows on this head, including previously
            # successful or failed workflows omitted by a later partial result.
            self.store.update('incident', sha, {'state': health['state'], 'failures': list(failures.values()),
                'workflows': health['workflows'], 'coverage': sorted(x for x in coverage if x is not None)})
            if self.store.settings()['autofix'] and health['failures']:
                self.fix_main({'key': sha})
        elif health['state'] == 'healthy':
            successful = {r['context_key'] for r in health['workflows']}
            for incident in self.store.list('incident'):
                expected = set(incident.get('coverage', [])) or {r.get('workflow_id') for r in incident.get('failures', [])}
                if incident['state'] != 'resolved' and expected <= successful:
                    self.store.update('incident', incident['key'], {'state': 'resolved', 'resolved_sha': sha})

    def tick(self):
        if not self.poll_lock.acquire(blocking=False):
            return
        try:
            if self.store.settings()['watch']:
                try:
                    self._monitor_main()
                except Exception as error:
                    self.store.update('monitor', 'main', {'error': redact(str(error)), 'last_error': time.time()})
            from .maintenance_workflows import continue_approvals
            for row in self.store.list('update'):
                if row.get('state') == 'updated' and row.get('reapprove') and not row.get('approval_terminal'):
                    try:
                        with publication_lock(self.path):
                            continue_approvals(self.store, self.github, row, self.config, self.path)
                    except Exception as error:
                        self.store.update('update', row['key'], {'approval_status': redact(str(error))})
        finally:
            self.poll_lock.release()

    def run(self):
        while not self.stop.is_set():
            self.tick()
            if not self.worker_thread or not self.worker_thread.is_alive():
                self.worker_thread = threading.Thread(target=self.process_pending, daemon=True,
                                                      name='maintenance-work')
                self.worker_thread.start()
            self.stop.wait(30)

    def close(self):
        self.stop.set()
        if self.worker_thread and self.worker_thread is not threading.current_thread():
            self.worker_thread.join(timeout=1)
