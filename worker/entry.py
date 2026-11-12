"""Container-only entrypoint; no host configuration is loaded."""

import json
import hashlib
import copy
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agentops.harness import Harness, RESULT_SCHEMA, validate_result, usage_from_events, response_from_events
from agentops.redaction import collect_secrets, redact


def run():
    assignment = json.loads(Path('/job/input/assignment.json').read_text())
    output = Path('/job/output')
    strict_schema = copy.deepcopy(RESULT_SCHEMA)
    def make_strict(schema):
        if schema.get('type') == 'object':
            schema['required'] = list(schema.get('properties', {}))
            for child in schema.get('properties', {}).values():
                make_strict(child)
        elif schema.get('type') == 'array':
            make_strict(schema['items'])
    make_strict(strict_schema)
    (output / 'schema.json').write_text(json.dumps(strict_schema))
    env = {**os.environ, **json.loads(Path('/job/auth/model-env.json').read_text())}
    for name, destination in [('auth.json', Path('/home/node/.codex/auth.json')),
                               ('.credentials.json', Path('/home/node/.claude/.credentials.json'))]:
        source = Path('/job/auth') / name
        if source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
    args = Harness(assignment['harness'], assignment.get('model')).command(assignment)
    started = time.time()
    version = subprocess.run([args[0], '--version'], capture_output=True, text=True)
    with (output / 'session.jsonl').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
        try:
            completed = subprocess.run(args, stdout=stdout, stderr=stderr, env=env,
                                       timeout=assignment.get('timeout_seconds', 3600))
        except subprocess.TimeoutExpired:
            completed = subprocess.CompletedProcess(args, 124)
            stderr.write('Worker session time budget exhausted.\n')
    auth_paths = [*Path('/job/auth').glob('*.json'), Path('/home/node/.codex/auth.json'),
                  Path('/home/node/.claude/.credentials.json')]
    secrets = collect_secrets(auth_paths)
    for filename in ('session.jsonl', 'stderr.log', 'result.json', 'final.json'):
        retained = output / filename
        if retained.is_symlink():
            raise ValueError('Worker output may not be a symbolic link')
        if retained.is_file():
            retained.write_text(redact(retained.read_text(), secrets))
    events = []
    for line in (output / 'session.jsonl').read_text().splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            continue
    usage = usage_from_events(assignment['harness'], events)
    if assignment['harness'] == 'copilot' and (output / 'usage.json').is_file():
        usage = json.loads((output / 'usage.json').read_text())
    (output / 'execution.json').write_text(json.dumps({
        'harness': assignment['harness'], 'version': version.stdout.strip(),
        'model': assignment.get('model'), 'exit_code': completed.returncode,
        'duration_seconds': time.time() - started, 'usage': usage,
        'prompt_sha256': hashlib.sha256(assignment['prompt'].encode()).hexdigest(),
        'packet_bytes': Path('/job/input/packet.json').stat().st_size if Path('/job/input/packet.json').is_file() else 0,
        'retrieval_bytes_measured': None}))
    if completed.returncode:
        return completed.returncode
    target = output / 'result.json'
    if not target.exists():
        candidate = output / 'final.json'
        if candidate.exists():
            data = json.loads(candidate.read_text())
        else:
            data = response_from_events(assignment['harness'], events)
        validate_result(data, assignment)
        target.write_text(json.dumps(data))
    validate_result(json.loads(target.read_text()), assignment)
    return completed.returncode


def export_candidate(assignment, output):
    # Export in the sandbox. The host publisher never opens worker-controlled Git config.
    with (output / 'uncommitted.patch').open('w') as patch:
        subprocess.run(['git', '-c', 'safe.directory=/workspace', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                        'diff', '--no-ext-diff', '--binary'], stdout=patch, check=True)
    candidate = subprocess.run(['git', '-c', 'safe.directory=/workspace', 'rev-parse', '--verify',
                                'refs/heads/agentops-candidate'], capture_output=True, text=True)
    if candidate.returncode == 0 and candidate.stdout.strip() != assignment['head_sha']:
        temporary_bundle = output / 'candidate.bundle.tmp'
        subprocess.run(['git', '-c', 'safe.directory=/workspace', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                        'bundle', 'create', str(temporary_bundle),
                        assignment['head_sha'] + '..refs/heads/agentops-candidate'], check=True)
        subprocess.run(['git', '-c', 'safe.directory=/workspace', 'bundle', 'verify', str(temporary_bundle)], check=True)
        temporary_bundle.replace(output / 'candidate.bundle')
        receipt = output / 'candidate.json.tmp'
        receipt.write_text(json.dumps({'original_head': assignment['head_sha'],
            'candidate_sha': candidate.stdout.strip(), 'published': False,
            'bundle_sha256': hashlib.sha256((output / 'candidate.bundle').read_bytes()).hexdigest()}))
        receipt.replace(output / 'candidate.json')


def main():
    assignment = json.loads(Path('/job/input/assignment.json').read_text())
    try:
        return run()
    finally:
        export_candidate(assignment, Path('/job/output'))


if __name__ == '__main__':
    sys.exit(main())
