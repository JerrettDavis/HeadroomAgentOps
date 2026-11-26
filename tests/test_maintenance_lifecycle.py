"""Real Git candidate export/import and host publication with a deterministic harness."""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentops.maintenance_agent import MaintenanceAgent
from agentops.maintenance_git import MaintenanceGit
from agentops.worker import Worker
from agentops.process import command
from agentops.model import digest


DOCKER_CLI = '''#!/usr/bin/python3
import json, pathlib, subprocess, sys
if '--version' in sys.argv:
    print('maintenance-fixture 1.0')
    raise SystemExit(0)
a = json.loads(pathlib.Path('/job/input/assignment.json').read_text())
pathlib.Path('/workspace/answer.py').write_text('value = 1\\n')
subprocess.run(['/usr/bin/python3', '-c', 'import answer; assert answer.value == 1'], check=True)
subprocess.run(['git', 'add', 'answer.py'], check=True)
subprocess.run(['git', 'commit', '-m', 'fix main fixture'], check=True)
r = {k: a[k] for k in ('schema', 'session_id', 'number', 'head_sha', 'main_sha')}
r.update(outcome='ready', summary='Corrected fixture answer', findings=[], actions=[], notices=[],
         coverage={'files':['answer.py'], 'threads':[]}, evidence=[{'kind':'test',
         'description':'actual container assertion', 'command':'python3 -c "import answer; assert answer.value == 1"', 'status':'passed'}])
pathlib.Path('/job/output/result.json').write_text(json.dumps(r))
'''


