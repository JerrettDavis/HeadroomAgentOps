"""Repository-scoped durable maintenance state, independent of review admission."""
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class MaintenanceStore:
    def __init__(self, path, repo):
        self.path = Path(path).resolve().parent / 'maintenance.sqlite3'
        self.repo = repo.lower()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS records '
                       '(repo TEXT, kind TEXT, key TEXT, body TEXT NOT NULL, '
                       'PRIMARY KEY(repo,kind,key))')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute('PRAGMA busy_timeout=30000')
        return db

    def settings(self):
        return {'watch': True, 'autofix': False, 'reapprove': True,
                **(self.get('settings', 'settings') or {})}

    def configure(self, values):
        if any(k not in ('watch', 'autofix', 'reapprove') or type(v) is not bool
               for k, v in values.items()):
            raise ValueError('Maintenance settings require boolean watch, autofix or reapprove')
        self.update('settings', 'settings', values)
        return self.settings()

    def get(self, kind, key):
        with closing(self.connect()) as db:
            row = db.execute('SELECT body FROM records WHERE repo=? AND kind=? AND key=?',
                             (self.repo, kind, str(key))).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, kind):
        with closing(self.connect()) as db:
            rows = db.execute('SELECT body FROM records WHERE repo=? AND kind=?',
                              (self.repo, kind)).fetchall()
        return sorted((json.loads(row[0]) for row in rows),
                      key=lambda row: row.get('updated', 0), reverse=True)

    def put(self, kind, key, data):
        with closing(self.connect()) as db, db:
            self._write(db, kind, key, data)
        return self.get(kind, key)

    def _write(self, db, kind, key, data):
        body = {**data, 'key': str(key), 'updated': time.time()}
        db.execute('INSERT OR REPLACE INTO records VALUES (?,?,?,?)',
                   (self.repo, kind, str(key), json.dumps(body, allow_nan=False)))

    def update(self, kind, key, data):
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM records WHERE repo=? AND kind=? AND key=?',
                             (self.repo, kind, str(key))).fetchone()
            self._write(db, kind, key, {**(json.loads(row[0]) if row else {}), **data})
        return self.get(kind, key)

    def claim(self, kind, key, data):
        body = {**data, 'key': str(key), 'updated': time.time()}
        with closing(self.connect()) as db, db:
            cursor = db.execute('INSERT OR IGNORE INTO records VALUES (?,?,?,?)',
                                (self.repo, kind, str(key), json.dumps(body, allow_nan=False)))
            return cursor.rowcount == 1

    def transition(self, kind, key, expected, data):
        """Claim a queued record across threads and coordinator processes."""
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT body FROM records WHERE repo=? AND kind=? AND key=?',
                             (self.repo, kind, str(key))).fetchone()
            prior = json.loads(row[0]) if row else {}
            if prior.get('state') not in expected:
                return False
            self._write(db, kind, key, {**prior, **data})
            return True
