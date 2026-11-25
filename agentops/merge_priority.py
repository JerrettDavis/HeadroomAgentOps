"""Read-only, current GitHub merge suggestions, independent of review dispatch."""

from concurrent.futures import ThreadPoolExecutor
import copy
import re
import threading
import time


QUERY = '''query AgentOpsMergePriority($owner:String!,$name:String!,$cursor:String) {
 repository(owner:$owner,name:$name) {
 defaultBranchRef { name target { oid } }
 pullRequests(first:25,after:$cursor,states:OPEN,orderBy:{field:CREATED_AT,direction:ASC}) {
 totalCount pageInfo {hasNextPage endCursor}
 nodes { number title body author {login} authorAssociation isDraft
 headRefName baseRefName headRefOid baseRefOid baseRef {target {oid}} headRepository {nameWithOwner}
 mergeable mergeStateStatus reviewDecision
 labels(first:100) {nodes {name} pageInfo {hasNextPage}}
 commits(last:1) {nodes {commit {oid statusCheckRollup {state contexts {totalCount}}}}}
 } } } }'''


def declarations(body):
    # Examples in fenced code and quoted discussions are not declarations.
    lines, fence = [], None
    for line in (body or '').splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            run = marker[1]
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence):
                fence = None
            continue
        if fence is None and not line.startswith(('    ', '\t')) and not line.lstrip().startswith('>'):
            lines.append(line)
    return lines


def dependencies(repo, pr):
    found = set()
    for line in declarations(pr.get('body', '')):
        if not re.match(r'^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?(?:depends on|stacked on|requires)\b', line, re.I):
            continue
        urls = r'https://github\.com/([\w.-]+/[\w.-]+)/pull/([1-9]\d*)\b'
        for match in re.finditer(urls, line, re.I):
            found.add((match[1].lower(), int(match[2])))
        for number in re.findall(r'(?<![\w/])#([1-9]\d*)\b', re.sub(urls, '', line, flags=re.I)):
            found.add((repo.lower(), int(number)))
    return found


def category(pr):
    if pr.get('association') in ('OWNER', 'MEMBER', 'COLLABORATOR'):
        return 0, 'Maintainer', 'Author association: ' + pr['association'].lower()
    labels = {x.lower() for x in pr.get('labels', [])}
    title = pr.get('title', '')
    if labels & {'dependencies', 'dependency', 'deps'} or re.match(r'^(?:\w+\(deps(?:-dev)?\)|deps)\s*:', title, re.I):
        return 1, 'Dependencies', 'Dependency label or title'
    if labels & {'fixes-main-ci', 'fix-main-ci', 'main-ci-fix'} or any(
            re.match(r'^\s*(?:fixes|restores)\s+(?:main|default branch)\s+CI\b', line, re.I)
            for line in [title, *declarations(pr.get('body', ''))]):
        return 2, 'Main CI fix (declared)', 'Explicit main-CI repair label or declaration; verify in final review'
    if labels & {'bug', 'fix', 'bugfix'} or re.match(r'^fix(?:\([^)]*\))?!?:', title, re.I):
        return 3, 'Fix', 'Fix label or title'
    if labels & {'feature', 'enhancement', 'feat'} or re.match(r'^feat(?:\([^)]*\))?!?:', title, re.I):
        return 4, 'Feature', 'Feature label or title'
    return 5, 'Other', 'No priority category declared'


