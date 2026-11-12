"""Apply portable GitHub repository settings using the maintainer's gh login.

Dry-run by default. Explicit repository, GitHub context, current remote, then
repository.json determine the destination, so transfers do not require edits
to workflow files. This script never reads or publishes local AgentOps state.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', help='Override owner/name')
    parser.add_argument('--apply', action='store_true', help='Apply settings through gh api')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    settings = json.loads((root / '.github/repository.json').read_text())
    repository = args.repository or os.environ.get('GITHUB_REPOSITORY') or os.environ.get('GH_REPO')
    if not repository:
        probe = subprocess.run(['gh', 'repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner'],
                               cwd=root, capture_output=True, text=True)
        repository = probe.stdout.strip() if probe.returncode == 0 else settings['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        parser.error('Repository must be owner/name')
    base = f'repos/{repository}'
    reviewers = []
    for username in settings['release_reviewers']:
        if args.apply:
            result = subprocess.run(['gh', 'api', f'users/{username}', '--jq', '.id'],
                                    check=True, capture_output=True, text=True)
            reviewers.append({'type': 'User', 'id': int(result.stdout)})
        else:
            reviewers.append({'type': 'User', 'login': username})
    protection = {
        'required_status_checks': {'strict': True, 'contexts': settings['required_checks']},
        'enforce_admins': True,
        'required_pull_request_reviews': {
            'dismiss_stale_reviews': True,
            'required_approving_review_count': settings['required_approvals'],
        },
        'restrictions': None,
        'required_conversation_resolution': True,
        'allow_force_pushes': False,
        'allow_deletions': False,
    }
    operations = [
        ('PATCH', base, {'description': settings['description'],
                         'default_branch': settings['default_branch'],
                         'delete_branch_on_merge': True, 'allow_auto_merge': False,
                         'security_and_analysis': {
                             'secret_scanning': {'status': 'enabled'},
                             'secret_scanning_push_protection': {'status': 'enabled'},
                         }}),
        ('PUT', f'{base}/vulnerability-alerts', None),
        ('PUT', f'{base}/private-vulnerability-reporting', None),
        ('PUT', f'{base}/actions/permissions/workflow',
         {'default_workflow_permissions': 'read', 'can_approve_pull_request_reviews': False}),
        ('PUT', f'{base}/branches/{settings["default_branch"]}/protection', protection),
        ('PUT', f'{base}/environments/{settings["release_environment"]}',
         {'reviewers': reviewers, 'prevent_self_review': False,
          'deployment_branch_policy': {'protected_branches': False, 'custom_branch_policies': True}}),
        ('TAG_POLICY', f'{base}/environments/{settings["release_environment"]}/deployment-branch-policies',
         {'name': settings['release_tag_pattern'], 'type': 'tag'}),
    ]
    for label in json.loads((root / '.github/labels.json').read_text()):
        # Create-or-update is handled separately below, preserving unrelated labels.
        operations.append(('LABEL', base, label))
    for method, endpoint, body in operations:
        if not args.apply:
            print(json.dumps({'method': method, 'endpoint': endpoint, 'body': body}))
            continue
        if method == 'TAG_POLICY':
            result = subprocess.run(['gh', 'api', endpoint], check=True, capture_output=True, text=True)
            policies = json.loads(result.stdout)['branch_policies']
            if not any(item['name'] == body['name'] and item['type'] == body['type'] for item in policies):
                subprocess.run(['gh', 'api', '--method', 'POST', endpoint, '--input', '-'],
                               input=json.dumps(body), text=True, check=True, capture_output=True)
            print(f'Configured release tag policy on {repository}')
        elif method == 'LABEL':
            command = ['gh', 'label', 'create', body['name'], '--repo', repository,
                       '--color', body['color'], '--description', body['description'], '--force']
            subprocess.run(command, check=True)
        else:
            command = ['gh', 'api', '--method', method, endpoint]
            if body is not None:
                command += ['--input', '-']
            result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                                    text=True, capture_output=True)
            if result.returncode:
                raise SystemExit(f'{method} {endpoint}: {result.stderr}')
            print(f'Applied {method} {endpoint}')


if __name__ == '__main__':
    main()
