"""Explicit preview receipts and local coordinator dispatch for the panel."""

import argparse
import copy
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
        config = {**self.config, 'publish': False}
        for key in ('harness', 'model', 'concurrency', 'publish'):
            if key in request:
                config[key] = request[key]
        if config['harness'] not in ('codex', 'claude', 'copilot'):
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
                                   str(Path.home() / '.claude') if config['harness'] == 'claude' else None)
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
        snapshot = self.github.snapshot()
        if snapshot.get('complete') is not True or snapshot.get('repo') != config['repo']:
            raise ValueError('Complete repository inventory required')
        selected = [p for p in snapshot['prs'] if numbers is None or p['number'] in numbers]
        if numbers is not None and numbers - {p['number'] for p in selected}:
            raise ValueError('Some requested PRs are no longer open; preview again')
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
            snapshot = self.github.snapshot()
            current = {p['number']: p['head_sha'] for p in snapshot['prs']}
            if any(current.get(n) != expected[n] for n in selected):
                raise ValueError('A selected PR changed or closed; preview again')
            config = receipt['config']
            result = make_plan(config, argparse.Namespace(snapshot=None, pr=selected, limit=None),
                               self.ledger, type('Inventory', (), {'snapshot': lambda _: snapshot})(),
                               Evidence(self.root / 'evidence'))
            run_id = result['run_id']
            logdir = self.root / 'launches'
            logdir.mkdir(parents=True, exist_ok=True)
            logfile = logdir / f'{run_id}.log'
            # Persist the receipt before spawn so a retry can never dispatch twice.
            receipt['launched'] = {'run_id': run_id, 'selected': result['selected'], 'log': str(logfile)}
            command = [sys.executable, '-m', 'agentops', '--state', str(self.root),
                       'run', '--plan', run_id, '--force-fresh']
            try:
                with logfile.open('ab') as output:
                    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                               start_new_session=os.name != 'nt',
                                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                self.processes[run_id] = process
            except OSError:
                receipt['launched']['error'] = 'Coordinator could not start. Inspect the launch log and use the CLI to run this saved plan.'
            return receipt['launched']

    def status(self):
        with self.lock:
            return {run: {'exit_code': process.poll(), 'running': process.poll() is None}
                    for run, process in self.processes.items()}