def rank_prs(repo, prs, default_branch='main', resolved=None):
    """Return a stable topological order, with indentation and explicit prerequisites."""
    resolved = resolved or {}
    rows = {}
    for pr in prs:
        number = pr['number']
        if number in rows:
            raise ValueError('Duplicate PR in inventory')
        rank, label, reason = category(pr)
        blockers = []
        if pr.get('draft') is not False:
            blockers.append('Draft' if pr.get('draft') else 'Draft status unknown')
        if pr.get('ci') != 'passed':
            blockers.append('CI ' + pr.get('ci', 'unknown'))
        if pr.get('up_to_date') is not True:
            blockers.append('Behind base branch' if pr.get('up_to_date') is False else 'Branch freshness unknown')
        if pr.get('mergeable') == 'CONFLICTING' or pr.get('merge_state') == 'DIRTY':
            blockers.append('Merge conflicts')
        elif pr.get('mergeable') != 'MERGEABLE':
            blockers.append('Mergeability unknown')
        if pr.get('review_decision') != 'APPROVED':
            blockers.append('Approval required')
        rows[number] = {**pr, 'repo': repo, 'url': f'https://github.com/{repo}/pull/{number}',
            'category_rank': rank, 'category': label, 'category_reason': reason,
            'blockers': blockers, 'dependencies': [], 'ready': False,
            'ready_after_dependencies': False, 'depth': 0, 'parent': None}
    edges = {n: set() for n in rows}
    for number, row in rows.items():
        refs = dependencies(repo, row)
        branch_parents = [n for n, p in rows.items() if n != number and
            p.get('head_repo', '').lower() == repo.lower() and
            p.get('head_ref') == row.get('base_ref')]
        if row.get('base_ref') != default_branch:
            declared_parents = [n for n in branch_parents if (repo.lower(), n) in refs]
            if len(branch_parents) == 1:
                refs.add((repo.lower(), branch_parents[0]))
            elif len(declared_parents) != 1 and len(branch_parents) > 1:
                row['blockers'].append('Ambiguous base branch: declare the prerequisite PR explicitly')
            elif not branch_parents:
                row['blockers'].append('Base is not the default branch')
            candidates = declared_parents or (branch_parents if len(branch_parents) == 1 else [])
            if candidates and row.get('base_sha') and rows[candidates[0]].get('head_sha') != row['base_sha']:
                row['blockers'].append('Prerequisite head changed since base observation')
        for dep_repo, dep_number in sorted(refs):
            local = dep_repo == repo.lower() and dep_number in rows
            state = 'open' if local else resolved.get((dep_repo, dep_number), 'unknown')
            row['dependencies'].append({'repo': dep_repo, 'number': dep_number,
                'url': f'https://github.com/{dep_repo}/pull/{dep_number}', 'state': state,
                'approved': rows[dep_number].get('review_decision') == 'APPROVED' if local else None})
            if local:
                edges[number].add(dep_number)
            elif state != 'merged':
                row['blockers'].append(f'Dependency {dep_repo}#{dep_number}: {state}')
    def key(n):
        row = rows[n]
        return (bool(row['blockers']), row.get('draft') is not False,
                row.get('ci') != 'passed', row.get('up_to_date') is not True,
                row['category_rank'], n)
    remaining, ordered = set(rows), []
    viable, positions = {}, {}
    while remaining:
        eligible = [n for n in remaining if not edges[n] & remaining]
        if not eligible:
            # Remaining nodes are cycles or depend on cycles. Keep every PR visible.
            for n in sorted(remaining, key=key):
                rows[n]['blockers'].append('Dependency cycle or prerequisite in a cycle')
                rows[n]['merge_order'] = None
                ordered.append(rows[n])
            break
        # Category wins over adjacency. Prerequisites still always come first.
        previous = ordered[-1]['number'] if ordered else None
        n = min(eligible, key=lambda n: (*key(n)[:-1], previous not in edges[n], n))
        row = rows[n]
        viable[n] = not row['blockers'] and all(viable.get(p, False) for p in edges[n])
        row['ready'] = viable[n] and not edges[n]
        row['ready_after_dependencies'] = viable[n] and bool(edges[n])
        if edges[n]:
            row['parent'] = max(edges[n], key=lambda p: positions[p])
            row['depth'] = rows[row['parent']]['depth'] + 1
            if not viable[n] and not row['blockers']:
                row['blockers'].append('Prerequisite is not ready')
        positions[n] = len(ordered)
        row['merge_order'] = len(ordered) + 1 if viable[n] else None
        ordered.append(row)
        remaining.remove(n)
    # Hidden prerequisites remain in the graph so their dependants stay blocked.
    ordered = [row for row in ordered if row.get('review_decision') == 'APPROVED'
               and row.get('ci') != 'failed' and row.get('mergeable') != 'CONFLICTING'
               and row.get('merge_state') != 'DIRTY']
    visible = {row['number'] for row in ordered}
    for row in ordered:
        if row['parent'] not in visible:
            row['parent'] = None
            row['depth'] = 0
        else:
            row['depth'] = rows[row['parent']]['depth'] + 1
    # Number suggestions consecutively, excluding blocked rows.
    count = 0
    for row in ordered:
        row.pop('body', None)
        if row['merge_order'] is not None:
            count += 1
            row['merge_order'] = count
    return ordered


