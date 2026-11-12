"""Import worker bundles into a trusted bare repo before a leased fast-forward push."""

import os
import base64
import uuid
import re
from pathlib import Path

from .process import command
from .policy import revision_gate


def changed_paths(output):
    return [path for path in output.split('\0') if path]


class CandidatePublisher:
    def __init__(self, root, github, runner=command, evidence=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.github, self.runner = github, runner
        self.evidence = evidence

    def git(self, args, cwd=None, authenticated=False):
        env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
               'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'}
        prefix = ['git', '--no-replace-objects', '-c', 'core.hooksPath=' + os.devnull,
                  '-c', 'core.fsmonitor=false', '-c', 'credential.helper=',
                  # Disposable repositories must not leave detached writers
                  # racing the coordinator or temporary-directory cleanup.
                  '-c', 'maintenance.auto=false', '-c', 'gc.auto=0']
        if authenticated:
            prefix += ['-c', 'credential.helper=!gh auth git-credential']
        return self.runner([*prefix, *args], cwd=cwd, env=env, timeout=600)

    @staticmethod
    def bundle_path(job):
        directory = Path(job['handle']['directory']).resolve()
        output = directory / 'output'
        bundle = output / 'candidate.bundle'
        if not bundle.is_file() or bundle.is_symlink() or bundle.resolve().parent != output.resolve():
            raise ValueError('Candidate bundle must be a regular job-owned file')
        if output.resolve().parent != directory:
            raise ValueError('Candidate bundle output path escaped job directory')
        return bundle

    def reconcile(self, job, current, action):
        if current['head_sha'] == action['candidate_sha']:
            return {'candidate_sha': action['candidate_sha'], 'reconciled': True}
        return None

    def changed_files(self, directory, before, after):
        return changed_paths(self.git(['diff', '--name-only', '-z', before, after], directory))

    def __call__(self, job, current, action):
        candidate = action.get('candidate_sha', '')
        if not re.fullmatch('[a-f0-9]{40}', candidate):
            raise ValueError('Candidate SHA must be a full commit identity')
        handle = job.get('handle')
        import json
        result = job.get('result') or {}
        archived = result.get('candidate_archive')
        if archived and self.evidence:
            record = self.evidence.get(archived['sha256'])
            source_job = result.get('candidate_archive_source_job') or job['id']
            reused = source_job != job['id'] and bool(result.get('reused_from'))
            if (any(record.get(key) != job[key] for key in ('head_sha', 'main_sha')) or
                    record.get('job_id') != source_job or
                    (not reused and (not handle or record.get('session_id') != handle['session_id']))):
                raise ValueError('Candidate archive identity mismatch')
            uuid.UUID(record['session_id'])
            bundle = self.root / (job['id'] + '.bundle')
            if bundle.is_symlink() or bundle.resolve().parent != self.root:
                raise ValueError('Candidate archive output escaped trusted root')
            bundle.write_bytes(base64.b64decode(record['bundle_base64'], validate=True))
        else:
            if not handle:
                raise ValueError('Candidate has no recorded worker handle')
            observation = self.runner(['docker', 'inspect', handle['container']])
            container = json.loads(observation)[0]
            if (container['State']['Running'] or container['Config']['Labels'].get('agentops.session') != handle['session_id']):
                raise ValueError('Candidate worker must be terminal with verified ownership')
            bundle = self.bundle_path(job)
        directory = self.root / job['id']
        if directory.resolve().parent != self.root:
            raise ValueError('Invalid trusted repository path')
        directory.mkdir(exist_ok=True)
        self.git(['init', '--bare', str(directory)])
        for field in ('head_repo',):
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', current.get(field) or ''):
                raise ValueError('Missing or invalid candidate target repository')
        self.git(['check-ref-format', 'refs/heads/' + current['head_ref']], directory)
        self.git(['fetch', '--no-tags', f'https://github.com/{job["repo"]}.git',
                  job['head_sha'], job['main_sha']], directory)
        self.git(['bundle', 'verify', str(bundle)], directory)
        self.git(['fetch', str(bundle), 'refs/heads/agentops-candidate:refs/heads/agentops-candidate'], directory)
        actual = self.git(['rev-parse', 'refs/heads/agentops-candidate'], directory)
        if actual != candidate:
            raise ValueError('Bundle candidate does not match review intent')
        self.git(['merge-base', '--is-ancestor', job['head_sha'], candidate], directory)
        # Preserve original author history; a force push can never authorize rewriting it.
        paths = self.changed_files(directory, job['head_sha'], candidate)
        if any(p.startswith(('.github/', '.gitmodules')) for p in paths):
            raise ValueError('Workflow/tooling publication requires separately verified source provenance')
        # Recheck immediately before the write, after potentially long bundle verification.
        live = self.github.pull(job['number'])
        expected = {**current, 'head_sha': job['head_sha']}
        if revision_gate(expected, live, job['main_sha'], self.github.main_sha()):
            raise ValueError('Candidate inputs changed before push')
        self.git(['push', '--porcelain',
                  '--force-with-lease=refs/heads/' + current['head_ref'] + ':' + job['head_sha'],
                  f'https://github.com/{current["head_repo"]}.git',
                  candidate + ':refs/heads/' + current['head_ref']], directory, authenticated=True)
        return {'candidate_sha': candidate, 'head_repo': current['head_repo'],
                'head_ref': current['head_ref'], 'requires_requalification': True}
