import tempfile
import unittest
from pathlib import Path
from agentops.evidence import Evidence
from agentops.ledger import Ledger
from agentops.qualification import qualification_pilot


class QualificationPilotTests(unittest.TestCase):
    def test_shared_provenance_is_investigated_once_without_duplicating_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ledger,evidence=Ledger(root/'ledger.db'),Evidence(root/'evidence')
            prs=[{'number':n,'head_sha':'b'*40} for n in (1,2,3)]
            snap=ledger.save_snapshot({'repo':'o/r','main_sha':'a'*40,'prs':prs})
            old=ledger.create_run(snap,{})
            for pr in prs[:2]:
                identity=ledger.enqueue(old,pr,'a'*40)
                ledger.claim('worker',1,old)
                proof={'source_review':{'manifest_hash':'shared','verdict':'held'},
                       'qualification_limits':['Exact-source provenance review is incomplete'],
                       'findings':[]}
                ledger.finish(identity,'worker','held',{**proof,'evidence_record':evidence.put(proof)})
            pending=ledger.create_run(snap,{'repo':'o/r'})
            for pr in prs:
                identity=ledger.enqueue(pending,pr,'a'*40)
                if pr['number']!=3:
                    ledger.set_review_task(identity,'qualification')
            before={j['id'] for j in ledger.jobs(pending)}
            report=qualification_pilot(ledger,evidence,pending)
            self.assertEqual(report['selected'],[1,3])
            self.assertEqual(report['deferred'],[2])
            after=ledger.jobs(pending)+ledger.jobs(report['run_id'])
            self.assertEqual({j['id'] for j in after},before)
            self.assertEqual(len(after),3)

    def test_existing_shared_hold_prevents_another_identical_audit_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            ledger,evidence=Ledger(root/'ledger.db'),Evidence(root/'evidence')
            pr={'number':1,'head_sha':'b'*40}
            snap=ledger.save_snapshot({'repo':'o/r','main_sha':'a'*40,'prs':[pr]})
            old=ledger.create_run(snap,{})
            job=ledger.enqueue(old,pr,'a'*40)
            ledger.claim('worker',1,old)
            proof={'source_review':{'manifest_hash':'shared','verdict':'held'},
                   'qualification_limits':['Exact-source provenance review is incomplete']}
            ref=evidence.put(proof)
            ledger.finish(job,'worker','held',{**proof,'evidence_record':ref})
            ledger.record_workflow_audit('o/r','shared',ref)
            pending=ledger.create_run(snap,{})
            job=ledger.enqueue(pending,pr,'a'*40)
            ledger.set_review_task(job,'qualification')
            report=qualification_pilot(ledger,evidence,pending)
            self.assertEqual(report['selected'],[])
            self.assertEqual(report['deferred'],[1])
            self.assertEqual(report['blocked_manifest_hashes'],['shared'])
