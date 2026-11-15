import json
from pathlib import Path
import tempfile
import unittest

from agentops.evidence import Evidence
from agentops.ledger import Ledger
from agentops.panel import detail, report
from agentops.planning import packet


class AggregatePanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'ledger.sqlite3'
        self.ledger = Ledger(self.path)
        self.old, self.old_jobs = self.make_run('owner/repo', 10, [(1, 'a', 40), (2, 'e', 999)], 'old')
        self.new, self.new_jobs = self.make_run('owner/repo', 20, [(1, 'c', 200)], 'new')
        self.other, self.other_jobs = self.make_run('other/repo', 30, [(1, 'd', 50)], 'other')
        with self.ledger.connect() as db:
            for attempt, tokens in ((1, 100), (2, 40)):
                db.execute('INSERT INTO job_attempts VALUES(?,?,?,?,?,?,?)',
                           (self.old_jobs[0], attempt, 'fixture', None, 'held',
                            json.dumps({'execution': {'usage': {'input_tokens': tokens}}}), 15))
            db.execute('UPDATE jobs SET attempt=2 WHERE id=?', (self.old_jobs[0],))
            reused = {'execution': {'reused': True, 'usage': {'input_tokens': 999}}}
            db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(reused), self.old_jobs[1]))
            for index, (job, body) in enumerate(((self.old_jobs[0], 'Old review'), (self.new_jobs[0], 'New review'))):
                db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?)',
                           (str(index), job, json.dumps({'kind': 'review', 'body': body}),
                            'sent', '{}', 40 + index))

    def make_run(self, repo, created, entries, model):
        prs = [{'number': n, 'head_sha': head * 40, 'title': f'{model} PR {n}', 'body': model,
                'files': [{'filename': 'file.py', 'patch': f'@@ -1 +1 @@\n-before\n+{model}'}]}
               for n, head, _ in entries]
        snapshot = {'repo': repo, 'main_sha': 'b' * 40, 'prs': prs, 'collected_at': created}
        run = self.ledger.create_run(self.ledger.save_snapshot(snapshot), {'harness': 'codex', 'model': model})
        jobs = []
        with self.ledger.connect() as db:
            db.execute('UPDATE runs SET created=? WHERE id=?', (created, run))
        for index, (pr, (_, _, tokens)) in enumerate(zip(prs, entries)):
            job = self.ledger.enqueue(run, pr, 'b' * 40)
            jobs.append(job)
            session = f'{created:08d}-0000-0000-0000-{index:012d}'
            inputs = self.root / 'jobs' / session / 'input'
            inputs.mkdir(parents=True)
            (inputs / 'packet.json').write_text(packet(pr, snapshot, [], Evidence(self.root / 'evidence')))
            result = {'summary': model, 'execution': {'usage': {'input_tokens': tokens}}}
            with self.ledger.connect() as db:
                db.execute('UPDATE jobs SET state=?,attempt=1,created=?,handle=?,result=? WHERE id=?',
                           ('held', created + index, json.dumps({'session_id': session}), json.dumps(result), job))
        return run, jobs

    def test_default_all_deduplicates_by_repository_and_pr_and_keeps_sources(self):
        data = report(self.path)
        self.assertEqual(data['selected_run'], 'all')
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['job_count'], 4)
        group = next(j for j in data['jobs'] if j['repo'] == 'owner/repo' and j['number'] == 1)
        self.assertEqual(group['id'], self.new_jobs[0])
        self.assertEqual(group['model'], 'new')
        self.assertEqual(group['pr']['title'], 'new PR 1')
        self.assertEqual({j['run_id'] for j in group['sessions']}, {self.old, self.new})
        self.assertEqual({j['pr']['title'] for j in group['sessions']}, {'old PR 1', 'new PR 1'})
        self.assertEqual({a['run_id'] for a in data['actions']}, {self.old, self.new})
        self.assertEqual(data['usage']['input_tokens'], 390)
        self.assertEqual(data['usage']['reused_attempts'], 1)
        self.assertEqual(sum(r['usage']['input_tokens'] for r in data['session_usage']), 390)

    def test_explicit_run_filters_without_losing_existing_accounting(self):
        data = report(self.path, self.old)
        self.assertEqual(data['selected_run'], self.old)
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['usage']['input_tokens'], 140)
        self.assertEqual({a['body'] for a in data['actions']}, {'Old review'})
        self.assertEqual(report(self.path, 'all')['total'], 3)
        with self.assertRaises(KeyError):
            report(self.path, 'unknown')

    def test_grouped_detail_keeps_each_original_head_and_patch(self):
        data = detail(self.path, self.new_jobs[0], diffs=True, all_sessions=True)
        self.assertEqual(len(data['sessions']), 2)
        by_run = {s['run_id']: s for s in data['sessions']}
        self.assertIn('+old', by_run[self.old]['files'][0]['patch'])
        self.assertIn('+new', by_run[self.new]['files'][0]['patch'])
        self.assertEqual(by_run[self.old]['head_sha'], 'a' * 40)
        self.assertEqual(by_run[self.new]['head_sha'], 'c' * 40)
        self.assertNotIn(self.other, by_run)

    def test_empty_newest_run_does_not_hide_prior_prs(self):
        snapshot = self.ledger.save_snapshot({'repo': 'owner/repo', 'prs': []})
        self.ledger.create_run(snapshot, {})
        self.assertEqual(report(self.path)['total'], 3)
