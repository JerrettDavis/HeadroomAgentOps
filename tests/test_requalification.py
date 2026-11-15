import unittest
import test_settlement
from agentops.requalification import requalify_inherited_review


class RequalificationTests(unittest.TestCase):
    setUp=test_settlement.SettlementTests.setUp
    def test_sole_inherited_hold_can_be_approved_without_rerunning_tests(self):
        result={**self.saved,'require_decision':True,
            'qualification_limits':['Exact-source provenance review is incomplete'],
            'source_review':{'manifest_hash':'manifest','verdict':'held',
                             'rationale':'Inherited installer hold','evidence':['security.yml']}}
        self.ledger.reclassify(self.job,'held',result)
        self.github.review_workflow_baseline=lambda current,main: {'manifest_hash':'manifest','review_events':False}
        report=requalify_inherited_review(self.ledger,self.github,self.evidence,self.publisher,self.job)
        self.assertTrue(report['approved'])
        self.assertEqual(self.github.posts[-1]['event'],'APPROVE')
        self.assertEqual(self.ledger.job(self.job)['attempt'],1)

    def test_new_discussion_prevents_mechanical_approval(self):
        self.ledger.reclassify(self.job,'held',{**self.saved,'require_decision':True,
            'qualification_limits':['Exact-source provenance review is incomplete']})
        self.github.current={**self.current,'discussion_hash':'changed'}
        report=requalify_inherited_review(self.ledger,self.github,self.evidence,self.publisher,self.job)
        self.assertFalse(report['approved'])
        self.assertEqual(self.github.posts,[])
