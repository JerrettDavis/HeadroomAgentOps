"""GitHub CLI transport and complete paginated inventory. Read-only by default."""

import datetime
import base64
import hashlib
import json
import re
import subprocess
import time
import urllib.parse

from .model import digest
from .discussion import fingerprint


QUEUE = '''query AgentOpsQueue($owner:String!,$name:String!,$cursor:String,$size:Int!) {
 repository(owner:$owner,name:$name) { pullRequests(first:$size,after:$cursor,
 states:OPEN,orderBy:{field:CREATED_AT,direction:ASC}) {
 nodes { number title body url author { login } headRefOid baseRefOid baseRefName
 headRefName isDraft updatedAt reviewDecision mergeable mergeStateStatus
 headRepository { nameWithOwner } }
 totalCount pageInfo {hasNextPage endCursor} } } }'''
QUEUE_IDENTITIES = '''query AgentOpsQueueIdentities($owner:String!,$name:String!,
 $cursor:String,$size:Int!) { repository(owner:$owner,name:$name) {
 pullRequests(first:$size,after:$cursor,states:OPEN,orderBy:{field:CREATED_AT,direction:ASC}) {
 nodes {number headRefOid updatedAt} totalCount pageInfo {hasNextPage endCursor} } } }'''
THREADS = '''query AgentOpsThreads($owner:String!,$name:String!,$number:Int!,
 $cursor:String,$size:Int!) { repository(owner:$owner,name:$name) {
 pullRequest(number:$number) { reviewThreads(first:$size,after:$cursor) {
 nodes { id isResolved isOutdated path line comments(first:100) {
 nodes { id databaseId body url createdAt updatedAt author {login} }
 pageInfo {hasNextPage endCursor} } }
 pageInfo {hasNextPage endCursor} } } } }'''
THREAD_COMMENTS = '''query AgentOpsThreadComments($id:ID!,$cursor:String) {
 node(id:$id) { ... on PullRequestReviewThread { comments(first:100,after:$cursor) {
 nodes { id databaseId body url createdAt updatedAt author {login} }
 pageInfo {hasNextPage endCursor} } } } }'''


def read_pull(github,number):
    for attempt in range(3):
        try:
            return github.pull(number)
        except ValueError as exc:
            if str(exc)!='PR changed during detail collection; retry with fresh evidence' or attempt==2:
                raise


class RejectedWrite(RuntimeError):
    """GitHub returned a definite rejection; no ambiguous write is assumed."""


def gh_transport(method, endpoint, payload=None):
    command = ['gh', 'api', '--method', method, endpoint,
               '-H', 'Accept: application/vnd.github+json',
               '-H', 'X-GitHub-Api-Version: 2022-11-28']
    if payload is not None:
        command += ['--input', '-']
    readonly = method == 'GET' or (endpoint == 'graphql' and payload and
                                   payload.get('query', '').lstrip().startswith('query '))
    for attempt in range(3):
        result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, encoding='utf8', timeout=120,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if (not result.returncode or not readonly or attempt == 2 or
                not re.search(r'HTTP (500|502|503|504)|TLS handshake timeout', result.stderr, re.IGNORECASE)):
            break
        time.sleep(2 ** attempt)
    if result.returncode:
        status = re.search(r'HTTP (4\d\d)\b', result.stderr)
        if not readonly and status and int(status[1]) != 408:
            raise RejectedWrite(f'GitHub rejected {method} {endpoint.split("?")[0]}: {result.stderr.strip()}')
        raise RuntimeError(f'GitHub {method} {endpoint.split("?")[0]} failed: {result.stderr.strip()}')
    if not result.stdout.strip() and method != 'GET' and endpoint != 'graphql':
        return {}
    return json.loads(result.stdout)


