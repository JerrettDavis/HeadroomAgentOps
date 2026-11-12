"""Opt-in tests of real containers with a deterministic fake CLI; no model calls."""

import json
import base64
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from agentops.model import canonical
from agentops.process import command
from agentops.worker import Worker, container_command, private_job_directory
from agentops.evidence import Evidence


FAKE_CLI = '''#!/usr/bin/python3
import json, pathlib, subprocess, sys, time
if '--version' in sys.argv:
    print('fixture-harness 1.0')
    raise SystemExit(0)
a=json.loads(pathlib.Path('/job/input/assignment.json').read_text())
subprocess.run(['git','checkout','-b','agentops-candidate'],check=True)
pathlib.Path('/workspace/fixture.txt').write_text('maintainer candidate\\n')
subprocess.run(['git','add','fixture.txt'],check=True)
subprocess.run(['git','commit','-m','fixture candidate'],check=True)
time.sleep(a.get('fixture_sleep',0))
if a.get('fixture_failure') == 'exit':
    raise SystemExit(17)
if a.get('fixture_failure') == 'malformed':
    pathlib.Path('/job/output/result.json').write_text('{broken')
    raise SystemExit(0)
r={k:a[k] for k in ('session_id','number','head_sha','main_sha')}
r.update(schema=1,outcome='held',summary='Controlled fixture, no live inference.',findings=[],
         evidence=[],actions=[],notices=[],coverage={'files':['fixture.txt'],'threads':[]})
pathlib.Path('/job/output/result.json').write_text(json.dumps(r))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':0,'output_tokens':0}}))
'''


@unittest.skipUnless(os.environ.get('AGENTOPS_DOCKER_TESTS') == '1', 'Set AGENTOPS_DOCKER_TESTS=1 for real container tests')
class DockerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = str(uuid.uuid4())
        self.directory = self.root / self.session
        private_job_directory(self.directory)
        for name in ('repo', 'input', 'output', 'auth', 'fakebin'):
            (self.directory / name).mkdir()
        repo = self.directory / 'repo'
        command(['git', 'init', str(repo)])
        command(['git', 'config', 'user.name', 'Fixture'], cwd=repo)
        command(['git', 'config', 'user.email', 'fixture@example.test'], cwd=repo)
        (repo / 'fixture.txt').write_text('original author content\n')
        command(['git', 'add', 'fixture.txt'], cwd=repo)
        command(['git', 'commit', '-m', 'fixture original'], cwd=repo)
        self.head = command(['git', 'rev-parse', 'HEAD'], cwd=repo)
        fake = self.directory / 'fakebin' / 'codex'
        fake.write_text(FAKE_CLI, encoding='utf8', newline='\n')
        fake.chmod(0o755)
        (self.directory / 'auth' / 'model-env.json').write_text('{}')
        self.worker = Worker(self.root, 'o/r')
        self.handle = {'session_id': self.session, 'container': 'agentops-' + self.session,
                       'directory': str(self.directory)}
        self.addCleanup(self.cleanup_worker)

    def cleanup_worker(self):
        state = self.worker.observe(self.handle)['state']
        if state == 'live':
            self.worker.cancel(self.handle)
        if state != 'unknown':
            self.worker.cleanup(self.handle)

    def start(self, sleep=0, timeout=30, failure=None):
        assignment = {'session_id': self.session, 'number': 1, 'head_sha': self.head, 'main_sha': self.head,
                      'harness': 'codex', 'model': None, 'prompt': 'Controlled lifecycle fixture.',
                      'fixture_sleep': sleep, 'timeout_seconds': timeout, 'fixture_failure': failure}
        (self.directory / 'input' / 'assignment.json').write_text(canonical(assignment))
        image = command(['docker', 'image', 'inspect', 'headroom-agentops-worker:local', '--format', '{{.Id}}'])
        args = container_command(self.directory, self.session, image)
        args[-1:-1] = ['--mount', f'type=bind,src={self.directory / "fakebin"},dst=/opt/harness/node_modules/.bin,readonly']
        command(args)
        command(['docker', 'start', self.handle['container']])
        return assignment

    def terminal(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            observation = self.worker.observe(self.handle)
            if observation['state'] == 'exited':
                return observation
            self.assertEqual(observation['state'], 'live')
            time.sleep(0.1)
        self.fail('Owned fake CLI container failed to terminate within the test deadline')

    def test_held_candidate_is_archived_even_without_a_push_action(self):
        self.start()
        observation = self.terminal()
        if observation['exit_code']:
            self.fail(self.worker.diagnostics(self.handle))
        bundle = self.directory / 'output' / 'candidate.bundle'
        self.assertTrue(bundle.is_file(), 'Unpublished candidate must survive checkout cleanup')
        metadata = json.loads((self.directory / 'output' / 'candidate.json').read_text())
        self.assertEqual(metadata['original_head'], self.head)
        self.assertFalse(metadata['published'])
        evidence = Evidence(self.root / 'evidence')
        archive = self.worker.archive_candidate(self.handle,
            {'id': 'fixture-job', 'head_sha': self.head, 'main_sha': self.head}, evidence)
        self.assertEqual(base64.b64decode(evidence.get(archive['sha256'])['bundle_base64']), bundle.read_bytes())
        original_bytes = bundle.read_bytes()
        bundle.write_bytes(b'partial-corrupt-bundle')
        with self.assertRaisesRegex(ValueError, 'receipt'):
            self.worker.archive_candidate(self.handle,
                {'id': 'fixture-job', 'head_sha': self.head, 'main_sha': self.head}, evidence)
        bundle.write_bytes(original_bytes)
        self.worker.cleanup(self.handle)
        self.assertFalse((self.directory / 'repo').exists())
        self.assertTrue(bundle.is_file())

    def test_intrinsic_budget_terminates_worker_without_a_coordinator(self):
        self.start(sleep=20, timeout=1)
        observation = self.terminal(timeout=15)
        self.assertEqual(observation['exit_code'], 124)
        metadata = self.worker.metrics(self.handle)
        self.assertEqual(metadata['exit_code'], 124)
        self.assertTrue((self.directory / 'output' / 'candidate.bundle').is_file())

    def test_failed_cli_preserves_committed_candidate(self):
        self.start(failure='exit')
        self.assertEqual(self.terminal()['exit_code'], 17)
        self.assertTrue((self.directory / 'output' / 'candidate.bundle').is_file())

    def test_malformed_result_preserves_committed_candidate(self):
        self.start(failure='malformed')
        self.assertNotEqual(self.terminal()['exit_code'], 0)
        self.assertTrue((self.directory / 'output' / 'candidate.bundle').is_file())


if __name__ == '__main__':
    unittest.main()
