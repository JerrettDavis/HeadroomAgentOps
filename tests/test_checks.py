import unittest
from agentops.checks import effective_ci


class EffectiveCITests(unittest.TestCase):
    def test_successful_replacement_supersedes_only_its_old_workflow_run(self):
        runs=[{'id':1,'workflow_id':10,'path':'a.yml','source_repo':'o/r','head_sha':'h',
               'event':'pull_request','created_at':'1','conclusion':'cancelled'},
              {'id':2,'workflow_id':10,'path':'a.yml','source_repo':'o/r','head_sha':'h',
               'event':'pull_request','created_at':'2','conclusion':'success'},
              {'id':3,'workflow_id':11,'path':'b.yml','source_repo':'o/r','head_sha':'h',
               'event':'pull_request','created_at':'1','conclusion':'failure'}]
        checks=[{'id':1,'name':'same','details_url':'https://github.com/o/r/actions/runs/1/job/1'},
                {'id':2,'name':'same','details_url':'https://github.com/o/r/actions/runs/2/job/2'},
                {'id':3,'name':'same','details_url':'https://github.com/o/r/actions/runs/3/job/3'}]
        selected,workflows=effective_ci({'checks':checks,'workflows':runs})
        self.assertEqual([r['id'] for r in workflows],[2,3])
        self.assertEqual([c['id'] for c in selected],[2,3])

    def test_newer_failure_is_not_hidden_by_earlier_success(self):
        runs=[{'id':1,'path':'a.yml','head_sha':'h','conclusion':'success'},
              {'id':2,'path':'a.yml','head_sha':'h','conclusion':'failure'}]
        _,selected=effective_ci({'workflows':runs,'checks':[]})
        self.assertEqual(selected[0]['conclusion'],'failure')
