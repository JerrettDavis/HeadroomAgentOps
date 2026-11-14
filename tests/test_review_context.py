import json
import tempfile
import unittest
from pathlib import Path
from agentops.evidence import Evidence
from agentops.worker import stage_review_context


class ReviewContextTests(unittest.TestCase):
    def test_prior_source_coverage_reusable_only_for_identical_head_base_and_main(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = Evidence(root / 'evidence')
            prior_pr = {'number': 1, 'head_sha': 'head', 'base_sha': 'base'}
            proof = {'number': 1, 'head_sha': 'head', 'main_sha': 'main',
                     'reviewed_pr_evidence': evidence.put(prior_pr), 'coverage': {'files': ['a.py']}}
            job = {'number': 1, 'main_sha': 'main',
                   'prior_review_context': {'result_evidence': evidence.put(proof)}}
            (root / 'input').mkdir()
            self.assertTrue(stage_review_context(root, job, prior_pr, evidence))
            self.assertTrue(json.loads((root / 'input/previous-review-context.json').read_text())['coverage_reusable'])
            stage_review_context(root, job, {**prior_pr, 'head_sha': 'new'}, evidence)
            self.assertFalse(json.loads((root / 'input/previous-review-context.json').read_text())['coverage_reusable'])

    def test_mismatched_prior_pr_cannot_be_staged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = Evidence(root / 'evidence')
            proof = {'number': 2, 'head_sha': 'head', 'main_sha': 'main',
                     'reviewed_pr_evidence': evidence.put({'number': 2, 'head_sha': 'head'})}
            with self.assertRaises(ValueError):
                stage_review_context(root, {'number': 1, 'main_sha': 'main',
                    'prior_review_context': {'result_evidence': evidence.put(proof)}},
                    {'number': 1, 'head_sha': 'head'}, evidence)
