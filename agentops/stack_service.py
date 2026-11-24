"""Bounded mechanical preparation and human-triggered aggregate PR publication."""
import json
from contextlib import closing
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

from .merge_priority import collect_priority
from .model import digest
from .process import owner_state, process_identity
from .redaction import redact
from .stack_git import GitStack
from .stacking import StackStore, plan_stack


ACTIVE = ('preparing', 'publishing', 'checking')


class StackService:
    def __init__(self, path, config, github, *, collect=None, engine=None):
        self.config, self.github = dict(config), github
        self.store = StackStore(path)
        comparisons = {}
        self.collect = collect or (lambda: collect_priority(github, comparisons, full=True))
        settings = self.config.get('stacking', {})
        self.validation = {'commands': settings.get('commands', []),
                           'image': self.config.get('image', 'headroom-agentops-worker:local'),
                           'timeout': settings.get('timeout_seconds', 900)}
        self.engine = engine or GitStack(Path(path).resolve().parent / 'stacks', config['repo'],
                                        **self.validation)
        self.owner = json.dumps(process_identity(), sort_keys=True)
        self.thread, self.lock = None, threading.Lock()
        self.recover()

    def recover(self):
        for record in self.store.active():
            if record['state'] in ACTIVE and owner_state(record['owner'] or '') == 'missing':
                self.store.update(record['id'], {'state': 'publication_unknown' if record['state'] == 'publishing'
                                                else 'interrupted', 'phase': 'Prior process exited; explicit retry required'},
                                  owner=record['owner'])

    def close(self):
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=30)

    def _plan(self, numbers):
        data = self.collect()
        if data['repo'].lower() != self.config['repo'].lower():
            raise ValueError('Inventory repository mismatch')
        plan = plan_stack(data, numbers)
        plan['plan_id'] = plan['id']
        plan['id'] = digest({'plan': plan['id'], 'validation': self.validation})
        return plan

    def _record(self, request):
        record = self.store.get(request['id'])
        if record['repo'].lower() != self.config['repo'].lower():
            raise ValueError('Stack belongs to another repository')
        return record

    def _fresh(self, record):
        current = self._plan(record['selected'])
        if current['id'] != record['id']:
            raise ValueError('Source heads, dependencies, target or validation settings changed; prepare again')
        if current['blockers']:
            raise ValueError('; '.join(current['blockers']))
        return time.time()

    def prepare(self, request):
        with self.lock:
            self.recover()
            if self.store.active():
                raise ValueError('Another stack operation is active')
            plan = self._plan(request.get('numbers'))
            plan['initiator'] = 'local dashboard user'
            record, started = self.store.begin_preparation(plan, self.owner)
            if not started:
                return self.public(record)
            self.thread = threading.Thread(target=self._prepare, args=(record['id'],),
                                           name='stack-preparation', daemon=True)
            self.thread.start()
            return self.public(self.store.get(record['id']))

    def _prepare(self, identity):
        try:
            record = self.store.get(identity)
            result = self.engine.prepare(record, lambda data: self.store.update(identity, data, owner=self.owner))
            if result['state'] == 'prepared':
                try:
                    result['verified_at'] = self._fresh(record)
                except Exception as error:
                    result.update(state='stale', error=redact(str(error)))
            self.store.update(identity, result, owner=self.owner)
        except Exception as error:
            self.store.update(identity, {'state': 'failed', 'error': redact(str(error)),
                                         'phase': 'Preparation failed'}, owner=self.owner)

    @staticmethod
    def public(record):
        result = {k: v for k, v in record.items() if k not in ('owner', 'checkout')}
        result['members'] = [{k: p[k] for k in ('number', 'title', 'author', 'head_sha', 'base_ref',
                                              'prerequisites', 'ci', 'review_decision') if k in p}
                             for p in record['members']]
        result['fresh'] = time.time() - record.get('verified_at', 0) < 180
        if record['state'] in ACTIVE and owner_state(record.get('owner') or '') == 'missing':
            result.update(state='publication_unknown' if record['state'] == 'publishing' else 'interrupted',
                          fresh=False, phase='Prior process exited; explicit retry required')
        return result

    def status(self):
        return {'repo': self.config['repo'], 'publish_enabled': self.config.get('publish') is True,
                'model_calls': 0, 'stacks': [self.public(r) for r in self.store.list()
                                          if r['repo'].lower() == self.config['repo'].lower()]}

    @classmethod
    def peek(cls, path, config):
        """Dashboard GETs never create/migrate a ledger or alter existing records."""
        records = []
        path = Path(path).resolve()
        if path.exists():
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)) as db:
                db.row_factory = sqlite3.Row
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='merge_stacks'").fetchone():
                    records = [StackStore._decode(row) for row in db.execute(
                        'SELECT * FROM merge_stacks ORDER BY created DESC LIMIT 100')]
        return {'repo': config['repo'], 'publish_enabled': config.get('publish') is True, 'model_calls': 0,
                'stacks': [cls.public(r) for r in records if r['repo'].lower() == config['repo'].lower()]}

    def _branch(self, record):
        branch = 'agentops/stacks/' + record['id']
        refs = self.github.request('GET', f'repos/{self.github.repo}/git/matching-refs/heads/{branch}')
        found = [r for r in refs if r['ref'] == 'refs/heads/' + branch]
        if len(found) > 1 or (found and found[0]['object']['sha'] != record['revision']):
            raise ValueError('Dedicated stack branch differs from prepared revision; refusing to overwrite')
        return bool(found)

    def _existing_pr(self, record):
        branch = 'agentops/stacks/' + record['id']
        query = urlencode({'state': 'all', 'head': f'{self.github.owner}:{branch}', 'base': record['target_branch']})
        prs = [p for p in self.github.pages(f'repos/{self.github.repo}/pulls?{query}')
               if p['head']['ref'] == branch and p['head']['repo'] and
               p['head']['repo']['full_name'].lower() == self.github.repo.lower()]
        if len(prs) > 1:
            raise ValueError('Multiple aggregate PRs found; inspect on GitHub')
        if not prs:
            return None
        pr = prs[0]
        if (pr['head']['sha'] != record['revision'] or
                f'<!-- agentops-stack:{record["id"]} -->' not in pr.get('body', '') or
                pr['base']['ref'] != record['target_branch']):
            raise ValueError('Existing PR does not match prepared stack')
        return pr

    @staticmethod
    def _receipt(pr):
        return {'state': 'published', 'pr_number': pr['number'], 'pr_url': pr['html_url'],
                'aggregate': {'state': pr['state'], 'draft': pr.get('draft'), 'merged': pr.get('merged', False)}}

    def publish(self, request):
        if self.config.get('publish') is not True:
            raise ValueError('Publication is disabled by panel configuration')
        with self.lock:
            self.recover()
            record = self._record(request)
            if record['state'] == 'published':
                return self.public(record)
            if not self.store.claim(record['id'], self.owner, ['prepared', 'publication_unknown'],
                                    state='publishing', exclusive=True):
                raise ValueError('Stack is not prepared for publication')
            attempted_write = False
            try:
                # Reconcile a lost response before freshness: the target may already
                # contain an aggregate merged by a human after a prior lost response.
                existing = self._existing_pr(record)
                if existing:
                    result = self._receipt(existing)
                else:
                    verified = self._fresh(record)
                    permissions = self.github.request('GET', f'repos/{self.github.repo}').get('permissions', {})
                    if permissions.get('push') is not True:
                        raise ValueError('Authenticated GitHub user needs repository write permission')
                    actor = self.github.authenticated_user()['login']
                    if not self._branch(record):
                        self.store.update(record['id'], {'phase': 'Publishing dedicated stack branch',
                                                        'publication_initiator': actor}, owner=self.owner)
                        attempted_write = True
                        self.engine.publish_branch(record)
                    if not self._branch(record):
                        raise ValueError('Published branch could not be verified')
                    self._fresh(record)
                    branch = 'agentops/stacks/' + record['id']
                    body = '\n'.join([
                        f'<!-- agentops-stack:{record["id"]} -->',
                        'Prepared PR stack for final human review and merge.', '',
                        f'Target: `{record["target_branch"]}` at `{record["target_sha"]}`',
                        f'Combined revision: `{record["revision"]}`', '', 'Sources (integration order):',
                        *[f'- #{p["number"]} by @{p["author"]}: `{p["head_sha"]}`' for p in record['members']], '',
                        'Local validation: ' + record['validation']['state'] + '.',
                        'GitHub CI and branch protection must pass for this combined revision.',
                        'A human merges on GitHub. Source PRs are never closed automatically; '
                        'check them after merging, especially if using squash or rebase.',
                    ])
                    attempted_write = True
                    pr = self.github.request('POST', f'repos/{self.github.repo}/pulls',
                                             {'title': ('PR stack: ' + ', '.join('#' + str(p['number']) for p in record['members']))[:250],
                                              'head': branch, 'base': record['target_branch'], 'body': body, 'draft': True})
                    result = {**self._receipt(pr), 'verified_at': verified, 'publication_initiator': actor}
                return self.public(self.store.update(record['id'], {**result, 'error': None,
                                                                     'phase': 'Human review and merge on GitHub'}, owner=self.owner))
            except Exception as error:
                self.store.update(record['id'], {'state': 'publication_unknown' if attempted_write or
                                                record['state'] == 'publication_unknown' else 'stale',
                                                'verified_at': 0, 'error': redact(str(error))}, owner=self.owner)
                raise

    def refresh(self, request):
        with self.lock:
            self.recover()
            record = self._record(request)
            previous = record['state']
            if not self.store.claim(record['id'], self.owner,
                                    ['prepared', 'stale', 'published', 'publication_unknown'],
                                    state='checking', exclusive=True):
                raise ValueError('Stack operation is active or preparation is incomplete')
            try:
                existing = self._existing_pr(record) if previous == 'publication_unknown' else None
                if record.get('pr_number') or existing:
                    pr = existing or self.github.request('GET', f'repos/{self.github.repo}/pulls/{record["pr_number"]}')
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        sources = list(pool.map(lambda p: self.github.request('GET', f'repos/{self.github.repo}/pulls/{p["number"]}'),
                                                record['members']))
                    result = {**self._receipt(pr), 'source_cleanup': [p['number'] for p in sources if p['state'] == 'open'],
                              'verified_at': time.time()}
                else:
                    result = {'state': 'prepared', 'verified_at': self._fresh(record)}
                return self.public(self.store.update(record['id'], {**result, 'error': None}, owner=self.owner))
            except Exception as error:
                self.store.update(record['id'], {'state': previous if previous in ('published', 'publication_unknown') else 'stale',
                                                'verified_at': 0, 'error': redact(str(error))}, owner=self.owner)
                raise
