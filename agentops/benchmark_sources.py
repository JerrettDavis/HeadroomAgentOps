"""Bounded, cached read-only GitHub source discovery for Benchmark Studio."""
from __future__ import annotations

import copy
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlencode


def repository(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value) or any(p in {'.','..'} for p in value.split('/')):
        raise ValueError('Use a repository in owner/name form')
    return value


def page_number(value):
    if type(value) is not int or not 1 <= value <= 20:
        raise ValueError('Search page must be between 1 and 20')
    return value


class Sources:
    def __init__(self, github, ttl=60, clock=time.monotonic):
        self.github, self.ttl, self.clock = github, ttl, clock
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def _get(self, endpoint):
        with self.lock:
            cached = self.cache.get(endpoint)
            if cached and self.clock() - cached[0] < self.ttl:
                self.cache.move_to_end(endpoint)
                return copy.deepcopy(cached[1])
        data = self.github.request('GET', endpoint)
        with self.lock:
            self.cache[endpoint] = (self.clock(), copy.deepcopy(data))
            self.cache.move_to_end(endpoint)
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
        return data

    def search(self, repo, query='', page=1, kind='commits'):
        repo, page = repository(repo), page_number(page)
        if not isinstance(query, str) or len(query) > 120 or any(ord(c) < 32 for c in query):
            raise ValueError('Search text must be at most 120 printable characters')
        if re.search(r'\b(?:repo|org|user)\s*:', query, re.I):
            raise ValueError('Change the Repository field to change search scope')
        if kind not in {'commits','prs'}:
            raise ValueError('Choose commit or pull request search')
        query = query.strip()
        params = {'per_page': 20, 'page': page}
        if kind == 'prs':
            raw = self._get('search/issues?' + urlencode(dict(params, q=f'repo:{repo} is:pr {query}')))
            items = [{'number': p['number'], 'subject': p['title'], 'author': (p.get('user') or {}).get('login',''),
                      'date': p.get('updated_at',''), 'state': p.get('state','')} for p in raw['items']
                     if not p.get('repository_url') or p['repository_url'].lower().endswith('/repos/' + repo.lower())]
            total = raw.get('total_count', 0)
        else:
            pr = re.fullmatch(r'(?:#|pr[: ]?)([1-9][0-9]{0,8})', query, re.I)
            if pr:
                raw = self._get(f'repos/{repo}/pulls/{pr[1]}/commits?' + urlencode(params))
                total = None
            elif query:
                response = self._get('search/commits?' + urlencode(dict(params, q=f'repo:{repo} {query}')))
                raw, total = response['items'], response.get('total_count',0)
            else:
                raw = self._get(f'repos/{repo}/commits?' + urlencode(params))
                total = None
            items = []
            for item in raw:
                actual_repo = (item.get('repository') or {}).get('full_name')
                if actual_repo and actual_repo.lower() != repo.lower():
                    continue
                sha = item.get('sha','')
                if not re.fullmatch('[0-9a-f]{40}', sha):
                    continue
                commit, author = item.get('commit',{}), item.get('author') or {}
                identity = commit.get('author') or {}
                items.append({'sha': sha, 'subject': commit.get('message','').split('\n',1)[0][:300],
                              'author': author.get('login') or identity.get('name',''),
                              'date': identity.get('date','')})
        return {'repository': repo, 'kind': kind, 'items': items, 'page': page,
                'has_more': page < 20 and (len(items) == 20 if total is None else total > page * 20),
                'total': total, 'cache_seconds': self.ttl}

    def artifacts(self, repo, sha, page=1):
        repo, page = repository(repo), page_number(page)
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('Select an immutable full commit SHA first')
        raw = self._get(f'repos/{repo}/actions/artifacts?' + urlencode({'per_page':20, 'page':page}))
        items = []
        for artifact in raw['artifacts']:
            run = artifact.get('workflow_run') or {}
            if run.get('head_sha') != sha:
                continue
            items.append({key: artifact.get(key) for key in ('id','name','size_in_bytes','expired','created_at','expires_at')}
                         | {'sha': sha, 'run_id': run.get('id'), 'repository': repo})
        return {'items': items, 'repository': repo, 'sha': sha, 'page': page,
                'has_more': page < 20 and raw.get('total_count',0) > page * 20,
                'notice': 'Showing matches from this page of repository artifacts; continue to search older artifacts.'}
