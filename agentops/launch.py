"""Explicit preview receipts and local coordinator dispatch for the panel."""

import argparse
import copy
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from .cli import make_plan
from .evidence import Evidence
from .github import GitHub
from .ledger import Ledger
from .provider_usage import default_auth_root
from .publisher import Publisher
from .reconcile import reconcile_jobs
from .requalification import requalify_inherited_review
from .process import owner_state


def parse_prs(text, repo):
    numbers = []
    for value in re.split(r'[\s,]+', text.strip()):
        match = re.fullmatch(r'https://github.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)/?', value)
        if match:
            if match[1].lower() != repo.lower():
                raise ValueError('PR URL belongs to a different repository')
            value = match[2]
        else:
            value = value.removeprefix('#')
        if not re.fullmatch(r'[1-9][0-9]*', value):
            raise ValueError('Enter PR numbers or GitHub pull request URLs, separated by spaces or commas')
        number = int(value)
        if number not in numbers:
            numbers.append(number)
    return numbers


class Launcher:
    def __init__(self, path, config, github=None):
        self.root = Path(path).resolve().parent
        self.config = {**config, 'state': str(self.root)}
        self.github = github or GitHub(config['repo'])
        self.previews = {}
        self.processes = {}
        self.lock = threading.Lock()

    @property
    def ledger(self):
        return Ledger(self.root / 'ledger.sqlite3')

    def settings(self, request):
        config = dict(self.config)
        for key in ('harness', 'model', 'concurrency', 'publish'):
            if key in request:
                config[key] = request[key]
        if config['harness'] not in ('codex', 'claude', 'copilot', 'devin'):
            raise ValueError('Unknown review harness')
        if type(config['concurrency']) is not int or not 1 <= config['concurrency'] <= 4:
            raise ValueError('Concurrency must be an integer from 1 to 4')
        if type(config['publish']) is not bool:
            raise ValueError('Publication must be true or false')
        if config['model'] is not None and (not isinstance(config['model'], str) or len(config['model']) > 200):
            raise ValueError('Invalid model')
        if config['harness'] != self.config['harness']:
            # Let the existing CLI select the appropriate default credential directory.
            config['auth_root'] = (os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))
                                   if config['harness'] == 'codex' else
                                   str(Path.home() / '.claude') if config['harness'] == 'claude' else
                                   str(default_auth_root('devin')) if config['harness'] == 'devin' else None)
        return config

    def query_numbers(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise ValueError('Enter a GitHub PR search query')
        numbers = set()
        total = None
        for page in range(1, 11):
            endpoint = 'search/issues?' + urlencode({'q': f'{query} repo:{self.config["repo"]} is:pr is:open',
                                                      'per_page': 100, 'page': page})
            result = self.github.request('GET', endpoint)
            if result.get('incomplete_results') is not False or result['total_count'] > 1000:
                raise ValueError('Search is incomplete or exceeds 1,000 results; narrow the query')
            if total is not None and total != result['total_count']:
                raise ValueError('Search changed during pagination; preview again')
            total = result['total_count']
            for item in result['items']:
                if item.get('repository_url', '').lower() != f'https://api.github.com/repos/{self.config["repo"]}'.lower():
                    raise ValueError('Search returned a PR from another repository; remove repository qualifiers')
                numbers.add(item['number'])
            if len(numbers) == total:
                return numbers
            if not result['items']:
                break
        raise ValueError('Search pagination was incomplete; preview again')

    def preview(self, request):
        config = self.settings(request)
        if type(request.get('non_draft_only', False)) is not bool:
            raise ValueError('Non-draft filter must be true or false')
        config['non_draft_only'] = request.get('non_draft_only', False)
        if type(request.get('require_decision', False)) is not bool:
            raise ValueError('Decision requirement must be true or false')
        config['require_decision'] = request.get('require_decision', False)
        source = request.get('source')
        if source in ('single', 'batch'):
            numbers = set(parse_prs(request.get('prs', ''), config['repo']))
            if source == 'single' and len(numbers) != 1:
                raise ValueError('Select exactly one PR')
        elif source == 'query':
            numbers = self.query_numbers(request.get('query'))
        elif source == 'all':
            numbers = None
        else:
            raise ValueError('Choose a review source')
        snapshot = self.github.snapshot(queue_only=True, numbers=numbers)
        if snapshot.get('complete') is not True or snapshot.get('repo') != config['repo']:
            raise ValueError('Complete repository inventory required')
        selected = [p for p in snapshot['prs'] if numbers is None or p['number'] in numbers]
        if numbers is not None and numbers - {p['number'] for p in selected}:
            raise ValueError('Some requested PRs are no longer open; preview again')
        excluded = {a.lower() for a in config.get('exclude_authors', [])}
        if numbers is not None and any(p['author'].lower() in excluded for p in selected):
            raise ValueError('PRs by excluded authors are assigned to other maintainers')
        selected = [p for p in selected if p['author'].lower() not in excluded]
        if config['non_draft_only']:
            selected = [p for p in selected if p.get('draft') is False]
        if not selected:
            raise ValueError('No open pull requests match this selection')
        identity = secrets.token_urlsafe(24)
        receipt = {'config': config, 'prs': copy.deepcopy(selected), 'created': time.time()}
        with self.lock:
            self.previews = {k: v for k, v in self.previews.items() if time.time() - v['created'] < 1800}
            if len(self.previews) >= 32:
                raise ValueError('Too many previews; wait for an earlier preview to expire')
            self.previews[identity] = receipt
        return {'preview_id': identity, 'repo': config['repo'],
                'settings': {k: config[k] for k in ('harness', 'model', 'concurrency', 'publish')},
                'prs': [{k: p.get(k) for k in ('number', 'title', 'author', 'head_sha')} for p in selected]}

    def launch(self, request):
        with self.lock:
            receipt = self.previews.get(request.get('preview_id'))
            if not receipt or time.time() - receipt['created'] >= 1800:
                raise ValueError('Preview expired; preview the selection again')
            if 'launched' in receipt:
                return receipt['launched']
            selected = request.get('selected')
            if not isinstance(selected, list) or not selected or any(type(n) is not int for n in selected):
                raise ValueError('Select at least one PR from the preview')
            expected = {p['number']: p['head_sha'] for p in receipt['prs']}
            if not set(selected) <= expected.keys():
                raise ValueError('Selection is outside the preview')
            snapshot = self.github.snapshot(queue_only=True, numbers=selected)
            if snapshot.get('complete') is not True or snapshot.get('repo') != self.config['repo']:
                raise ValueError('Complete repository inventory required')
            current = {p['number']: p['head_sha'] for p in snapshot['prs']}
            if any(current.get(n) != expected[n] for n in selected):
                raise ValueError('A selected PR changed or closed; preview again')
            config = receipt['config']
            if config.get('non_draft_only') and any(
                    p['number'] in selected and p.get('draft') is not False for p in snapshot['prs']):
                raise ValueError('A selected PR is now a draft; preview again')
            result = make_plan(config, argparse.Namespace(snapshot=None, pr=selected, limit=None),
                               self.ledger, type('Inventory', (), {'snapshot': lambda _: snapshot})(),
                               Evidence(self.root / 'evidence'))
            run_id = result['run_id']
            logdir = self.root / 'launches'
            logdir.mkdir(parents=True, exist_ok=True)
            logfile = logdir / f'{run_id}.log'
            # Persist the receipt before spawn so a retry can never dispatch twice.
            receipt['launched'] = {'run_id': run_id, 'selected': result['selected'], 'log': str(logfile)}
            self._spawn(receipt['launched'])
            return receipt['launched']

    def _spawn(self, result):
        command = [sys.executable, '-m', 'agentops', '--state', str(self.root),
                   'run', '--plan', result['run_id'], '--force-fresh']
        try:
            with Path(result['log']).open('ab') as output:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                           start_new_session=os.name != 'nt',
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes[result['run_id']] = process
            result.pop('error', None)
        except OSError:
            result['error'] = 'Coordinator could not start. The saved plan remains queued.'
        return result

    def retry_launch(self, result):
        """Retry only a definitive spawn failure, using the existing immutable plan."""
        with self.lock:
            if result['run_id'] in self.processes:
                return {k: v for k, v in result.items() if k != 'error'}
            run = self.ledger.get_run(result['run_id'])
            jobs = self.ledger.jobs(result['run_id'])
            snapshot = self.github.snapshot(queue_only=True, numbers=[j['number'] for j in jobs])
            if snapshot.get('complete') is not True or snapshot.get('repo') != self.config['repo']:
                raise ValueError('Complete repository inventory required before retrying a launch')
            current = {p['number']: p for p in snapshot['prs']}
            if any(j['number'] not in current or current[j['number']]['head_sha'] != j['head_sha']
                   or (run['config'].get('non_draft_only') and current[j['number']].get('draft') is not False)
                   for j in jobs):
                self.ledger.stop(result['run_id'])
                return {**result, 'error': None, 'cancelled': True}
            return self._spawn(dict(result))

    def refresh_reviews(self, snapshot, non_draft_only=True):
        """Recheck current saved reviews after CI changes, without new reasoning."""
        report = {'errors': [], 'resume_runs': []}
        if snapshot.get('complete') is not True or snapshot.get('repo') != self.config['repo']:
            raise ValueError('Complete repository inventory required for review refresh')
        if not self.config.get('publish'):
            return report
        current = {pr['number']: pr for pr in snapshot['prs']}
        excluded = {a.lower() for a in self.config.get('exclude_authors', [])}
        latest = {}
        jobs = self.ledger.jobs()
        for job in jobs:
            if job['repo'] == self.config['repo']:
                latest[job['number']] = job
        # A dead coordinator can leave completed workers occupying every global
        # slot. Resume its saved assignments rather than clone a review plan.
        # The coordinator verifies owner, worker state and current revisions.
        orphan_runs = {j['run_id'] for j in jobs if j['repo'] == self.config['repo']
                       and j['state'] == 'running'
                       and owner_state(j['owner']) == 'missing'}
        with self.lock:
            for run_id in orphan_runs:
                process = self.processes.get(run_id)
                if process is not None and process.poll() is None:
                    continue
                run = self.ledger.get_run(run_id)
                if run['stopped'] or not run['config'].get('publish'):
                    continue
                run_excluded = excluded | {a.lower() for a in run['config'].get('exclude_authors', [])}
                run_jobs = self.ledger.jobs(run_id)
                if any(current.get(j['number'], {}).get('author', '').lower() in run_excluded
                       for j in run_jobs if j['state'] == 'running'):
                    continue
                for queued in (j for j in run_jobs if j['state'] == 'queued' and not j['stale']):
                    pr = current.get(queued['number'])
                    if (not pr or pr['head_sha'] != queued['head_sha'] or
                            snapshot['main_sha'] != queued['main_sha'] or
                            latest.get(queued['number'], {}).get('id') != queued['id'] or
                            pr['author'].lower() in run_excluded or
                            ((non_draft_only or run['config'].get('non_draft_only')) and pr.get('draft') is not False)):
                        self.ledger.cancel_queued(queued['id'], 'Eligibility changed before orphan recovery')
                logdir = self.root / 'launches'
                logdir.mkdir(parents=True, exist_ok=True)
                recovered = self._spawn({'run_id': run_id, 'selected': [], 'log': str(logdir / f'{run_id}.log')})
                if recovered.get('error'):
                    report['errors'].append({'run_id': run_id, 'error': recovered['error']})
        selected = []
        for number, job in latest.items():
            pr = current.get(number)
            if (not pr or pr['head_sha'] != job['head_sha'] or job['main_sha'] != snapshot['main_sha']
                    or job['stale'] or pr['author'].lower() in excluded
                    or pr.get('review_decision') == 'APPROVED'
                    or (non_draft_only and pr.get('draft') is not False)):
                continue
            run = self.ledger.get_run(job['run_id'])
            if run['stopped'] or not run['config'].get('publish') or not run['config'].get('require_decision'):
                continue
            if job['state'] == 'queued':
                report['resume_runs'].append(job['run_id'])
            elif (job['state'] in ('held', 'awaiting_ci', 'needs_followup')
                    and (job.get('result') or {}).get('outcome') in ('ready', 'awaiting_ci')):
                selected.append(job)
        if not selected:
            return report
        evidence = Evidence(self.root / 'evidence')
        publisher = Publisher(self.github, self.ledger)
        # Persist attempts even when a legacy record cannot be reconciled. A
        # skipped/erroring record must not permanently occupy the limited batch.
        setting = 'review_refresh_attempts:' + self.config['repo']
        with self.ledger.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (setting,)).fetchone()
        attempted = json.loads(row[0]) if row else {}
        current_ids = {j['id'] for j in latest.values()}
        attempted = {key: value for key, value in attempted.items()
                     if key in current_ids and type(value) in (int, float)}
        selected.sort(key=lambda j: (attempted.get(j['id'], 0), j['created']))
        selected = selected[:4]
        attempted.update({job['id']: time.time() for job in selected})
        with self.ledger.connect(immediate=True) as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       (setting, json.dumps(attempted)))
        refreshed = reconcile_jobs(self.ledger, self.github, evidence, retry_changed=True,
                                   publisher=publisher, job_ids={j['id'] for j in selected})
        report['errors'].extend(refreshed['errors'])
        for job in selected:
            try:
                requalify_inherited_review(self.ledger, self.github, evidence, publisher, job['id'])
                if self.ledger.job(job['id'])['state'] == 'queued':
                    report['resume_runs'].append(job['run_id'])
            except (ValueError, RuntimeError, OSError, KeyError) as error:
                report['errors'].append({'number': job['number'], 'error': str(error)})
        report['resume_runs'] = sorted(set(report['resume_runs']))
        return report

    def resume_reviews(self, run_ids, non_draft_only=True):
        """Resume previously authorized queued follow-ups; never enqueue a new job."""
        pending = []
        with self.lock:
            for run_id in set(run_ids):
                process = self.processes.get(run_id)
                if process is not None and process.poll() is None:
                    continue
                run = self.ledger.get_run(run_id)
                if (run['stopped'] or not self.config.get('publish') or not run['config'].get('publish')
                        or not run['config'].get('require_decision')):
                    continue
                run_jobs = self.ledger.jobs(run_id)
                if any(j['state'] == 'running' and owner_state(j['owner']) != 'missing' for j in run_jobs):
                    # Panel restarts lose Popen handles, not coordinator ownership.
                    # Do not re-inventory a live run or start a competing coordinator.
                    continue
                jobs = [j for j in run_jobs if j['state'] == 'queued' and not j['stale']]
                if not jobs:
                    continue
                try:
                    snapshot = self.github.snapshot(queue_only=True, numbers=[j['number'] for j in jobs])
                    if snapshot.get('complete') is not True or snapshot.get('repo') != self.config['repo']:
                        raise ValueError('Complete repository inventory required before resuming reviews')
                except (ValueError, RuntimeError, OSError):
                    pending.append(run_id)
                    continue
                current = {pr['number']: pr for pr in snapshot['prs']}
                latest = {j['number']: j['id'] for j in self.ledger.jobs() if j['repo'] == self.config['repo']}
                excluded = {a.lower() for a in self.config.get('exclude_authors', []) +
                            run['config'].get('exclude_authors', [])}
                for job in jobs:
                    pr = current.get(job['number'])
                    if (not pr or job['repo'] != self.config['repo'] or latest.get(job['number']) != job['id']
                            or pr['head_sha'] != job['head_sha']
                            or snapshot['main_sha'] != job['main_sha'] or pr['author'].lower() in excluded
                            or ((non_draft_only or run['config'].get('non_draft_only')) and pr.get('draft') is not False)):
                        self.ledger.cancel_queued(job['id'], 'Review eligibility changed before resuming')
                jobs = [j for j in jobs if self.ledger.job(j['id'])['state'] == 'queued']
                if not jobs:
                    continue
                logdir = self.root / 'launches'
                logdir.mkdir(parents=True, exist_ok=True)
                result = self._spawn({'run_id': run_id, 'selected': [j['number'] for j in jobs],
                                      'log': str(logdir / f'{run_id}.log')})
                if result.get('error'):
                    pending.append(run_id)
        return pending

    def status(self):
        with self.lock:
            return {run: {'exit_code': process.poll(), 'running': process.poll() is None}
                    for run, process in self.processes.items()}

    def resume_saved_runs(self, handoff_path=None):
        """Consume an authorized idle handoff before automatic scheduling starts."""
        from .reload import consume
        return consume(self, handoff_path)