def collect_priority(github, comparisons=None, *, full=False):
    """Collect complete open inventory and CI for immutable observed head commits."""
    comparisons = comparisons if comparisons is not None else {}
    comparison_lock = threading.Lock()
    cursor, seen_cursors, nodes, default, expected = None, set(), {}, None, None
    while True:
        data = github.graphql(QUERY, {'owner': github.owner, 'name': github.name, 'cursor': cursor})['repository']
        branch = data['defaultBranchRef']
        branch_identity = (branch['name'], branch['target']['oid'])
        page = data['pullRequests']
        if default is not None and (branch_identity != default or expected != page['totalCount']):
            raise ValueError('Repository changed during collection')
        default, expected = branch_identity, page['totalCount']
        for node in page['nodes']:
            if node['number'] in nodes:
                raise ValueError('Duplicate PR in inventory')
            if node['labels']['pageInfo']['hasNextPage']:
                raise ValueError('Incomplete PR labels')
            nodes[node['number']] = node
        info = page['pageInfo']
        if not info['hasNextPage']:
            break
        cursor = info['endCursor']
        if not cursor or cursor in seen_cursors:
            raise ValueError('Incomplete PR pagination')
        seen_cursors.add(cursor)
    if len(nodes) != expected:
        raise ValueError('Incomplete PR inventory')

    def normalize(node):
        commits = node.get('commits', {}).get('nodes', [])
        commit = commits[-1]['commit'] if commits else {}
        rollup = commit.get('statusCheckRollup') or {}
        ci = {'SUCCESS': 'passed', 'FAILURE': 'failed', 'ERROR': 'failed',
              'PENDING': 'pending', 'EXPECTED': 'pending'}.get(rollup.get('state'), 'unknown')
        if commit.get('oid') != node['headRefOid'] or not rollup.get('contexts', {}).get('totalCount'):
            ci = 'unknown'
        # baseRefOid is the PR's historical base, not necessarily today's branch
        # tip. Use the actual target of the live ref (or the captured default).
        base_sha = default[1] if node['baseRefName'] == default[0] else (
            (node.get('baseRef') or {}).get('target') or {}).get('oid')
        current = None
        if node.get('mergeStateStatus') == 'BEHIND':
            current = False
        elif (base_sha and ci == 'passed' and node.get('isDraft') is False
              and node.get('reviewDecision') == 'APPROVED'):
            identity = (github.repo.lower(), base_sha, node['headRefOid'])
            try:
                with comparison_lock:
                    current = comparisons.get(identity)
                if current is None:
                    result = github.request('GET', f'repos/{github.repo}/compare/{base_sha}...{node["headRefOid"]}')
                    current = result['merge_base_commit']['sha'] == base_sha
                    with comparison_lock:
                        comparisons[identity] = current
            except Exception:
                current = None
        return {'number': node['number'], 'title': node['title'], 'body': node.get('body', ''),
            'author': (node.get('author') or {}).get('login', 'Deleted user'),
            'association': node.get('authorAssociation'),
            'labels': [label['name'] for label in node['labels']['nodes']],
            'head_ref': node['headRefName'], 'base_ref': node['baseRefName'],
            'head_repo': (node.get('headRepository') or {}).get('nameWithOwner', ''),
            'head_sha': node['headRefOid'], 'base_sha': base_sha,
            'draft': node.get('isDraft'), 'mergeable': node.get('mergeable'),
            'merge_state': node.get('mergeStateStatus'), 'review_decision': node.get('reviewDecision'),
            'ci': ci, 'up_to_date': current}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='priority-read') as pool:
        prs = list(pool.map(normalize, nodes.values()))
    while len(comparisons) > 2048:
        del comparisons[next(iter(comparisons))]
    resolved = {}
    for dep_repo, number in sorted(set().union(*(dependencies(github.repo, pr) for pr in prs))):
        if dep_repo == github.repo.lower() and number in nodes:
            continue
        try:
            dep = github.request('GET', f'repos/{dep_repo}/pulls/{number}')
            resolved[(dep_repo, number)] = 'merged' if dep.get('merged') is True else dep.get('state', 'unknown')
        except Exception:
            resolved[(dep_repo, number)] = 'unknown'
    return {'repo': github.repo, 'default_branch': default[0], 'default_sha': default[1],
            'prs': prs if full else rank_prs(github.repo, prs, default[0], resolved),
            **({'resolved_dependencies': [{'repo': repo, 'number': number, 'state': state}
                                         for (repo, number), state in sorted(resolved.items())]} if full else {})}


class PriorityMonitor:
    """Single-flight background collection; stale observations never imply ready."""

    def __init__(self, reader, interval=60, max_age=180, clock=time.time):
        self.reader, self.interval, self.clock = reader, interval, clock
        self.max_age = max_age
        self.lock = threading.Lock()
        self.worker = None
        self.cached = None
        self.observed = None
        self.attempted = None
        self.error = None

    def _refresh(self, started):
        try:
            result = self.reader()
            with self.lock:
                self.cached, self.observed, self.error = result, started, None
        except Exception:
            with self.lock:
                self.error = 'GitHub refresh failed. Check GitHub sign-in and repository access; retrying automatically.'

    def status(self):
        with self.lock:
            now = self.clock()
            refreshing = bool(self.worker and self.worker.is_alive())
            if not refreshing and (self.attempted is None or now - self.attempted >= self.interval):
                self.attempted = now
                self.worker = threading.Thread(target=self._refresh, args=(now,), daemon=True,
                                               name='merge-priority')
                self.worker.start()
                refreshing = True
            result = copy.deepcopy(self.cached or {'prs': []})
            fresh = self.observed is not None and now - self.observed < self.max_age and not self.error
            result.update(fresh=fresh, observed_at=self.observed, attempted_at=self.attempted,
                          refreshing=refreshing, error=self.error, refresh_seconds=self.interval,
                          freshness_seconds=self.max_age)
        if not fresh:
            for row in result['prs']:
                row['ready'] = row['ready_after_dependencies'] = False
                row['merge_order'] = None
                row['blockers'].append('Observation expired; waiting for GitHub refresh')
        return result

    def wait(self, timeout):
        """Join the current refresh for shutdown or deterministic verification."""
        worker = self.worker
        if worker:
            worker.join(timeout)
