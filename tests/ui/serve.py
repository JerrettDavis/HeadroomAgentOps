"""Isolated browser-test fixture: no watcher, GitHub calls or real workers."""

from pathlib import Path
import sys
import tempfile
import json
import argparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentops.cli import DEFAULTS
from agentops.ledger import Ledger
from agentops.panel import make_server
from agentops.planning import packet
from agentops.evidence import Evidence


parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()

with tempfile.TemporaryDirectory(prefix='agentops-ui-') as directory:
    path = Path(directory) / 'ledger.sqlite3'
    ledger = Ledger(path)
    body = '# Summary\n\n**Safe** Markdown with `inline code`.\n\n' + '\n\n'.join(
        f'## Section {i}\n\nDescription paragraph {i}.' for i in range(30))
    prs = [{'number': i, 'title': f'Test pull request {i}', 'author': 'contributor',
            'head_sha': 'a' * 40, 'head_ref': 'feature/test', 'base_ref': 'main',
            'body': body, 'draft': False, 'files': [
                {'filename': 'source.py', 'status': 'modified', 'patch': '@@ -1 +1 @@\n-old\n+new'}]}
           for i in range(1, 31)]
    inventory = {'repo': 'example/repository', 'main_sha': 'b' * 40, 'prs': prs}
    snapshot = ledger.save_snapshot(inventory)
    run = ledger.create_run(snapshot, {'harness': 'codex'})
    baseline_jobs = []
    for pr in prs:
        job = ledger.enqueue(run, pr, 'b' * 40)
        baseline_jobs.append(job)
        session = f'00000000-0000-0000-0000-{pr["number"]:012d}'
        inputs = Path(directory) / 'jobs' / session / 'input'
        inputs.mkdir(parents=True)
        (inputs / 'packet.json').write_text(packet(pr, inventory, [], Evidence(Path(directory) / 'evidence')))
        with ledger.connect() as db:
            db.execute('UPDATE jobs SET handle=? WHERE id=?', (json.dumps({'session_id': session}), job))
    followup = {**prs[0], 'head_sha': 'c' * 40, 'title': 'Follow-up pull request 1',
                'body': '# Summary\n\nFollow-up description.',
                'files': [{'filename': 'source.py', 'status': 'modified',
                           'patch': '@@ -1 +1 @@\n-old\n+followup'}]}
    extra = {**prs[1], 'number': 31, 'title': 'Additional pull request 31'}
    next_inventory = {**inventory, 'prs': [followup, extra]}
    next_run = ledger.create_run(ledger.save_snapshot(next_inventory), {'harness': 'codex', 'model': 'followup'})
    next_jobs = []
    for pr in (followup, extra):
        job = ledger.enqueue(next_run, pr, 'b' * 40)
        next_jobs.append(job)
        session = f'11111111-0000-0000-0000-{pr["number"]:012d}'
        inputs = Path(directory) / 'jobs' / session / 'input'
        inputs.mkdir(parents=True)
        (inputs / 'packet.json').write_text(packet(pr, next_inventory, [], Evidence(Path(directory) / 'evidence')))
        with ledger.connect() as db:
            db.execute('UPDATE jobs SET handle=? WHERE id=?', (json.dumps({'session_id': session}), job))
    with ledger.connect() as db:
        for index, (job, label, tokens) in enumerate(((baseline_jobs[0], 'Initial session', 100),
                                                    (next_jobs[0], 'Follow-up session', 200))):
            result = {'summary': label + ' review', 'evidence': [
                {'kind': 'test', 'status': 'passed', 'description': label + ' evidence', 'command': 'python -m unittest'}],
                'execution': {'usage': {'input_tokens': tokens, 'output_tokens': 10}}}
            db.execute('UPDATE jobs SET state=?,attempt=1,result=? WHERE id=?', ('held', json.dumps(result), job))
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?)',
                       (f'action-{index}', job, json.dumps({'kind': 'review', 'body': label + ' comment'}), 'sent', '{}', index + 1))
        db.execute('UPDATE jobs SET state=?,attempt=1 WHERE id=?', ('running', next_jobs[1]))
    config = {**DEFAULTS, 'repo': 'example/repository', 'state': directory, 'publish': False}
    with make_server(path, port=args.port, config=config) as server:
        server.serve_forever()
