"""Local, read-only reporting over a consistent ledger snapshot."""

import json
import hashlib
import math
import re
import sqlite3
import time
from collections import Counter
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .redaction import redact


def decode(value):
    return json.loads(value) if value else {}


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def normalize_usage(raw):
    if 'modelMetrics' not in raw:
        return raw
    usage = {}
    aliases = {'inputTokens': 'input_tokens', 'outputTokens': 'output_tokens',
               'cacheReadTokens': 'cached_input_tokens', 'cacheWriteTokens': 'cache_creation_input_tokens',
               'reasoningTokens': 'reasoning_output_tokens'}
    for model in raw['modelMetrics'].values():
        for source, target in aliases.items():
            value = number(model.get('usage', {}).get(source))
            if value is not None:
                usage[target] = usage.get(target, 0) + value
    credits = number(raw.get('totalNanoAiu'))
    if credits is not None:
        usage['ai_credits'] = credits / 1_000_000_000
    return usage


def check_status(pr):
    latest = {}
    for kind, rows in (('check', pr.get('checks') or []), ('status', pr.get('statuses') or [])):
        for row in rows:
            key = (kind, row.get('context', row.get('name')), (row.get('app') or {}).get('id'))
            if key not in latest or row.get('id', 0) > latest[key].get('id', 0):
                latest[key] = row
    conclusions = [r.get('conclusion') or r.get('state') or r.get('status') for r in latest.values()]
    if not conclusions:
        return 'unknown'
    if any(c in ('failure', 'failed', 'error', 'timed_out', 'cancelled', 'action_required', 'startup_failure') for c in conclusions):
        return 'failed'
    if any(c in (None, 'pending', 'in_progress', 'queued', 'waiting', 'requested') for c in conclusions):
        return 'pending'
    if all(c in ('skipped', 'neutral') for c in conclusions):
        return 'skipped'
    return 'passed' if all(c in ('success', 'skipped', 'neutral') for c in conclusions) else 'unknown'


