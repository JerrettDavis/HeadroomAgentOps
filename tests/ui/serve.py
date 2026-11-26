"""Isolated browser-test fixture: no watcher, GitHub calls or real workers."""

from pathlib import Path
import sys
import tempfile
import json
import argparse
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentops.cli import DEFAULTS
from agentops.ledger import Ledger
from agentops.panel import make_server
from agentops.planning import packet
from agentops.evidence import Evidence
from agentops.merge_priority import rank_prs


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
    config = {**DEFAULTS, 'repo': 'example/repository', 'state': directory,
              'auth_root': directory, 'publish': False, 'provider_limits': {
                  'claude': {'stop_percent': 80}, 'devin': {'stop_percent': 80}}}
    def synthetic_usage(config):
        return {'source': 'Synthetic account usage', 'blocked': False, 'auth_fingerprint': 'synthetic', 'windows': [{
            'id': 'weekly', 'label': 'Weekly', 'period': 'weekly', 'window_minutes': 10080,
            'used_percent': 60, 'resets_at': time.time() + 86400}]}
    class SyntheticPriority:
        def status(self):
            common = dict(author='maintainer', association='MEMBER', labels=[], body='',
                          ci='passed', up_to_date=True, draft=False, mergeable='MERGEABLE',
                          base_ref='main', head_repo='example/repository', review_decision='APPROVED')
            rows = [dict(common, number=101, title='fix: repair cache', head_ref='repair'),
                    dict(common, number=102, title='feat: follow-up', head_ref='followup', base_ref='repair',
                         association='CONTRIBUTOR'),
                    dict(common, number=103, title='chore(deps): update runtime', head_ref='deps',
                         association='CONTRIBUTOR'),
                    dict(common, number=104, title='fix: pending checks', head_ref='pending', ci='pending')]
            return dict(repo='example/repository', default_branch='main', fresh=True,
                        observed_at=time.time(), refreshing=False, error=None, refresh_seconds=60,
                        prs=rank_prs('example/repository', rows))
    # A real offline candidate source lets Studio browser tests exercise the
    # measured supervisor/runner path without provider credentials or charges.
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from benchmark_fixture import build_fixture
    from agentops.benchmark_service import BenchmarkService, _write
    from agentops.benchmarks.core import digest
    benchmark_service=BenchmarkService(path)
    source_id='f'*32
    source_directory=benchmark_service.root/source_id
    wheel_directory=source_directory/'wheel'
    wheel_directory.mkdir(parents=True)
    wheel,wheel_digest=build_fixture(wheel_directory)
    source={'kind':'artifact','repository':'fixture/studio','sha':'f'*40,'artifact_id':1}
    argv,paths=benchmark_service._argv({'action':'source','source':source},source_directory)
    record={'schema_version':1,'request':source,'resolved':source,'provenance':{'strategy':'offline-fixture'},
            'wheel':'wheel/'+wheel.name,'sha256':wheel_digest}
    _write(source_directory/'source.json',record)
    (source_directory/'source.sha256').write_text(digest(record),encoding='utf-8')
    _write(source_directory/'job.json',{'id':source_id,'label':'Offline measured fixture','action':'source',
        'state':'completed','created_at':time.time()-1,'exit_code':0,'argv':argv,'paths':paths})
    with make_server(path, port=args.port, config=config, usage_reader=synthetic_usage,
                     benchmark_service=benchmark_service,
                     priority_monitor=SyntheticPriority(),
                     usage_identity_reader=lambda config: 'synthetic') as server:
        server.serve_forever()
