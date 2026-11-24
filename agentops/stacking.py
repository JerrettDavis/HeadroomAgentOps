"""Deterministic dependency planning and transactional stack records. No agents."""
import heapq
import json
import re
import time

from .ledger import Ledger
from .merge_priority import dependencies
from .model import digest


def plan_stack(inventory, numbers):
    """Resolve prerequisite closure and pin a stable topological integration order."""
    if (not isinstance(numbers, list) or not 1 <= len(numbers) <= 100 or
            any(type(n) is not int or n < 1 for n in numbers)):
        raise ValueError('Select between 1 and 100 PR numbers')
    repo, base = inventory['repo'], inventory['default_branch']
    prs = {p['number']: p for p in inventory['prs']}
    if len(prs) != len(inventory['prs']):
        raise ValueError('Duplicate PR in inventory')
    selected = set(numbers)
    if selected - prs.keys():
        raise ValueError('Selected PR is no longer open')
    resolved = {(d['repo'].lower(), d['number']): d['state']
                for d in inventory.get('resolved_dependencies', [])}
    parents = {}
    for p in prs.values():
        if p.get('head_repo', '').lower() == repo.lower():
            parents.setdefault(p['head_ref'], []).append(p['number'])
    blockers, edges, pending = [], {}, list(sorted(selected, reverse=True))
    while pending:
        n = pending.pop()
        if n in edges:
            continue
        if len(edges) >= 100:
            raise ValueError('Stack including prerequisites exceeds 100 PRs')
        p = prs[n]
        refs = dependencies(repo, p)
        if p.get('base_ref') != base:
            candidates = [v for v in parents.get(p.get('base_ref'), []) if v != n]
            declared = [v for v in candidates if (repo.lower(), v) in refs]
            parent = declared[0] if len(declared) == 1 else candidates[0] if len(candidates) == 1 else None
            if parent is None:
                blockers.append(f'#{n}: base branch has no unambiguous prerequisite')
            else:
                refs.add((repo.lower(), parent))
                if p.get('base_sha') != prs[parent].get('head_sha'):
                    blockers.append(f'#{n}: prerequisite head differs from observed base')
        edges[n] = set()
        for dep_repo, dep in sorted(refs):
            if dep_repo == repo.lower() and dep in prs:
                edges[n].add(dep)
                pending.append(dep)
            elif resolved.get((dep_repo, dep)) != 'merged':
                blockers.append(f'#{n}: prerequisite {dep_repo}#{dep} is not verified merged or selectable')
        gates = [(p.get('draft') is False, 'draft status must be non-draft'),
                 (p.get('ci') == 'passed', 'source CI must pass'),
                 (p.get('review_decision') == 'APPROVED', 'source approval required'),
                 (p.get('up_to_date') is True, 'branch freshness is not verified'),
                 (p.get('mergeable') == 'MERGEABLE' and p.get('merge_state') != 'DIRTY',
                  'source mergeability is not verified')]
        for valid, reason in gates:
            if not valid:
                blockers.append(f'#{n}: {reason}')
        if not re.fullmatch('[a-f0-9]{40}', p.get('head_sha', '')):
            blockers.append(f'#{n}: invalid head revision')
    if not re.fullmatch('[a-f0-9]{40}', inventory.get('default_sha', '')):
        raise ValueError('Invalid target revision')
    children, indegree = {}, {n: len(deps) for n, deps in edges.items()}
    for n, deps in edges.items():
        for parent in deps:
            children.setdefault(parent, []).append(n)
    queue = [n for n, count in indegree.items() if count == 0]
    heapq.heapify(queue)
    ordered = []
    while queue:
        n = heapq.heappop(queue)
        ordered.append(n)
        for child in children.get(n, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                heapq.heappush(queue, child)
    if len(ordered) != len(edges):
        blockers.append('Dependency cycle in selected stack')
        ordered.extend(sorted(edges.keys() - set(ordered)))
    members = [{**prs[n], 'prerequisites': sorted(edges[n])} for n in ordered]
    identity = {'repo': repo.lower(), 'target_branch': base,
                'target_sha': inventory['default_sha'],
                'members': [{'number': p['number'], 'head_sha': p['head_sha'],
                             'base_ref': p['base_ref'], 'base_sha': p.get('base_sha'),
                             'prerequisites': p['prerequisites']} for p in members]}
    return {**identity, 'id': digest(identity), 'members': members,
            'selected': sorted(selected), 'included_prerequisites': sorted(edges.keys() - selected),
            'blockers': blockers, 'state': 'blocked' if blockers else 'planned'}


class StackStore:
    """Separate records in the existing ledger; transitions use compare-and-set."""
    def __init__(self, path):
        self.ledger = Ledger(path)
        with self.ledger.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS merge_stacks ('
                       'id TEXT PRIMARY KEY, state TEXT NOT NULL, owner TEXT, data TEXT NOT NULL, '
                       'created REAL NOT NULL, updated REAL NOT NULL)')

    @staticmethod
    def _decode(row):
        if row is None:
            raise ValueError('Stack not found')
        return {**json.loads(row['data']), **{k: row[k] for k in ('id', 'state', 'owner', 'created', 'updated')}}

    def create(self, plan):
        now = time.time()
        with self.ledger.connect(immediate=True) as db:
            db.execute('INSERT OR IGNORE INTO merge_stacks VALUES (?,?,NULL,?,?,?)',
                       (plan['id'], plan['state'], json.dumps(plan), now, now))
        return self.get(plan['id'])

    def get(self, identity):
        with self.ledger.connect() as db:
            return self._decode(db.execute('SELECT * FROM merge_stacks WHERE id=?', (identity,)).fetchone())

    def list(self):
        with self.ledger.connect() as db:
            return [self._decode(row) for row in db.execute('SELECT * FROM merge_stacks ORDER BY created DESC LIMIT 100')]

    def active(self):
        with self.ledger.connect() as db:
            return [self._decode(row) for row in db.execute(
                "SELECT * FROM merge_stacks WHERE state IN ('preparing','publishing','checking')")]

    def begin_preparation(self, plan, owner):
        """Apply fresh planning evidence and acquire ownership in one transaction."""
        now = time.time()
        with self.ledger.connect(immediate=True) as db:
            row = db.execute('SELECT * FROM merge_stacks WHERE id=?', (plan['id'],)).fetchone()
            record = self._decode(row) if row else None
            if record and record['state'] in ('preparing', 'publishing', 'checking'):
                raise ValueError('Another stack operation is active')
            if record and record['state'] in ('published', 'publication_unknown'):
                return record, False
            if record and record['state'] == 'prepared' and not plan['blockers']:
                record['verified_at'] = now
                db.execute('UPDATE merge_stacks SET data=?,updated=? WHERE id=?',
                           (json.dumps(record), now, plan['id']))
                return {**record, 'updated': now}, False
            if db.execute("SELECT 1 FROM merge_stacks WHERE state IN "
                          "('preparing','publishing','checking') LIMIT 1").fetchone():
                raise ValueError('Another stack operation is active')
            record = {**(record or {}), **plan, 'state': 'blocked' if plan['blockers'] else 'preparing',
                      'owner': owner, 'verified_at': now, 'error': None,
                      'created': record['created'] if record else now, 'updated': now}
            db.execute('INSERT INTO merge_stacks VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
                       'state=excluded.state,owner=excluded.owner,data=excluded.data,updated=excluded.updated',
                       (record['id'], record['state'], owner, json.dumps(record), record['created'], now))
            return record, record['state'] == 'preparing'

    def claim(self, identity, owner, states, state='preparing', exclusive=False):
        with self.ledger.connect(immediate=True) as db:
            if exclusive and db.execute("SELECT 1 FROM merge_stacks WHERE state IN "
                                        "('preparing','publishing','checking') LIMIT 1").fetchone():
                raise ValueError('Another stack operation is active')
            marks = ','.join('?' for _ in states)
            return db.execute(f'UPDATE merge_stacks SET state=?,owner=?,updated=? WHERE id=? AND state IN ({marks})',
                              (state, owner, time.time(), identity, *states)).rowcount == 1

    def update(self, identity, changes, owner=None):
        with self.ledger.connect(immediate=True) as db:
            record = self._decode(db.execute('SELECT * FROM merge_stacks WHERE id=?', (identity,)).fetchone())
            if owner is not None and record['owner'] != owner:
                raise ValueError('Stack operation belongs to another owner')
            record.update(changes)
            db.execute('UPDATE merge_stacks SET state=?,owner=?,data=?,updated=? WHERE id=?',
                       (record['state'], record.get('owner'), json.dumps(record), time.time(), identity))
        return self.get(identity)