def report(path, run_id=None):
    """Never initialize/migrate the ledger, contact GitHub, or inspect auth files."""
    path = Path(path).resolve()
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        runs = [dict(r) for r in db.execute(
            'SELECT r.id,r.created,r.stopped,s.repo,(SELECT count(*) FROM jobs j WHERE j.run_id=r.id) AS total FROM runs r '
            'JOIN snapshots s ON s.id=r.snapshot_id ORDER BY r.created DESC')]
        if run_id and not any(r['id'] == run_id for r in runs):
            raise KeyError('Unknown run')
        selected = run_id or (runs[0]['id'] if runs else None)
        jobs = [dict(r) for r in db.execute('SELECT * FROM jobs WHERE run_id=? ORDER BY created', (selected,))]
        attempts = [dict(r) for r in db.execute(
            'SELECT a.* FROM job_attempts a JOIN jobs j ON j.id=a.job_id WHERE j.run_id=?', (selected,))]
        actions = [dict(r) for r in db.execute(
            'SELECT a.* FROM actions a JOIN jobs j ON j.id=a.job_id WHERE j.run_id=? ORDER BY a.updated DESC', (selected,))]
        snapshot_row = db.execute('SELECT s.data FROM snapshots s JOIN runs r ON r.snapshot_id=s.id WHERE r.id=?', (selected,)).fetchone()
        snapshot = decode(snapshot_row[0]) if snapshot_row else {}
        config_row = db.execute('SELECT config FROM runs WHERE id=?', (selected,)).fetchone()
        config = decode(config_row[0]) if config_row else {}
    finally:
        db.close()
    now = time.time()
    usage = {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0,
             'cache_creation_input_tokens': 0, 'reasoning_output_tokens': 0,
             'cost_usd': 0, 'reported_attempts': 0, 'cost_reported_attempts': 0,
             'ai_credits': 0, 'credits_reported_attempts': 0,
             'unknown_attempts': 0, 'reused_attempts': 0}
    # Terminal attempts are durable even after retries. The current result is a
    # fallback only: reclassification/reuse must not double count model spend.
    executions = {(a['job_id'], a['attempt']): decode(a['result']).get('execution', {}) for a in attempts}
    for job in jobs:
        if job['attempt']:
            executions.setdefault((job['id'], job['attempt']),
                                  {} if job['state'] in ('running', 'queued') else decode(job['result']).get('execution', {}))
    for execution in executions.values():
        if execution.get('reused'):
            usage['reused_attempts'] += 1
            continue
        measured = execution.get('usage')
        if not isinstance(measured, dict) or not measured:
            usage['unknown_attempts'] += 1
            continue
        measured = normalize_usage(measured)
        if not any(number(measured.get(key)) is not None for key in ('input_tokens', 'output_tokens')):
            usage['unknown_attempts'] += 1
            continue
        usage['reported_attempts'] += 1
        for key in ('input_tokens', 'output_tokens', 'cached_input_tokens',
                    'cache_creation_input_tokens', 'reasoning_output_tokens'):
            # Claude uses cache_read_input_tokens; do not count both aliases.
            value = measured.get(key, measured.get('cache_read_input_tokens') if key == 'cached_input_tokens' else None)
            usage[key] += number(value) or 0
        cost = number(measured.get('cost_usd'))
        if cost is not None:
            usage['cost_usd'] += cost
            usage['cost_reported_attempts'] += 1
        credits = number(measured.get('ai_credits'))
        if credits is not None:
            usage['ai_credits'] += credits
            usage['credits_reported_attempts'] += 1
    public_jobs = []
    prs = {p['number']: p for p in snapshot.get('prs', [])}
    for job in jobs:
        result = {} if job['state'] in ('running', 'queued') else decode(job['result'])
        public_jobs.append({key: job[key] for key in
                            ('id', 'number', 'repo', 'head_sha', 'main_sha', 'state', 'stale', 'attempt', 'created', 'heartbeat')})
        public_jobs[-1].update(summary=result.get('summary', result.get('reason', result.get('error', ''))),
                               result=result, heartbeat_age_seconds=round(now - job['heartbeat']) if job['heartbeat'] else None)
        pr = prs.get(job['number'], {})
        handle = decode(job['handle'])
        public_jobs[-1].update(pr=pr, session_id=handle.get('session_id'),
                               agent=config.get('harness'), model=config.get('model'),
                               started_at=handle.get('started_at'),
                               execution=result.get('execution', {}))
        public_jobs[-1]['usage'] = normalize_usage(result.get('execution', {}).get('usage') or {})
        public_jobs[-1]['attempts'] = [
            {'attempt': a['attempt'], 'state': a['state'], 'finished': a['finished'],
             'session_id': decode(a['handle']).get('session_id'),
             'execution': decode(a['result']).get('execution', {}),
             'usage': normalize_usage(decode(a['result']).get('execution', {}).get('usage') or {})}
            for a in attempts if a['job_id'] == job['id']]
        observed = saved_pr(path.parent, handle.get('session_id'))
        public_jobs[-1]['observed'] = {k: observed.get(k) for k in
                                     ('head_sha', 'checks', 'statuses', 'workflows', 'mergeable', 'merge_state', 'review_decision')}
        public_jobs[-1]['ci'] = check_status(observed) if observed.get('head_sha') == job['head_sha'] else 'unknown'
    public_actions = []
    by_id = {j['id']: j for j in jobs}
    for action in actions:
        data, receipt = decode(action['data']), decode(action['receipt'])
        if data.get('kind') not in ('comment', 'review'):
            continue
        job = by_id[action['job_id']]
        public_actions.append({'id': action['id'], 'number': job['number'], 'repo': job['repo'],
                               'state': action['state'], 'kind': data['kind'], 'body': data.get('body', ''),
                               'updated': action['updated'], 'url': receipt.get('html_url'),
                               'reasons': receipt.get('reasons', [])})
    states = dict(Counter(j['state'] for j in jobs))
    return {'generated': now, 'collected_at': snapshot.get('collected_at'), 'runs': runs, 'selected_run': selected, 'jobs': public_jobs,
            'actions': public_actions, 'usage': usage, 'states': states, 'total': len(jobs),
            'finished': sum(j['state'] not in ('queued', 'running') for j in jobs),
            'stale': sum(bool(j['stale']) for j in jobs)}


def artifact(root, identity):
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
        raise ValueError('Invalid evidence hash')
    target = root / 'evidence' / (identity + '.json')
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Evidence outside state directory')
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != identity:
        raise ValueError('Evidence hash mismatch')
    return json.loads(raw)


def saved_pr(root, session):
    if not isinstance(session, str) or not re.fullmatch('[a-f0-9-]{36}', session):
        return {}
    target = root / 'jobs' / session / 'input' / 'packet.json'
    try:
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            return {}
        packet = json.loads(target.read_text(encoding='utf8'))
        record = artifact(root, packet['evidence']['sha256'])
        return record.get('pr', record)
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def detail(path, job_id, diffs=False):
    path = Path(path).resolve()
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    finally:
        db.close()
    if not row:
        raise KeyError('Unknown job')
    job = dict(row)
    pr = saved_pr(path.parent, decode(job['handle']).get('session_id'))
    result = {} if job['state'] in ('running', 'queued') else decode(job['result'])
    response = {'pr': pr, 'result': result, 'files': pr.get('files', [])}
    if diffs:
        try:
            response['candidate_diff'] = candidate_diff(path.parent, result, job)
        except Exception:
            response['diff_error'] = 'Candidate diff unavailable. The exported bundle may require an unavailable base revision.'
        session = decode(job['handle']).get('session_id')
        if isinstance(session, str) and re.fullmatch('[a-f0-9-]{36}', session):
            patch = path.parent / 'jobs' / session / 'output' / 'uncommitted.patch'
            if patch.is_file() and not patch.is_symlink() and patch.resolve().is_relative_to(path.parent):
                response['uncommitted_diff'] = patch.read_text(encoding='utf8', errors='replace')
    return response


def candidate_diff(root, result, job):
    archive = result.get('candidate_archive')
    if not archive:
        return None
    return archived_diff(str(root), archive['sha256'], job['head_sha'], job['repo'])


