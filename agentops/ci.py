"""Only exact, observed workflow runs may be approved or rerun by the maintainer."""

import re


class CIHandler:
    def __init__(self, github):
        self.github = github

    def reconcile(self, job, current, action, before):
        run = next((r for r in current.get('workflows', []) if r['id'] == action.get('workflow_id')), None)
        if not run or run.get('head_sha') != job['head_sha'] or not before:
            return None
        if action.get('event') == 'RERUN' and run.get('run_attempt', 1) > before.get('run_attempt', 1):
            return {'run_id': run['id'], 'run_attempt': run['run_attempt'], 'reconciled': True}
        if (action.get('event') == 'APPROVE_WORKFLOW' and before.get('status') in
                ('action_required', 'requested', 'waiting') and run.get('status') in
                ('queued', 'in_progress', 'completed')):
            return {'run_id': run['id'], 'reconciled': True}
        return None

    def __call__(self, job, current, action):
        run_id = action.get('workflow_id')
        if type(run_id) is not int or run_id <= 0:
            raise ValueError('CI action requires an observed positive run ID')
        run = next((r for r in current.get('workflows', []) if r['id'] == run_id), None)
        if not run or run.get('head_sha') != job['head_sha']:
            raise ValueError('CI run is not bound to the exact reviewed head')
        path = run.get('path', '').split('@', 1)[0]
        if not any(item['path'] == path for item in current.get('workflow_manifest', [])):
            raise ValueError('CI execution source is not in the reviewed manifest')
        target = run.get('source_repo', job['repo'])
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', target):
            raise ValueError('Invalid observed workflow repository')
        event = action.get('event')
        if event == 'RERUN' and run.get('status') == 'completed':
            endpoint = 'rerun'
        elif event == 'APPROVE_WORKFLOW' and run.get('status') in ('action_required', 'requested', 'waiting'):
            endpoint = 'approve'
        else:
            raise ValueError('CI state does not support the requested action')
        return self.github.request('POST', f'repos/{target}/actions/runs/{run_id}/{endpoint}')
