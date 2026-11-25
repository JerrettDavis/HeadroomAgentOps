"""Mechanical stack integration. Repository code runs only in a credential-free container."""
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from .process import command
from .redaction import redact


def bounded_process(args, timeout):
    """Drain output continuously, retaining only its tail rather than filling disk/RAM."""
    tail = bytearray()
    lock = threading.Lock()
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    def drain():
        while True:
            chunk = process.stdout.read(4096)
            if not chunk:
                break
            with lock:
                tail.extend(chunk)
                del tail[:-16000]
    reader = threading.Thread(target=drain, daemon=True, name='stack-validation-log')
    reader.start()
    timed_out, code = False, None
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=10)
    reader.join(timeout=5)
    if not reader.is_alive():
        process.stdout.close()
    with lock:
        log = redact(bytes(tail).decode('utf8', errors='replace'))
    return code, timed_out, log


def validate_commands(commands):
    if (not isinstance(commands, list) or len(commands) > 20 or
            any(not isinstance(argv, list) or not argv or len(argv) > 100 or
                any(not isinstance(a, str) or not a or '\x00' in a or len(a) > 4096 for a in argv)
                for argv in commands)):
        raise ValueError('Stack validation requires at most 20 nonempty argv arrays')
    return commands


class GitStack:
    def __init__(self, root, repo, *, remote=None, commands=None,
                 image='headroom-agentops-worker:local', timeout=900, validator=None):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
            raise ValueError('Invalid repository')
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise ValueError('Stack validation timeout must be 1..3600 seconds')
        self.root, self.repo = Path(root).resolve(), repo
        self.remote = remote or f'https://github.com/{repo}.git'
        self.commands = validate_commands([] if commands is None else commands)
        self.image, self.timeout = image, timeout
        self.validator = validator or self.validate

    def git(self, args, cwd, *, env=None):
        # Ignore inherited Git configuration, hooks, external merge drivers and filters.
        clean = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        clean.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                     GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
        clean.update(env or {})
        return command(['git', '-c', 'core.hooksPath=' + os.devnull,
                        '-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false',
                        '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                        *args], cwd=cwd, env=clean, timeout=600)

    def prepare(self, record, progress=None):
        if record.get('blockers'):
            raise ValueError('Cannot prepare a blocked stack')
        if not re.fullmatch('[a-f0-9]{64}', record['id']):
            raise ValueError('Invalid stack identity')
        directory = self.root / record['id'] / str(uuid.uuid4())
        checkout = directory / 'repo'
        checkout.mkdir(parents=True)
        progress = progress or (lambda data: None)
        self.git(['init', str(checkout)], directory)
        self.git(['check-ref-format', 'refs/heads/' + record['target_branch']], checkout)
        refs = [f'refs/heads/{record["target_branch"]}:refs/stack/target']
        refs += [f'refs/pull/{p["number"]}/head:refs/stack/source/{p["number"]}' for p in record['members']]
        progress({'phase': 'Fetching pinned inputs', 'checkout': str(checkout)})
        self.git(['fetch', '--no-tags', '--', self.remote, *refs], checkout)
        for ref, expected in [('refs/stack/target', record['target_sha'])] + [
                (f'refs/stack/source/{p["number"]}', p['head_sha']) for p in record['members']]:
            if self.git(['rev-parse', ref], checkout) != expected:
                raise ValueError('Source or target changed while fetching; prepare again')
        self.git(['checkout', '--detach', record['target_sha']], checkout)
        epoch = int(self.git(['show', '-s', '--format=%ct', record['target_sha']], checkout))
        integrated = []
        for index, p in enumerate(record['members']):
            progress({'phase': f'Integrating #{p["number"]}', 'integrated': integrated[:]})
            identity = {'GIT_AUTHOR_NAME': 'Headroom AgentOps', 'GIT_AUTHOR_EMAIL': 'agentops@users.noreply.github.com',
                        'GIT_COMMITTER_NAME': 'Headroom AgentOps', 'GIT_COMMITTER_EMAIL': 'agentops@users.noreply.github.com',
                        'GIT_AUTHOR_DATE': f'@{epoch + index + 1} +0000',
                        'GIT_COMMITTER_DATE': f'@{epoch + index + 1} +0000'}
            try:
                self.git(['merge', '--no-ff', '--no-edit', '-m',
                          f'Stack {self.repo}#{p["number"]} ({p["head_sha"]})', p['head_sha']], checkout, env=identity)
            except RuntimeError:
                conflicts = self.git(['diff', '--name-only', '--diff-filter=U', '-z'], checkout).split('\0')
                conflicts = [path for path in conflicts if path]
                if not conflicts:
                    raise
                self.git(['merge', '--abort'], checkout)
                return {'state': 'conflict', 'conflicting_pr': p['number'], 'conflicts': conflicts,
                        'integrated': integrated, 'checkout': str(checkout), 'phase': 'Conflicts require author changes'}
            integrated.append(p['number'])
        revision = self.git(['rev-parse', 'HEAD'], checkout)
        progress({'phase': 'Validating combined revision', 'revision': revision, 'integrated': integrated})
        validation = {'state': 'unconfigured', 'commands': []}
        if self.commands:
            validation_copy = directory / 'validation'
            self.git(['clone', '--no-local', '--no-hardlinks', '--', str(checkout), str(validation_copy)], directory)
            validation = self.validator(validation_copy, self.commands)
        return {'state': 'prepared' if validation['state'] in ('passed', 'unconfigured') else 'validation_failed',
                'revision': revision, 'checkout': str(checkout), 'integrated': integrated,
                'validation': validation, 'phase': 'Preparation finished'}

    def validate(self, checkout, commands):
        """Run bounded argv commands in the worker image without any auth or network."""
        image = command(['docker', 'image', 'inspect', self.image, '--format', '{{.Id}}'])
        if not re.fullmatch('sha256:[a-f0-9]{64}', image):
            raise ValueError('Validation image must resolve to immutable SHA')
        uid, gid = (os.getuid(), os.getgid()) if sys.platform.startswith('linux') else (1000, 1000)
        if uid == 0 or gid == 0:
            raise ValueError('Validation must run as a non-root user')
        if ',' in str(checkout):
            raise ValueError('Validation mount path cannot contain comma')
        results = []
        for argv in commands:
            name = 'agentops-stack-' + uuid.uuid4().hex
            args = ['docker', 'run', '--name', name, '--network', 'none', '--read-only',
                    '--cap-drop=ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '512',
                    '--memory', '8g', '--cpus', '2', '--user', f'{uid}:{gid}',
                    '--tmpfs', '/tmp:rw,nosuid,exec,size=1g',
                    '--tmpfs', f'/home/node:rw,nosuid,exec,uid={uid},gid={gid},size=1g',
                    '--env', 'HOME=/home/node', '--env', 'GIT_CONFIG_NOSYSTEM=1',
                    '--env', 'GIT_CONFIG_GLOBAL=/dev/null', '--workdir', '/workspace',
                    '--env', 'GIT_CONFIG_COUNT=1', '--env', 'GIT_CONFIG_KEY_0=safe.directory',
                    '--env', 'GIT_CONFIG_VALUE_0=/workspace',
                    '--mount', f'type=bind,src={checkout},dst=/workspace',
                    '--entrypoint', argv[0], image, *argv[1:]]
            try:
                code, timed_out, tail = bounded_process(args, self.timeout)
            finally:
                # A killed Docker client does not imply its container stopped.
                command(['docker', 'rm', '--force', name], timeout=60)
            results.append({'argv': argv, 'exit_code': code, 'timed_out': timed_out, 'log': tail})
            if code != 0:
                return {'state': 'failed', 'image': image, 'commands': results}
        return {'state': 'passed', 'image': image, 'commands': results}

    def publish_branch(self, record):
        checkout = Path(record['checkout']).resolve()
        if not checkout.is_relative_to(self.root) or not checkout.is_dir():
            raise ValueError('Prepared checkout unavailable; prepare again')
        if self.git(['rev-parse', 'HEAD'], checkout) != record['revision']:
            raise ValueError('Prepared revision changed')
        if self.git(['status', '--porcelain', '--untracked-files=all'], checkout):
            raise ValueError('Prepared checkout was modified')
        branch = 'agentops/stacks/' + record['id']
        # No force, no contributor refs, and no merge endpoint. Reconciliation is in the service.
        self.git(['push', '--porcelain', '--', self.remote,
                  f'{record["revision"]}:refs/heads/{branch}'], checkout)
        return branch
