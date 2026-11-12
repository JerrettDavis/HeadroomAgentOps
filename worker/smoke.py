"""Run a tiny real harness contract check without GitHub publication.

Usage: python worker/smoke.py codex --auth-root PATH
The stdout contains only process identity; inspect output after it exits.
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentops.model import canonical
from agentops.process import command
from agentops.worker import auth_plan, container_command, verify_copilot_scopes, private_job_directory, stage_model_auth


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('harness', choices=['codex', 'claude', 'copilot'])
    parser.add_argument('--auth-root')
    parser.add_argument('--model')
    args = parser.parse_args()
    session = str(uuid.uuid4())
    directory = Path('.agentops/smoke', session).resolve()
    directory.parent.mkdir(parents=True, exist_ok=True)
    private_job_directory(directory)
    for name in ('repo', 'input', 'output', 'auth'):
        (directory / name).mkdir()
    command(['git', 'init', str(directory / 'repo')])
    assignment = {'schema': 1, 'session_id': session, 'number': 1, 'head_sha': 'b' * 40,
                  'main_sha': 'a' * 40, 'harness': args.harness, 'model': args.model, 'timeout_seconds': 120}
    expected = {**{k: assignment[k] for k in ('schema', 'session_id', 'number', 'head_sha', 'main_sha')},
                'outcome': 'held', 'summary': 'Harness contract smoke check.', 'findings': [],
                'evidence': [], 'notices': [], 'actions': [], 'coverage': {'files': [], 'threads': []}}
    assignment['prompt'] = ('This is a tiny harness contract smoke check. Do not inspect any files or '
                            'use tools. Return exactly this JSON object and nothing else: ' + canonical(expected))
    (directory / 'input' / 'assignment.json').write_text(canonical(assignment), encoding='utf8')
    scopes = (verify_copilot_scopes(os.environ) if args.harness == 'copilot' and
              not os.environ.get('COPILOT_PROVIDER_BASE_URL') else None)
    auth = auth_plan(args.harness, os.environ, args.auth_root, scopes)
    stage_model_auth(directory, auth)
    image = command(['docker', 'image', 'inspect', 'headroom-agentops-worker:local', '--format', '{{.Id}}'])
    identity = command(container_command(directory, session, image))
    command(['docker', 'start', identity])
    print(json.dumps({'session_id': session, 'container': identity, 'directory': str(directory), 'image': image}))


if __name__ == '__main__':
    main()
