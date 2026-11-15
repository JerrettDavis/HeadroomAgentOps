import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agentops.evidence import Evidence
from agentops.ci_evidence import collect_failed_ci


class CIEvidenceTests(unittest.TestCase):
    def test_failed_log_is_bound_to_head_and_cached_without_rereading(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence=Evidence(Path(directory)/'evidence')
            class GitHub:
                repo='o/r'
                def request(self,*args):
                    return {'id':3,'run_id':2,'head_sha':'head'}
            current={'head_sha':'head','workflows':[],'checks':[{'id':1,'name':'test',
                'status':'completed','conclusion':'failure',
                'details_url':'https://github.com/o/r/actions/runs/2/job/3'}]}
            with patch('agentops.ci_evidence.command',return_value='start\nFAILED test_case\nend') as download:
                first=collect_failed_ci(GitHub(),evidence,current)
                second=collect_failed_ci(GitHub(),evidence,current)
            self.assertEqual(first,second)
            self.assertEqual(download.call_count,1)
            self.assertIn('FAILED test_case',evidence.get(first['sha256'])['failed_jobs'][0]['excerpt'])