@lru_cache(maxsize=32)
def archived_diff(root, identity, expected_head, repository):
    import base64
    import os
    import subprocess
    import tempfile
    record = artifact(Path(root), identity)
    if record.get('head_sha') != expected_head:
        raise ValueError('Candidate head mismatch')
    head, candidate = record['head_sha'], record['candidate_sha']
    if not all(re.fullmatch('[a-f0-9]{40}', s) for s in (head, candidate)):
        raise ValueError('Invalid candidate revision')
    # Import objects only into a disposable bare repository; never check out or
    # execute candidate code, filters, textconv, hooks, or external diff drivers.
    with tempfile.TemporaryDirectory(prefix='agentops-diff-') as directory:
        bundle = Path(directory) / 'candidate.bundle'
        bundle.write_bytes(base64.b64decode(record['bundle_base64'], validate=True))
        repo = Path(directory) / 'objects.git'
        def git(*args):
            env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
            completed = subprocess.run(['git', '-c', f'core.hooksPath={os.devnull}', *args],
                                       capture_output=True, timeout=60, check=True, env=env)
            return completed.stdout.decode('utf8', errors='replace')
        git('init', '--bare', str(repo))
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid repository')
        # Incremental bundles omit the base commit. Read it from GitHub only
        # when the user opens diffs; all imported objects stay disposable.
        git('-C', str(repo), 'fetch', '--depth=1', '--no-tags',
            f'https://github.com/{repository}.git', head)
        git('-C', str(repo), 'fetch', '--no-tags', str(bundle), 'refs/heads/agentops-candidate')
        return git('-C', str(repo), 'diff', '--no-ext-diff', '--no-textconv', '--no-color', head, candidate)


def make_server(path, host='127.0.0.1', port=8765, config=None):
    if host not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('The panel binds to loopback only')
    page = Path(__file__).with_name('panel.html')
    import secrets
    from .launch import Launcher
    from .cli import DEFAULTS
    launcher = Launcher(path, config or DEFAULTS)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            origin = self.headers.get('Origin')
            host = self.headers.get('Host', '')
            try:
                valid_host = urlsplit('//' + host).hostname in ('127.0.0.1', 'localhost', '::1')
            except ValueError:
                valid_host = False
            if (not valid_host or origin != 'http://' + host or
                    not secrets.compare_digest(self.headers.get('X-Launch-Token', ''), token)):
                self.send_error(403, 'Same-origin launch token required')
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    raise ValueError('Invalid request size')
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('JSON object required')
                if self.path == '/api/preview':
                    result = launcher.preview(payload)
                elif self.path == '/api/launch':
                    result = launcher.launch(payload)
                else:
                    self.send_error(404)
                    return
                status = 200
            except (ValueError, TypeError, KeyError) as error:
                status, result = 400, {'error': str(error)}
            except Exception:
                status, result = 503, {'error': 'Review launch unavailable. Check GitHub authentication and the panel configuration, then preview again.'}
            body = redact(json.dumps(result)).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            try:
                allowed = urlsplit('//' + self.headers.get('Host', '')).hostname in ('127.0.0.1', 'localhost', '::1')
            except ValueError:
                allowed = False
            if not allowed:
                self.send_error(403, 'Loopback host required')
                return
            url = urlsplit(self.path)
            status, content_type = 200, 'application/json; charset=utf-8'
            if url.path == '/':
                body, content_type = page.read_bytes(), 'text/html; charset=utf-8'
            elif url.path == '/api/launcher':
                body = json.dumps({'token': token, 'repo': launcher.config['repo'],
                                   'harness': launcher.config['harness'],
                                   'model': launcher.config['model'],
                                   'concurrency': launcher.config['concurrency'],
                                   'processes': launcher.status()}).encode()
            elif url.path == '/api/report':
                try:
                    body = redact(json.dumps(report(path, parse_qs(url.query).get('run', [None])[0]), ensure_ascii=False)).encode()
                except KeyError:
                    status, body = 404, b'{"error":"Unknown run"}'
                except (sqlite3.Error, ValueError, OSError):
                    status, body = 503, b'{"error":"Ledger unavailable. Check the state directory and ledger schema, then refresh."}'
            elif url.path == '/api/job':
                try:
                    query = parse_qs(url.query)
                    body = redact(json.dumps(detail(path, query.get('id', [''])[0], query.get('diffs') == ['1']), ensure_ascii=False)).encode()
                except KeyError:
                    status, body = 404, b'{"error":"Unknown job"}'
                except Exception:
                    status, body = 503, b'{"error":"Saved PR evidence or candidate diff unavailable"}'
            else:
                status, body = 404, b'{"error":"Not found"}'
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    # IPv4 is deliberate; localhost resolves to the same loopback listener.
    if host == '::1':
        import socket
        class IPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        return IPv6Server((host, port), Handler)
    return ThreadingHTTPServer((host, port), Handler)


def serve(path, host='127.0.0.1', port=8765, config=None):
    with make_server(path, host, port, config) as server:
        print(f'Reviewer panel: http://{host}:{server.server_port} (Ctrl+C to stop)', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
