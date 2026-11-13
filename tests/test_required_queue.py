import argparse
import json
import tempfile
import unittest
from pathlib import Path
from agentops.cli import make_plan
from agentops.evidence import Evidence
from agentops.ledger import Ledger


class RequiredQueueTests(unittest.TestCase):
    def test_all_required_contributor_prs_are_planned_without_a_pilot_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            prs=[{'number':n,'head_sha':'b'*40,'author':'contributor','updated_at':'2026-11-13',
                  'review_decision':'REVIEW_REQUIRED'} for n in range(1,31)]
            prs += [{'number':31,'head_sha':'c'*40,'author':'owner','updated_at':'2026-11-13',
                     'review_decision':'REVIEW_REQUIRED'},
                    {'number':32,'head_sha':'d'*40,'author':'contributor','updated_at':'2026-11-13',
                     'review_decision':'APPROVED'}]
            path=root/'snapshot.json'
            path.write_text(json.dumps({'repo':'o/r','complete':True,'main_sha':'a'*40,'prs':prs}))
            ledger=Ledger(root/'ledger.db')
            config={'repo':'o/r','exclude_authors':['owner'],'harness':'codex','concurrency':4,'publish':True}
            args=argparse.Namespace(snapshot=str(path),pr=None,limit=None,review_required=True,require_decision=True)
            report=make_plan(config,args,ledger,None,Evidence(root/'evidence'))
            self.assertEqual(set(report['selected']),set(range(1,31)))
            self.assertTrue(ledger.get_run(report['run_id'])['config']['require_decision'])
            self.assertEqual(ledger.get_run(report['run_id'])['config']['concurrency'],4)
            self.assertTrue(all(j['result']['review_task']=='qualification' for j in ledger.jobs(report['run_id'])))
