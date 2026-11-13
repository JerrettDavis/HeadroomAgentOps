import tempfile
import unittest
from pathlib import Path
from agentops.approval_baseline import attach_approval_baseline
from agentops.evidence import Evidence
from agentops.policy import gate
from test_publisher import FakeGitHub, pr, result, MAIN
from agentops.ledger import Ledger
from agentops.publisher import Publisher


class ApprovalBaselineTests(unittest.TestCase):
    def test_inherited_workflow_hold_does_not_imply_new_execution_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Evidence(Path(directory))
            class GitHub(FakeGitHub):
                def review_workflow_baseline(self, current, main_sha):
                    return {'manifest_hash':'manifest','review_events':False}
            current={**pr(),'base_sha':MAIN}
            review={**result(),'source_review':{'manifest_hash':'manifest','verdict':'held',
                    'rationale':'Inherited unchecked installer','evidence':['security.yml']}}
            self.assertIn('Exact-source provenance review is incomplete',gate(current,current,MAIN,MAIN,review,[]))
            attach_approval_baseline(GitHub(),evidence,current,MAIN,review)
            self.assertEqual(gate(current,current,MAIN,MAIN,review,[]),[])
            self.assertEqual(review['source_review']['verdict'],'held')
            github=GitHub()
            github.current=current
            ledger=Ledger(Path(directory)/'ledger.db')
            snap=ledger.save_snapshot({'repo':'o/r','prs':[current]})
            run=ledger.create_run(snap,{})
            identity=ledger.enqueue(run,current,MAIN)
            job=ledger.claim('publisher',1,run)
            review['actions']=[{'kind':'ci','event':'RERUN','workflow_id':1}]
            publication=Publisher(github,ledger,ci_handler=lambda *a:{}).publish(job,current,review)
            self.assertEqual(publication['sent'],[])
            self.assertTrue(publication['held'])

    def test_execution_sensitive_delta_cannot_borrow_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Evidence(Path(directory))
            current={**pr(),'base_sha':MAIN,'files':[{'filename':'.github/actions/local/action.yml'}]}
            review=result()
            attach_approval_baseline(FakeGitHub(),evidence,current,MAIN,review)
            self.assertNotIn('approval_source_baseline',review)

    def test_review_triggered_workflows_cannot_borrow_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            class GitHub(FakeGitHub):
                def review_workflow_baseline(self,current,main_sha):
                    return {'manifest_hash':'manifest','review_events':True}
            review=result()
            attach_approval_baseline(GitHub(),Evidence(Path(directory)),{**pr(),'base_sha':MAIN},MAIN,review)
            self.assertNotIn('approval_source_baseline',review)

    def test_cached_base_pointer_does_not_block_verified_main_target(self):
        with tempfile.TemporaryDirectory() as directory:
            class GitHub(FakeGitHub):
                def review_workflow_baseline(self,current,main_sha):
                    return {'manifest_hash':'manifest','merge_base_sha':'old','review_events':False}
            current={**pr(),'base_ref':'main','base_sha':'old'}
            review=result()
            attach_approval_baseline(GitHub(),Evidence(Path(directory)),current,MAIN,review)
            self.assertIn('approval_source_baseline',review)

    def test_review_approval_can_precede_ci_authorization_without_claiming_ci_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            class GitHub(FakeGitHub):
                def review_workflow_baseline(self,current,main_sha):
                    return {'manifest_hash':'manifest','review_events':False}
            current={**pr(),'base_ref':'main','base_sha':MAIN,'checks':[],
                     'workflows':[{**pr()['workflows'][0],'status':'action_required','conclusion':None}]}
            review=result()
            self.assertTrue(any('not terminal' in r for r in gate(current,current,MAIN,MAIN,review,[])))
            attach_approval_baseline(GitHub(),Evidence(Path(directory)),current,MAIN,review)
            self.assertEqual(gate(current,current,MAIN,MAIN,review,[]),[])
            self.assertEqual(current['workflows'][0]['status'],'action_required')
            current['workflows'][0].update(status='completed',conclusion='action_required')
            self.assertEqual(gate(current,current,MAIN,MAIN,review,[]),[])
            current['workflows'][0].update(status='queued',conclusion=None)
            self.assertEqual(gate(current,current,MAIN,MAIN,review,[]),[])
            current['workflows'][0]['status']='in_progress'
            current['workflows'][0]['conclusion']=None
            self.assertTrue(any('not terminal' in r for r in gate(current,current,MAIN,MAIN,review,[])))

    def test_product_proof_scripts_do_not_count_as_changed_ci_tooling(self):
        with tempfile.TemporaryDirectory() as directory:
            class GitHub(FakeGitHub):
                def review_workflow_baseline(self,current,main_sha):
                    return {'manifest_hash':'manifest','review_events':False}
            current={**pr(),'base_ref':'main','base_sha':'old',
                     'files':[{'filename':'plugins/opencode/scripts/live-proof.mjs'}]}
            review=result()
            attach_approval_baseline(GitHub(),Evidence(Path(directory)),current,MAIN,review)
            self.assertIn('approval_source_baseline',review)
