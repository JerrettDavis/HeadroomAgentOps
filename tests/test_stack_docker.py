"""Opt-in real container checks; no harness or model is invoked."""
import os
import unittest
from pathlib import Path

from agentops.stacking import plan_stack
from test_stack_git import GitFixture


@unittest.skipUnless(os.environ.get('AGENTOPS_DOCKER_TESTS') == '1', 'Set AGENTOPS_DOCKER_TESTS=1 for real containers')
class StackDockerTests(GitFixture):
    def test_validation_runs_without_auth_or_network_and_preserves_pristine_checkout(self):
        self.source(1, 'first', 'one')
        script = ('from pathlib import Path; import os,subprocess; '
                  'assert not Path("/job/auth").exists(); '
                  'assert not any(k in os.environ for k in ("GH_TOKEN","GITHUB_TOKEN","OPENAI_API_KEY")); '
                  'assert len(Path("/proc/net/route").read_text().splitlines()) == 1; '
                  'assert Path("file.txt").read_text().startswith("one"); '
                  'subprocess.run(["git","status","--porcelain"],check=True); '
                  'Path("file.txt").write_text("validation side effect"); print("isolated validation passed")')
        result = self.engine(commands=[['python3', '-c', script]]).prepare(plan_stack(self.data, [1]))
        self.assertEqual(result['state'], 'prepared', result)
        self.assertEqual(result['validation']['state'], 'passed')
        self.assertIn('isolated validation passed', result['validation']['commands'][0]['log'])
        self.assertTrue((Path(result['checkout']) / 'file.txt').read_text().startswith('one'))

    def test_timeout_stops_container_and_cannot_publish(self):
        self.source(1, 'first', 'one')
        result = self.engine(commands=[['python3', '-c', 'import time; time.sleep(30)']], timeout=3).prepare(
            plan_stack(self.data, [1]))
        self.assertEqual(result['state'], 'validation_failed')
        self.assertTrue(result['validation']['commands'][0]['timed_out'])
