"""Durable PR maintenance and main-CI incident orchestration."""
import json
import threading
import time
from pathlib import Path

from .maintenance_github import drift_inventory, main_health, workflow_runs
from .maintenance_git import MaintenanceGit
from .maintenance_store import MaintenanceStore
from .process import owner_state, process_identity
from .redaction import redact


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
        self.recover()

    def recover(self):
        for kind in ('update', 'repair'):
            for row in self.store.list(kind):
                if row.get('state') in ('running', 'publishing', 'agent_running'):
                    state = owner_state(row.get('owner', ''))
                    if state == 'missing':
                        # Prepared pushes and containers have durable identities and
                        # are reconciled, never restarted because observation expired.
                        next_state = ('queued' if row.get('prepared') else
                                      'agent_queued' if row.get('handle') else 'interrupted')
                        self.store.update(kind, row['key'], {'state': next_state,
                            'reason': 'Prior coordinator exited; retained execution will be reconciled'})

    def configure(self, values):
        self.store.configure(values)
        return self.status()

    def refresh(self):
        try:
            inventory = drift_inventory(self.github)
            self.store.put('inventory', 'current', {**inventory, 'fetched': time.time(), 'error': None})
        except Exception as error:
            self.store.update('inventory', 'current', {'error': redact(str(error))})
        return self.status()

    def status(self):
        inventory = self.store.get('inventory', 'current') or {'prs': []}
        settings = self.store.settings()
        updates = self.store.list('update')
        incidents = self.store.list('incident')
        def public(row):
            return {k: v for k, v in row.items() if k not in
                    ('checkout', 'prepared', 'handle', 'owner', 'approval_baseline', 'packet')}
        active = [r for r in incidents if r.get('state') != 'resolved']
        monitor = self.store.get('monitor', 'main') or {'state': 'unknown'}
        return {'settings': {k: settings[k] for k in ('watch', 'autofix', 'reapprove')},
                'inventory': inventory, 'fresh': bool(inventory.get('fetched') and not inventory.get('error')
                                                     and time.time() - inventory['fetched'] < 300),
                'updates': [public(r) for r in updates],
                'secondary': [public(r) for r in updates if r.get('state') in
                              ('needs_remediation', 'interrupted', 'agent_failed')],
                'monitor': monitor, 'incidents': [public(r) for r in incidents],
                'alert': settings['watch'] and not settings['autofix'] and bool(active)}

    def update_selected(self, request):
        numbers = request.get('numbers')
        if (not isinstance(numbers, list) or not numbers or len(numbers) > 1000 or
                any(type(n) is not int or n <= 0 for n in numbers)):
            raise ValueError('Select valid PR numbers')
        inventory = self.store.get('inventory', 'current') or {}
        rows = {r['number']: r for r in inventory.get('prs', [])}
        if any(n not in rows or not rows[n].get('behind') for n in numbers):
            raise ValueError('Refresh and select PRs with a known positive drift')
        reapprove = request.get('reapprove', self.store.settings()['reapprove'])
        if type(reapprove) is not bool:
            raise ValueError('Reapproval must be boolean')
        for number in set(numbers):
            row = rows[number]
            key = f'{number}:{row["head_sha"]}:{row["main_sha"]}'
            self.store.claim('update', key, {**row, 'state': 'queued', 'reapprove': reapprove})
        return self.status()

    def remediate(self, request):
        keys = request.get('keys')
        if not isinstance(keys, list) or not keys or len(keys) > 1000:
            raise ValueError('Select remediation entries')
        for key in keys:
            row = self.store.get('update', key)
            if not row or row.get('state') not in ('needs_remediation', 'interrupted', 'agent_failed'):
                raise ValueError('Entry is not available for remediation')
        for key in keys:
            self.store.update('update', key, {'state': 'agent_queued', 'task': 'rebase'})
        return self.status()

    def retry(self, request):
        key = request.get('key', '')
        if not self.store.transition('update', key, ('needs_remediation', 'interrupted', 'agent_failed'),
                                     {'state': 'queued', 'reason': ''}):
            raise ValueError('Entry is not available for retry')
        return self.status()

    def fix_main(self, request):
        key = request.get('key')
        incident = self.store.get('incident', key) if key else next(
            (r for r in self.store.list('incident') if r.get('state') != 'resolved'), None)
        if not incident or incident.get('state') == 'resolved':
            raise ValueError('No unresolved main CI incident')
        if self.github.main_sha() != incident['head_sha']:
            raise ValueError('Main changed; wait for current CI before repairing this incident')
        key = incident['key']
        if request.get('retry'):
            self.store.transition('repair', key, ('agent_failed', 'interrupted'), {'state': 'agent_queued'})
        self.store.claim('repair', key, {**incident, 'state': 'agent_queued', 'task': 'main',
                                       'main_sha': incident['head_sha'], 'repo': self.config['repo']})
        return self.status()

    def _approvals_before(self, row):
        from .maintenance_workflows import approval_baseline
        return approval_baseline(self.path, self.github, row)

    def _update(self, row):
        key = row['key']
        prepared = row.get('prepared')
        if not prepared:
            current = self.github.request('GET', f'repos/{self.github.repo}/pulls/{row["number"]}')
            if (current['state'] != 'open' or current['head']['sha'] != row['head_sha'] or
                    current['base']['ref'] != 'main' or self.github.main_sha() != row['main_sha']):
                raise ValueError('PR or main changed; refresh and retry')
            baseline = self._approvals_before(row) if row.get('reapprove') else []
            prepared = self.engine.prepare(row)
            self.store.update('update', key, {'prepared': prepared, 'approval_baseline': baseline,
                                             **{k: v for k, v in prepared.items() if k != 'checkout'}})
        self.store.update('update', key, {'state': 'publishing'})
        receipt = self.engine.publish({**row, **prepared})
        self.store.update('update', key, {'state': 'updated', 'receipt': receipt, 'reason': '',
                                         'new_head': prepared['new_head'], 'equivalent': True})

    def _agent(self, kind, row):
        if self.agent is None:
            from .maintenance_agent import MaintenanceAgent
            self.agent = MaintenanceAgent(self.path, self.config, self.github)
        result = self.agent.execute(row, lambda data: self.store.update(kind, row['key'], data))
        self.store.update(kind, row['key'], {**result,
            'state': 'remediated' if kind == 'update' else 'pr_opened', 'reason': '', 'equivalent': False})
        if kind == 'repair':
            self.store.update('incident', row['key'], {**result, 'repair_state': 'pr_opened'})

    def process_pending(self):
        if not self.lock.acquire(blocking=False):
            return
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
                        else:
                            self._agent(kind, row)
                    except Exception as error:
                        self.store.update(kind, row['key'], {'state': 'needs_remediation' if state == 'queued'
                                                              else 'agent_failed', 'reason': redact(str(error))})
                        if kind == 'repair':
                            self.store.update('incident', row['key'], {'repair_state': 'agent_failed',
                                                                      'reason': redact(str(error))})
        finally:
            self.lock.release()

    def _monitor_main(self):
        sha = self.github.main_sha()
        health = main_health(sha, workflow_runs(self.github, sha, 'main'))
        self.store.put('monitor', 'main', {**health, 'last_poll': time.time(), 'error': None})
        if health['state'] == 'broken':
            self.store.claim('incident', sha, {'head_sha': sha, 'state': 'broken', 'created': time.time()})
            self.store.update('incident', sha, {'failures': health['failures'], 'workflows': health['workflows']})
            if self.store.settings()['autofix']:
                self.fix_main({'key': sha})
        elif health['state'] == 'healthy':
            successful = {r.get('workflow_id') for r in health['workflows']}
            for incident in self.store.list('incident'):
                expected = {r.get('workflow_id') for r in incident.get('failures', [])}
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
                if row.get('state') == 'updated' and row.get('reapprove'):
                    try:
                        continue_approvals(self.store, self.github, row)
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
