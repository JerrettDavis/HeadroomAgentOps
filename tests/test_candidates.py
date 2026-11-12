import tempfile
import unittest
import json
import uuid
import base64
from pathlib import Path

from agentops.candidates import CandidatePublisher, changed_paths
from agentops.process import command
from agentops.evidence import Evidence


class CandidateTests(unittest.TestCase):
    def test_actual_bundle_import_and_leased_fast_forward_push_preserve_author_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, remote = root / 'worker', root / 'remote.git'
            command(['git', 'init', '-b', 'main', str(work)])
            command(['git', 'config', 'user.name', 'Fixture'], cwd=work)
            command(['git', 'config', 'user.email', 'fixture@example.test'], cwd=work)
            (work / 'source.py').write_text('value = 1\n')
            command(['git', 'add', 'source.py'], cwd=work)
            command(['git', 'commit', '-m', 'baseline'], cwd=work)
            main = command(['git', 'rev-parse', 'HEAD'], cwd=work)
            command(['git', 'checkout', '-b', 'author-fix'], cwd=work)
            (work / 'source.py').write_text('value = 2\n')
            command(['git', 'commit', '-am', 'author intent'], cwd=work)
            original = command(['git', 'rev-parse', 'HEAD'], cwd=work)
            command(['git', 'clone', '--bare', str(work), str(remote)])
            command(['git', 'checkout', '-b', 'agentops-candidate'], cwd=work)
            (work / 'test_source.py').write_text('assert 2 == 2\n')
            command(['git', 'add', 'test_source.py'], cwd=work)
            command(['git', 'commit', '-m', 'maintainer test'], cwd=work)
            candidate = command(['git', 'rev-parse', 'HEAD'], cwd=work)
            job_root = root / 'job'
            (job_root / 'output').mkdir(parents=True)
            bundle = job_root / 'output' / 'candidate.bundle'
            command(['git', 'bundle', 'create', str(bundle), original + '..refs/heads/agentops-candidate'], cwd=work)
            session = str(uuid.uuid4())
            late_change = {}
            def runner(args, **kwargs):
                if args[:2] == ['docker', 'inspect']:
                    return json.dumps([{'State': {'Running': False},
                                        'Config': {'Labels': {'agentops.session': session}}}])
                # Substitute only the external network boundary; all Git operations are real.
                return command([str(remote) if a == 'https://github.com/o/r.git' else a for a in args], **kwargs)
            class GitHub:
                def pull(self, number):
                    return {'head_repo': 'o/r', 'head_ref': 'author-fix', **late_change,
                            'head_sha': command(['git', '--git-dir', str(remote), 'rev-parse', 'refs/heads/author-fix'])}
                def main_sha(self):
                    return main
            publisher = CandidatePublisher(root / 'trusted', GitHub(), runner)
            job = {'id': 'job', 'repo': 'o/r', 'number': 1, 'head_sha': original, 'main_sha': main,
                   'handle': {'directory': str(job_root), 'container': 'owned', 'session_id': session}}
            current = {'head_repo': 'o/r', 'head_ref': 'author-fix'}
            late_change['base_ref'] = 'retargeted'
            with self.assertRaisesRegex(ValueError, 'changed'):
                publisher(job, current, {'candidate_sha': candidate})
            self.assertEqual(command(['git', '--git-dir', str(remote), 'rev-parse', 'refs/heads/author-fix']), original)
            late_change.clear()
            evidence = Evidence(root / 'evidence')
            archive = evidence.put({'job_id': job['id'], 'session_id': session,
                                    'head_sha': original, 'main_sha': main,
                                    'bundle_base64': base64.b64encode(bundle.read_bytes()).decode('ascii')})
            job['result'] = {'candidate_archive': archive}
            publisher.evidence = evidence
            bundle.unlink()
            reused_job = {**job, 'id': 'reuse-job', 'handle': None, 'result': {
                'candidate_archive': archive, 'reused_from': 'job', 'candidate_archive_source_job': 'job'}}
            with self.assertRaisesRegex(ValueError, 'identity'):
                publisher({**reused_job, 'result': {**reused_job['result'],
                    'candidate_archive_source_job': 'unrelated-job'}}, current, {'candidate_sha': candidate})
            second_hop = {**reused_job, 'id': 'reuse-job-2',
                          'result': {**reused_job['result'], 'reused_from': 'reuse-job'}}
            publisher(second_hop, current, {'candidate_sha': candidate})
            published = command(['git', '--git-dir', str(remote), 'rev-parse', 'refs/heads/author-fix'])
            self.assertEqual(published, candidate)
            command(['git', '--git-dir', str(remote), 'merge-base', '--is-ancestor', original, published])
            self.assertEqual(command(['git', '--git-dir', str(remote), 'show', 'refs/heads/author-fix:source.py']), 'value = 2')
            protected = work / '.github' / 'workflows'
            protected.mkdir(parents=True)
            (protected / 'café.yml').write_text('on: pull_request\n', encoding='utf8')
            command(['git', 'add', '.github'], cwd=work)
            command(['git', 'commit', '-m', 'protected unicode path'], cwd=work)
            unsafe_candidate = command(['git', 'rev-parse', 'HEAD'], cwd=work)
            command(['git', 'bundle', 'create', str(bundle), candidate + '..refs/heads/agentops-candidate'], cwd=work)
            with self.assertRaisesRegex(ValueError, 'provenance'):
                publisher({**job, 'head_sha': candidate, 'result': {}}, current, {'candidate_sha': unsafe_candidate})
            self.assertEqual(command(['git', '--git-dir', str(remote), 'rev-parse', 'refs/heads/author-fix']), candidate)

    def test_unicode_and_newline_workflow_paths_cannot_bypass_provenance_gate(self):
        self.assertEqual(changed_paths('.github/workflows/café.yml\0tests/line\nbreak.py\0'),
                         ['.github/workflows/café.yml', 'tests/line\nbreak.py'])
    def test_candidate_bundle_must_be_regular_job_owned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = CandidatePublisher(root / 'trusted', None)
            job = {'id': 'job', 'head_sha': 'b' * 40, 'main_sha': 'a' * 40,
                   'handle': {'directory': str(root / 'job'), 'session_id': 'session'}}
            with self.assertRaisesRegex(ValueError, 'bundle'):
                publisher.bundle_path(job)

    def test_worker_git_configuration_is_never_used_by_host_publisher(self):
        import os
        seen = []
        def runner(args, **kwargs):
            seen.append((args, kwargs))
            return ''
        with tempfile.TemporaryDirectory() as directory:
            publisher = CandidatePublisher(directory, None, runner=runner)
            publisher.git(['init', '--bare', 'trusted'])
            self.assertEqual(seen[0][1]['env']['GIT_CONFIG_GLOBAL'], os.devnull)
            self.assertIn('core.hooksPath=' + os.devnull, seen[0][0])
            self.assertIn('credential.helper=', seen[0][0])

    def test_candidate_identity_cannot_inject_a_git_option(self):
        with tempfile.TemporaryDirectory() as directory:
            publisher = CandidatePublisher(directory, None)
            with self.assertRaises(ValueError):
                publisher({}, {}, {'candidate_sha': '--upload-pack=malicious'})


if __name__ == '__main__':
    unittest.main()
