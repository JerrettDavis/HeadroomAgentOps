"""Maintenance mutations share existing publication and active policy holds."""
import json
import sqlite3
from contextlib import closing


def mutation_gate(path, config, row=None, *, ci=False):
    if config.get('publish', True) is not True:
        raise ValueError('Publication is disabled in the active configuration')
    if ci and config.get('ci_enabled', True) is not True:
        raise ValueError('CI execution is disabled in the active configuration')
    row = row or {}
    if (row.get('author') or '').lower() in {a.lower() for a in config.get('exclude_authors', [])}:
        raise ValueError('Author is excluded by the active configuration')
    if not path.exists():
        return
    try:
        with closing(sqlite3.connect(path)) as db:
            notices = db.execute("SELECT numbers,body FROM notices WHERE repo=? AND status='active' "
                                 "AND kind IN ('security','policy')", (config['repo'],)).fetchall()
    except sqlite3.OperationalError:
        return
    for numbers, body in notices:
        targets = json.loads(numbers)
        if not targets or row.get('number') in targets:
            raise ValueError('Active execution/publication hold: ' + body)