class GitHub:
    def __init__(self, repo, runner=None, page_size=25):
        if not isinstance(repo, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('Repository must be owner/name')
        if not 1 <= page_size <= 100:
            raise ValueError('Invalid page size')
        self.repo = repo
        self.owner, self.name = repo.split('/')
        self.runner = runner or gh_transport
        self.page_size = page_size
        self._source_cache = {}
        self._actor = None

    def authenticated_user(self):
        if self._actor is None:
            self._actor = self.request('GET', 'user')
            if not isinstance(self._actor.get('login'), str) or not self._actor['login']:
                raise ValueError('Cannot establish authenticated maintainer identity')
        return self._actor

    def request(self, method, endpoint, payload=None):
        return self.runner(method, endpoint, payload)

    def main_sha(self):
        return self.request('GET', f'repos/{self.repo}/commits/main')['sha']

    def review_workflow_baseline(self,current,main_sha):
        compare=self.request('GET',f'repos/{self.repo}/compare/{main_sha}...{current["head_sha"]}')
        base=compare['merge_base_commit']['sha']
        cache=getattr(self,'_review_baseline_cache',{})
        def load(reference):
            if reference not in cache:
                data={**current,'head_sha':reference}
                self.workflow_qualification(data)
                events=any(re.search(r'\bpull_request_review(?:_comment)?\b',
                           self._source_cache[m['git_blob_sha']]) for m in data['workflow_manifest'])
                cache[reference]={'manifest_hash':data['workflow_manifest_hash'],'review_events':events}
            return cache[reference]
        baseline,main=load(base),load(main_sha)
        self._review_baseline_cache=cache
        head_events=any(re.search(r'\bpull_request_review(?:_comment)?\b',
                        self._source_cache[m['git_blob_sha']]) for m in current.get('workflow_manifest',[]))
        return {**baseline,'merge_base_sha':base,
                'review_events':baseline['review_events'] or main['review_events'] or head_events}

    def graphql(self, query, variables):
        result = self.request('POST', 'graphql', {'query': query, 'variables': variables})
        if result.get('errors') or not result.get('data'):
            raise ValueError(f'GraphQL returned incomplete data: {result.get("errors", "missing data")}')
        return result['data']

    @staticmethod
    def _connection(fetch):
        cursor = None
        seen = set()
        result = []
        while True:
            connection = fetch(cursor)
            if not isinstance(connection.get('nodes'), list) or not isinstance(connection.get('pageInfo'), dict):
                raise ValueError('Malformed GraphQL connection')
            result.extend(connection['nodes'])
            page = connection['pageInfo']
            if page.get('hasNextPage') is False:
                return result
            cursor = page.get('endCursor')
            if not cursor or cursor in seen:
                raise ValueError('Non-advancing GraphQL cursor')
            seen.add(cursor)

    def pages(self, endpoint, key=None):
        items = []
        fingerprints = set()
        for page in range(1, 10001):
            separator = '&' if '?' in endpoint else '?'
            data = self.request('GET', f'{endpoint}{separator}per_page={self.page_size}&page={page}')
            values = data.get(key) if key and isinstance(data, dict) else data
            if not isinstance(values, list):
                raise ValueError('Malformed REST page')
            fingerprint = digest(values)
            if values and fingerprint in fingerprints:
                raise ValueError('Non-advancing REST page')
            fingerprints.add(fingerprint)
            items.extend(values)
            if key and 'total_count' in data and len(items) >= data['total_count']:
                if len(items) != data['total_count']:
                    raise ValueError('REST count changed during pagination')
                return items
            if len(values) < self.page_size:
                if key and len(items) < data.get('total_count', len(items)):
                    raise ValueError('Incomplete REST pagination')
                return items
        raise ValueError('REST pagination exceeded safety bound')

    @staticmethod
    def _queue_pr(raw):
        return {'number': raw['number'], 'title': raw['title'], 'body': raw.get('body') or '',
                'url': raw['url'], 'author': (raw.get('author') or {}).get('login', 'ghost'),
                'head_sha': raw['headRefOid'], 'base_sha': raw['baseRefOid'],
                'base_ref': raw['baseRefName'], 'head_ref': raw['headRefName'],
                'head_repo': (raw.get('headRepository') or {}).get('nameWithOwner'),
                'draft': raw['isDraft'], 'updated_at': raw['updatedAt'],
                'review_decision': raw.get('reviewDecision'), 'mergeable': raw['mergeable'],
                'merge_state': raw['mergeStateStatus']}

    def snapshot(self):
        before = self.main_sha()
        counts = set()
        def fetch(cursor):
            data = self.graphql(QUEUE, {'owner': self.owner, 'name': self.name,
                                       'cursor': cursor, 'size': self.page_size})
            connection = data['repository']['pullRequests']
            counts.add(connection['totalCount'])
            return connection
        prs = [self._queue_pr(p) for p in self._connection(fetch)]
        if len({p['number'] for p in prs}) != len(prs):
            raise ValueError('Queue changed during pagination; duplicate PR')
        def identities(cursor):
            data = self.graphql(QUEUE_IDENTITIES, {'owner': self.owner, 'name': self.name,
                'cursor': cursor, 'size': self.page_size})
            connection = data['repository']['pullRequests']
            counts.add(connection['totalCount'])
            return connection
        confirmed = self._connection(identities)
        initial = {(p['number'], p['head_sha'], p['updated_at']) for p in prs}
        final = {(p['number'], p['headRefOid'], p['updatedAt']) for p in confirmed}
        if counts != {len(prs)} or len(confirmed) != len(prs) or initial != final:
            raise ValueError('Queue changed or was incomplete between inventory passes; collect again')
        after = self.main_sha()
        if before != after:
            raise ValueError('upstream main changed during inventory; collect a fresh snapshot')
        return {'repo': self.repo, 'main_sha': before, 'prs': prs, 'complete': True,
                'collected_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}

    def pull(self, number):
        if type(number) is not int or number <= 0:
            raise ValueError('PR number must be a positive integer')
        raw = self.request('GET', f'repos/{self.repo}/pulls/{number}')
        head = raw['head']
        base = raw['base']
        pr = {'number': number, 'title': raw['title'], 'body': raw.get('body') or '',
              'state': raw['state'], 'author': raw['user']['login'], 'author_id': raw['user'].get('id'),
              'draft': raw['draft'],
              'url': raw.get('html_url', f'https://github.com/{self.repo}/pull/{number}'),
              'head_sha': head['sha'], 'head_ref': head['ref'],
              'head_repo': (head.get('repo') or {}).get('full_name'),
              'base_sha': base['sha'], 'base_ref': base['ref'],
              'mergeable': {True: 'MERGEABLE', False: 'CONFLICTING', None: 'UNKNOWN'}[raw['mergeable']],
              'updated_at': raw['updated_at']}
        pr['files'] = self.pages(f'repos/{self.repo}/pulls/{number}/files')
        if len(pr['files']) != raw['changed_files']:
            raise ValueError('Incomplete changed files; GitHub may have hit its 3000-file cap')
        pr['comments'] = self.pages(f'repos/{self.repo}/issues/{number}/comments')
        pr['reviews'] = self.pages(f'repos/{self.repo}/pulls/{number}/reviews')
        pr['review_comments'] = self.pages(f'repos/{self.repo}/pulls/{number}/comments')
        def fetch(cursor):
            data = self.graphql(THREADS, {'owner': self.owner, 'name': self.name, 'number': number,
                                         'cursor': cursor, 'size': self.page_size})
            return data['repository']['pullRequest']['reviewThreads']
        pr['threads'] = self._connection(fetch)
        for thread in pr['threads']:
            initial = thread['comments']
            if initial['pageInfo']['hasNextPage']:
                first = True
                def fetch_comments(cursor):
                    nonlocal first
                    if first:
                        first = False
                        return initial
                    data = self.graphql(THREAD_COMMENTS, {'id': thread['id'], 'cursor': cursor})
                    return data['node']['comments']
                thread['comments'] = self._connection(fetch_comments)
            else:
                thread['comments'] = initial['nodes']
        pr['checks'] = self.pages(f'repos/{self.repo}/commits/{head["sha"]}/check-runs', 'check_runs')
        pr['statuses'] = self.pages(f'repos/{self.repo}/commits/{head["sha"]}/statuses')
        pr['workflows'] = []
        for repo in dict.fromkeys([self.repo, pr['head_repo']]):
            if repo:
                runs = self.pages(f'repos/{repo}/actions/runs?head_sha={head["sha"]}', 'workflow_runs')
                pr['workflows'].extend({**run, 'source_repo': repo} for run in runs)
        self.workflow_qualification(pr)
        # The detail fetch must not accidentally bind discussion from a different head.
        end = self.request('GET', f'repos/{self.repo}/pulls/{number}')
        if end['head']['sha'] != head['sha'] or end['updated_at'] != raw['updated_at']:
            raise ValueError('PR changed during detail collection; retry with fresh evidence')
        pr['discussion_hash'] = fingerprint(pr)
        pr['discussion_hash_version'] = 2
        pr['checks_hash'] = digest({key: pr[key] for key in ('checks', 'statuses', 'workflows')})
        return pr

    def workflow_qualification(self, pr):
        from .workflows import expected_workflows, screen_source
        reference = urllib.parse.quote(pr['head_sha'], safe='')
        files = self.request('GET', f'repos/{self.repo}/contents/.github/workflows?ref={reference}')
        if not isinstance(files, list):
            raise ValueError('Cannot inspect expected workflow source directory')
        sources, manifest = {}, []
        for item in files:
            path = item['path']
            if item['type'] != 'file' or not path.endswith(('.yml', '.yaml')):
                continue
            if not path.startswith('.github/workflows/') or '..' in path.split('/'):
                raise ValueError('Invalid workflow source path')
            identity = item['sha']
            if identity not in self._source_cache:
                blob = self.request('GET', f'repos/{self.repo}/contents/{path}?ref={reference}')
                if blob.get('encoding') != 'base64':
                    raise ValueError('Unsupported workflow source encoding')
                content = base64.b64decode(blob['content'])
                # Git blob IDs require SHA-1; changing the algorithm breaks identity checks.
                git_sha = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()  # nosec B324
                if git_sha != identity:
                    raise ValueError('Workflow source blob integrity mismatch')
                self._source_cache[identity] = content.decode('utf8')
            source = self._source_cache[identity]
            sources[path] = source
            manifest.append({'path': path, 'git_blob_sha': identity,
                             'sha256': hashlib.sha256(source.encode('utf8')).hexdigest(),
                             'execution_holds': screen_source(source)})
        expected, unknown = expected_workflows(sources, pr, self.repo)
        pr['workflow_manifest'] = manifest
        pr['workflow_manifest_hash'] = digest(manifest)
        pr['expected_workflows'] = expected
        pr['workflow_trigger_uncertainties'] = unknown
        pr['expected_workflows_verified'] = not unknown