class DockerFixtureWorker(Worker):
    starts = 0

    def start(self, prepared):
        self.starts += 1
        directory = Path(prepared['directory'])
        fakebin = directory / 'fakebin'
        fakebin.mkdir()
        cli = fakebin / 'codex'
        cli.write_text(DOCKER_CLI, encoding='utf8', newline='\n')
        cli.chmod(0o755)
        def runner(args, **kwargs):
            if args[:2] == ['docker', 'create']:
                args = args[:-1] + ['--network', 'none', '--env',
                    'PATH=/fakebin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                    '--mount', f'type=bind,src={fakebin},dst=/fakebin,readonly', args[-1]]
            return command(args, **kwargs)
        self.runner = runner
        return super().start(prepared)


def git(path, *args):
    return subprocess.check_output(['git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=', *args],
                                   cwd=path, text=True, stderr=subprocess.DEVNULL).strip()


class FixtureWorker(Worker):
    starts = 0
    protected = False

    def start(self, prepared):
        self.starts += 1
        directory = Path(prepared['directory'])
        checkout = directory / 'repo'
        (checkout / 'answer.py').write_text('value = 1\n')
        if self.protected:
            source = checkout / '.github' / 'workflows' / 'ci.yml'
            source.parent.mkdir(parents=True)
            source.write_text('name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo fixture\n')
        subprocess.run([sys.executable, '-c', 'import answer; assert answer.value == 1'], cwd=checkout, check=True)
        git(checkout, 'add', '.')
        git(checkout, 'commit', '-m', 'fix main fixture')
        assignment = prepared['assignment']
        output = directory / 'output'
        bundle = output / 'candidate.bundle'
        git(checkout, 'bundle', 'create', str(bundle), assignment['head_sha'] + '..refs/heads/agentops-candidate')
        candidate = git(checkout, 'rev-parse', 'HEAD')
        (output / 'candidate.json').write_text(json.dumps({'original_head': assignment['head_sha'],
            'candidate_sha': candidate, 'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest()}))
        result = {**{k: assignment[k] for k in
            ('schema', 'session_id', 'number', 'head_sha', 'main_sha')}, 'outcome': 'ready',
            'summary': 'Corrected fixture answer', 'findings': [], 'actions': [], 'notices': [],
            'coverage': {'files': ['answer.py'], 'threads': []}, 'evidence': [{'kind': 'test',
              'description': 'actual assertion', 'command': 'python3 -c "import answer; assert answer.value == 1"', 'status': 'passed'}]}
        if self.protected:
            paths = git(checkout, 'ls-tree', '-r', '--name-only', 'HEAD', '--', '.github', '.gitmodules').splitlines()
            manifest = digest([{'path': p, 'blob': git(checkout, 'rev-parse', 'HEAD:' + p)} for p in sorted(paths)])
            result['source_review'] = {'manifest_hash': manifest, 'verdict': 'clear',
                'rationale': 'Audited fixture workflow commands and execution dependencies', 'evidence': ['candidate workflow source']}
        (output / 'result.json').write_text(json.dumps(result))
        return {'session_id': prepared['session_id'], 'directory': str(directory), 'container': 'fixture'}

    def observe(self, handle):
        return {'state': 'exited', 'exit_code': 0}


class ProtectedFixtureWorker(FixtureWorker):
    protected = True


class MaintenanceLifecycleTests(unittest.TestCase):
    def test_remediation_rejects_erased_contribution_and_merge_substitute(self):
        from test_maintenance_git import MaintenanceGitTests
        fixture = MaintenanceGitTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        row = {**fixture.record(), 'task': 'rebase'}
        engine = fixture.engine()
        prepared = engine.prepare(row)
        agent = MaintenanceAgent(fixture.root / 'ledger', {'repo': 'o/r'}, object(), engine=engine)
        with self.assertRaisesRegex(ValueError, 'erased'):
            agent._validate_candidate(row, {**prepared, 'new_head': row['main_sha']}, {})
        checkout = Path(prepared['checkout'])
        engine.git(['checkout', '--detach', row['main_sha']], checkout)
        engine.git(['merge', '--no-ff', '--no-edit', row['head_sha']], checkout)
        merge = engine.git(['rev-parse', 'HEAD'], checkout)
        with self.assertRaisesRegex(ValueError, 'linear rebase'):
            agent._validate_candidate(row, {**prepared, 'new_head': merge}, {})

    def test_real_candidate_bundle_push_and_repair_pr_are_reconciled(self):
        self.run_lifecycle(FixtureWorker)

    def test_protected_main_repair_is_retained_until_exact_source_authorization(self):
        self.run_lifecycle(ProtectedFixtureWorker)

    @unittest.skipUnless(os.environ.get('AGENTOPS_DOCKER_TESTS') == '1', 'Opt-in real Docker fixture')
    def test_real_docker_harness_to_candidate_push_and_repair_pr(self):
        self.run_lifecycle(DockerFixtureWorker)

    def run_lifecycle(self, worker_type):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            remote = root / 'remote'
            remote.mkdir()
            git(remote, 'init', '-b', 'main')
            git(remote, 'config', 'user.name', 'Test')
            git(remote, 'config', 'user.email', 'test@example.com')
            (remote / 'answer.py').write_text('value = 0\n')
            git(remote, 'add', '.')
            git(remote, 'commit', '-m', 'broken main')
            main = git(remote, 'rev-parse', 'HEAD')
            class Github:
                repo = 'o/r'
                prs = []
                writes = 0
                def main_sha(self):
                    return git(remote, 'rev-parse', 'main')
                def pages(self, endpoint, key=None):
                    return self.prs
                def request(self, method, endpoint, payload=None):
                    self.writes += 1
                    pr = {'number': 99, 'html_url': 'https://github.com/o/r/pull/99', 'state': 'open',
                          'body': payload['body'], 'base': {'ref': payload['base']},
                          'head': {'ref': payload['head'], 'sha': git(remote, 'rev-parse', payload['head']),
                                   'repo': {'full_name': self.repo}}}
                    self.prs.append(pr)
                    return pr
            gh = Github()
            worker = worker_type(root / 'workers', 'o/r')
            engine = MaintenanceGit(root / 'publications', 'o/r', remote=str(remote))
            if worker_type is not DockerFixtureWorker:
                def validate_fixture(snapshot, commands):
                    receipts = []
                    for argv in commands:
                        parsed = shlex.split(argv[2])
                        self.assertEqual(parsed[:2], ['python3', '-c'])
                        environment = {k: v for k, v in os.environ.items()
                                       if k.upper() in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
                        checked = subprocess.run([sys.executable, *parsed[1:]], cwd=snapshot,
                            env=environment, capture_output=True, text=True, timeout=10)
                        receipts.append({'argv': argv, 'exit_code': checked.returncode})
                    return {'state': 'passed' if all(r['exit_code'] == 0 for r in receipts) else 'failed',
                            'commands': receipts, 'image': 'trusted fixture interpreter'}
                engine.validator = validate_fixture
            agent = MaintenanceAgent(root / 'ledger', {'repo': 'o/r'}, gh, worker=worker, engine=engine)
            record = {'task': 'main', 'main_sha': main, 'head_sha': main, 'failures': []}
            saved = {}
            with patch.dict('os.environ', {'OPENAI_API_KEY': 'fixture-model-credential'}):
                if worker_type is ProtectedFixtureWorker:
                    from agentops.maintenance_agent import SourcePublicationHeld
                    with self.assertRaises(SourcePublicationHeld):
                        agent.execute(record, saved.update)
                    self.assertEqual(gh.writes, 0)
                    record = {**record, **saved, 'source_authorization': {'candidate': saved['candidate'],
                                'source_manifest': saved['source_manifest']}}
                result = agent.execute(record, saved.update)
            self.assertEqual(result['pr']['number'], 99)
            self.assertEqual(git(remote, 'rev-parse', 'main'), main)
            branch = result['branch']
            self.assertEqual(git(remote, 'show', branch + ':answer.py'), 'value = 1')
            self.assertEqual(gh.writes, 1)
            self.assertEqual(agent.execute(record, saved.update)['pr']['number'], 99)
            self.assertEqual(worker.starts, 1)
            self.assertFalse(list((Path(saved['handle']['directory']) / 'auth').iterdir()))
            if worker_type is DockerFixtureWorker:
                worker.cleanup(saved['handle'])
