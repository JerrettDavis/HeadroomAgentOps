"""Transactional coordination; no network or agent invocation occurs here."""

import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path

from .model import TERMINAL, canonical, digest


SCHEMA = '''
CREATE TABLE IF NOT EXISTS snapshots (
 id TEXT PRIMARY KEY, repo TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
 config TEXT NOT NULL, stopped INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), repo TEXT NOT NULL,
 number INTEGER NOT NULL, head_sha TEXT NOT NULL, main_sha TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', stale INTEGER NOT NULL DEFAULT 0,
 owner TEXT, handle TEXT, attempt INTEGER NOT NULL DEFAULT 0,
 heartbeat REAL, result TEXT, created REAL NOT NULL,
 UNIQUE(run_id,number,head_sha,main_sha));
CREATE INDEX IF NOT EXISTS job_claim ON jobs(state,created);
CREATE INDEX IF NOT EXISTS job_revision ON jobs(repo,number,head_sha,main_sha);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notices (
 id TEXT PRIMARY KEY, repo TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
 provenance TEXT NOT NULL, numbers TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
 resolution TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
 notice_id TEXT REFERENCES notices(id), session TEXT NOT NULL, created REAL NOT NULL,
 PRIMARY KEY(notice_id,session));
CREATE TABLE IF NOT EXISTS actions (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), data TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'prepared', receipt TEXT, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS job_attempts (
 job_id TEXT NOT NULL REFERENCES jobs(id), attempt INTEGER NOT NULL,
 owner TEXT, handle TEXT, state TEXT NOT NULL, result TEXT, finished REAL NOT NULL,
 PRIMARY KEY(job_id,attempt));
CREATE TABLE IF NOT EXISTS job_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
 kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
'''


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError(f'Unsupported ledger schema {version}')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)
            # Preserve the latest known evidence when migrating pre-history ledgers.
            db.execute("INSERT OR IGNORE INTO job_attempts SELECT id,attempt,owner,handle,state,result,"
                       "coalesce(heartbeat,created) FROM jobs WHERE attempt>0 AND state NOT IN ('running','queued')")
            db.execute('PRAGMA user_version=2')

    @contextlib.contextmanager
    def connect(self, immediate=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if immediate:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def save_snapshot(self, snapshot):
        identity = digest(snapshot)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?)',
                       (identity, snapshot['repo'], canonical(snapshot), time.time()))
        return identity

    def get_snapshot(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT data FROM snapshots WHERE id=?', (identity,)).fetchone()
            if not row:
                raise KeyError(identity)
            return json.loads(row[0])

    def create_run(self, snapshot_id, config):
        identity = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO runs(id,snapshot_id,config,created) VALUES(?,?,?,?)',
                       (identity, snapshot_id, canonical(config), time.time()))
        return identity

    def get_run(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        result = dict(row)
        result['config'] = json.loads(result['config'])
        return result

    def enqueue(self, run_id, pr, main_sha):
        with self.connect(immediate=True) as db:
            snapshot = db.execute('SELECT s.repo FROM snapshots s JOIN runs r '
                                  'ON r.snapshot_id=s.id WHERE r.id=?', (run_id,)).fetchone()
            if not snapshot:
                raise KeyError(run_id)
            identity = uuid.uuid4().hex
            db.execute('INSERT OR IGNORE INTO jobs '
                       '(id,run_id,repo,number,head_sha,main_sha,created) VALUES(?,?,?,?,?,?,?)',
                       (identity, run_id, snapshot[0], pr['number'], pr['head_sha'],
                        main_sha, time.time()))
            return db.execute('SELECT id FROM jobs WHERE run_id=? AND number=? '
                              'AND head_sha=? AND main_sha=?',
                              (run_id, pr['number'], pr['head_sha'], main_sha)).fetchone()[0]

    def claim(self, owner, limit, run_id=None):
        if not 1 <= limit <= 4:
            raise ValueError('Concurrency must be between 1 and 4')
        with self.connect(immediate=True) as db:
            current = db.execute("SELECT value FROM settings WHERE key='capacity'").fetchone()
            # The smallest declared cap wins while any work is live.
            active = db.execute("SELECT count(*) FROM jobs WHERE state='running'").fetchone()[0]
            capacity = min(limit, int(current[0])) if current and active else limit
            db.execute("INSERT OR REPLACE INTO settings VALUES('capacity',?)", (str(capacity),))
            if active >= capacity:
                return None
            query = '''SELECT j.* FROM jobs j JOIN runs r ON r.id=j.run_id
                WHERE j.state='queued' AND j.stale=0 AND r.stopped=0
                AND NOT EXISTS (SELECT 1 FROM jobs live WHERE live.state='running'
                    AND live.repo=j.repo AND live.number=j.number
                    AND live.head_sha=j.head_sha AND live.main_sha=j.main_sha)'''
            args = ()
            if run_id:
                query += ' AND j.run_id=?'
                args = (run_id,)
            row = db.execute(query + ' ORDER BY j.created,j.id LIMIT 1', args).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET state='running',owner=?,attempt=attempt+1,"
                       'heartbeat=?,handle=NULL WHERE id=?', (owner, time.time(), row['id']))
            return self._decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())

    @staticmethod
    def _decode(row):
        if row is None:
            raise KeyError('Unknown job')
        value = dict(row)
        for field in ('handle', 'result'):
            if value[field] is not None:
                value[field] = json.loads(value[field])
        return value

    def job(self, identity):
        with self.connect() as db:
            return self._decode(db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone())

    def jobs(self, run_id=None):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM jobs' + (' WHERE run_id=?' if run_id else '') +
                              ' ORDER BY created,id', (run_id,) if run_id else ()).fetchall()
            return [self._decode(row) for row in rows]

    def prior_reviews(self, job):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE repo=? AND number=? AND head_sha=? AND main_sha=? "
                "AND id<>? AND stale=0 AND state IN ('ready','held','reviewed','awaiting_ci','needs_followup') ORDER BY created DESC",
                (job['repo'], job['number'], job['head_sha'], job['main_sha'], job['id']))
            return [self._decode(row) for row in rows]

    def context_review(self, job):
        """Latest completed analysis, including an older head, for bounded follow-up context."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE repo=? AND number=? AND id<>? "
                             "AND state IN ('ready','held','reviewed','awaiting_ci','needs_followup') "
                             "AND result IS NOT NULL ORDER BY created DESC,id DESC LIMIT 1",
                             (job['repo'], job['number'], job['id'])).fetchone()
            return self._decode(row) if row else None

    def set_review_task(self, identity, task, shared_manifest=None,ci_evidence=None):
        if task != 'qualification':
            raise ValueError('Unknown bounded review task')
        context = {'review_task':task}
        if shared_manifest:
            context['shared_provenance_hash'] = shared_manifest
        if ci_evidence:
            context['ci_failure_evidence']=ci_evidence
        with self.connect(immediate=True) as db:
            changed = db.execute("UPDATE jobs SET result=? WHERE id=? AND state='queued'",
                                 (canonical(context), identity)).rowcount
            if changed != 1:
                raise ValueError('Review task can only be set before a job is claimed')

    def split_pending_run(self, run_id, numbers):
        source = self.get_run(run_id)
        identity = uuid.uuid4().hex
        wanted = set(numbers)
        with self.connect(immediate=True) as db:
            rows = db.execute('SELECT id,number,state FROM jobs WHERE run_id=?',(run_id,)).fetchall()
            selected = [r for r in rows if r['number'] in wanted]
            if len(selected) != len(wanted) or any(r['state'] != 'queued' for r in selected):
                raise ValueError('Only unclaimed queued jobs can be partitioned')
            db.execute('INSERT INTO runs(id,snapshot_id,config,created) VALUES(?,?,?,?)',
                       (identity,source['snapshot_id'],canonical(source['config']),time.time()))
            for row in selected:
                db.execute('UPDATE jobs SET run_id=? WHERE id=?',(identity,row['id']))
        return identity

    def workflow_audit(self, repo, manifest):
        with self.connect() as db:
            row=db.execute('SELECT value FROM settings WHERE key=?',
                           ('workflow-audit:'+repo+':'+manifest,)).fetchone()
            return json.loads(row['value']) if row else None

    def record_workflow_audit(self, repo, manifest, reference):
        with self.connect(immediate=True) as db:
            db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                       ('workflow-audit:'+repo+':'+manifest,canonical(reference)))

    def attach_handle(self, identity, owner, handle):
        with self.connect() as db:
            count = db.execute("UPDATE jobs SET handle=?,heartbeat=? WHERE id=? AND owner=? "
                               "AND state='running'", (canonical(handle), time.time(), identity, owner)).rowcount
            if count != 1:
                raise ValueError('Job is not owned and running')

    def heartbeat(self, identity, owner):
        with self.connect() as db:
            db.execute("UPDATE jobs SET heartbeat=? WHERE id=? AND owner=? AND state='running'",
                       (time.time(), identity, owner))

    def finish(self, identity, owner, state, result):
        if state not in TERMINAL:
            raise ValueError(f'Invalid terminal state {state}')
        with self.connect(immediate=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
            if not row or row['owner'] != owner or row['state'] != 'running':
                raise ValueError('Job is not owned and running')
            if row['stale'] and state in ('ready', 'reviewed'):
                raise ValueError('Stale job cannot be qualified')
            now = time.time()
            db.execute('INSERT OR REPLACE INTO job_attempts VALUES(?,?,?,?,?,?,?)',
                       (identity, row['attempt'], owner, row['handle'], state, canonical(result), now))
            db.execute('UPDATE jobs SET state=?,result=?,heartbeat=?,stale=? WHERE id=?',
                       (state, canonical(result), now, int(bool(row['stale']) or state == 'stale'), identity))

    def attempts(self, identity):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM job_attempts WHERE job_id=? ORDER BY attempt', (identity,))
            result = []
            for row in rows:
                item = dict(row)
                item['result'] = json.loads(item['result']) if item['result'] else None
                item['handle'] = json.loads(item['handle']) if item['handle'] else None
                result.append(item)
            return result

    def reclassify(self, identity, state, result):
        if state not in TERMINAL:
            raise ValueError('Reclassification requires a terminal recorded outcome')
        with self.connect(immediate=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
            if not row or row['state'] not in TERMINAL:
                raise ValueError('Cannot reclassify a claimed or queued job')
            if row['stale'] and state in ('ready', 'reviewed'):
                raise ValueError('Stale evidence cannot be requalified')
            db.execute('INSERT INTO job_events(job_id,kind,data,created) VALUES(?,?,?,?)',
                       (identity, 'reclassified', canonical({'from': row['state'], 'to': state, 'result': result}), time.time()))
            db.execute('UPDATE jobs SET state=?,result=?,stale=? WHERE id=?',
                       (state, canonical(result), int(bool(row['stale']) or state == 'stale'), identity))

    def invalidate(self, repo, main_sha, heads, scope=None):
        with self.connect(immediate=True) as db:
            for row in db.execute('SELECT id,number,head_sha,main_sha,state FROM jobs WHERE repo=?', (repo,)):
                observed = scope is None or row['number'] in scope
                if row['main_sha'] != main_sha or (observed and heads.get(row['number']) != row['head_sha']):
                    state = 'running' if row['state'] == 'running' else 'stale'
                    db.execute('UPDATE jobs SET stale=1,state=? WHERE id=?', (state, row['id']))

    def recover(self, identity, observe):
        original = self.job(identity)
        if original['state'] != 'running':
            return False
        # The observer must also check coordinator ownership when no worker handle exists.
        state = observe(original['handle'] or {'owner': original['owner']})
        if state not in ('missing', 'exited'):
            return False
        with self.connect(immediate=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
            if (row['state'] != 'running' or row['owner'] != original['owner'] or
                    row['handle'] != (canonical(original['handle']) if original['handle'] else None) or
                    row['heartbeat'] != original['heartbeat']):
                return False
            db.execute("UPDATE jobs SET state='interrupted',heartbeat=? WHERE id=?", (time.time(), identity))
        return True

    def adopt(self, identity, owner, observe_owner):
        original = self.job(identity)
        if original['state'] != 'running' or observe_owner(original['owner']) != 'missing':
            return False
        with self.connect(immediate=True) as db:
            count = db.execute("UPDATE jobs SET owner=?,heartbeat=? WHERE id=? AND owner=? "
                               "AND state='running' AND heartbeat=?",
                               (owner, time.time(), identity, original['owner'], original['heartbeat'])).rowcount
            return count == 1

    def retry(self, identity, max_attempts=3):
        if not 1 <= max_attempts <= 3:
            raise ValueError('Session budget must be between 1 and 3')
        with self.connect(immediate=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
            if not row or row['state'] not in TERMINAL or row['stale'] or row['attempt'] >= max_attempts:
                raise ValueError('Job cannot be retried within this revision budget')
            db.execute("UPDATE jobs SET state='queued',owner=NULL,handle=NULL WHERE id=?", (identity,))

    def notice(self, repo, kind, body, provenance, numbers=None):
        if not provenance or kind not in ('baseline', 'security', 'policy', 'learning', 'suggestion'):
            raise ValueError('Notice requires a recognized kind and provenance')
        data = [repo, kind, body, provenance, sorted(numbers or [])]
        identity = digest(data)
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO notices(id,repo,kind,body,provenance,numbers,created) '
                       'VALUES(?,?,?,?,?,?,?)', (identity, repo, kind, body, canonical(provenance),
                                                canonical(sorted(numbers or [])), time.time()))
        return identity

    def notices(self, repo, number):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM notices WHERE repo=? AND status='active' ORDER BY created", (repo,))
            result = []
            for row in rows:
                item = dict(row)
                item['numbers'] = json.loads(item['numbers'])
                item['provenance'] = json.loads(item['provenance'])
                if not item['numbers'] or number in item['numbers']:
                    result.append(item)
            return result

    def ack_notice(self, identity, session):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO deliveries VALUES(?,?,?)', (identity, session, time.time()))

    def supersede_notice(self, identity, reason, provenance):
        if not reason or not provenance:
            raise ValueError('Resolution needs provenance')
        with self.connect() as db:
            db.execute("UPDATE notices SET status='superseded',resolution=? WHERE id=?",
                       (canonical({'reason': reason, 'provenance': provenance}), identity))

    def intent(self, job_id, action):
        with self.connect() as db:
            job = db.execute('SELECT repo,number,head_sha,main_sha FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job:
                raise KeyError(job_id)
            identity = digest([dict(job), action])
            db.execute('INSERT OR IGNORE INTO actions(id,job_id,data,updated) VALUES(?,?,?,?)',
                       (identity, job_id, canonical(action), time.time()))
        return identity

    def action(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM actions WHERE id=?', (identity,)).fetchone()
        if not row:
            raise KeyError(identity)
        result = dict(row)
        result['data'] = json.loads(result['data'])
        result['receipt'] = json.loads(result['receipt']) if result['receipt'] else None
        return result

    def action_state(self, identity, state, receipt=None):
        if state not in ('prepared', 'sending', 'sent', 'held', 'failed'):
            raise ValueError('Invalid action state')
        with self.connect() as db:
            db.execute('UPDATE actions SET state=?,receipt=?,updated=? WHERE id=?',
                       (state, canonical(receipt) if receipt is not None else None, time.time(), identity))

    def stop(self, run_id):
        with self.connect() as db:
            db.execute('UPDATE runs SET stopped=1 WHERE id=?', (run_id,))

    def status(self, run_id=None):
        jobs = self.jobs(run_id)
        states = {}
        for job in jobs:
            states[job['state']] = states.get(job['state'], 0) + 1
        return {'total': len(jobs), 'states': states,
                'stale': sum(bool(j['stale']) for j in jobs),
                'live': sum(j['state'] == 'running' for j in jobs)}
