import unittest


class FakeGithub:
    repo = 'o/r'

    def main_sha(self):
        return 'main'

    def pages(self, endpoint, key=None):
        if '/pulls?' in endpoint:
            return [{'number': n, 'title': f'PR {n}', 'draft': True, 'user': {'login': 'excluded'},
                     'head': {'sha': f'h{n}', 'ref': f'topic{n}', 'repo': {'full_name': 'fork/r'}},
                     'base': {'ref': 'main'}, 'html_url': f'https://github.com/o/r/pull/{n}'}
                    for n in (1, 2, 3)]
        return []

    def request(self, method, endpoint, payload=None):
        if '/compare/h1...main' in endpoint:
            return {'ahead_by': 3}
        if '/compare/h2...main' in endpoint:
            return {'ahead_by': 0}
        raise RuntimeError('API unavailable')


class MaintenanceGithubTests(unittest.TestCase):
    def test_all_open_main_prs_include_drafts_forks_and_unknowns(self):
        from agentops.maintenance_github import drift_inventory
        inventory = drift_inventory(FakeGithub())
        self.assertEqual([p['number'] for p in inventory['prs']], [1, 3])
        self.assertEqual(inventory['prs'][0]['behind'], 3)
        self.assertIsNone(inventory['prs'][1]['behind'])
        self.assertEqual(inventory['prs'][0]['head_repo'], 'fork/r')

    def test_latest_attempt_only_and_non_main_ignored(self):
        from agentops.maintenance_github import main_health
        runs = [{'id': 1, 'workflow_id': 10, 'head_sha': 'main', 'head_branch': 'main',
                 'run_number': 1, 'run_attempt': 1, 'status': 'completed', 'conclusion': 'failure'},
                {'id': 1, 'workflow_id': 10, 'head_sha': 'main', 'head_branch': 'main',
                 'run_number': 1, 'run_attempt': 2, 'status': 'completed', 'conclusion': 'success'},
                {'id': 2, 'workflow_id': 11, 'head_sha': 'other', 'head_branch': 'main',
                 'status': 'completed', 'conclusion': 'failure'}]
        for run in runs:
            run.setdefault('event', 'push')
        self.assertEqual(main_health('main', runs)['state'], 'healthy')
        runs.append({'id': 3, 'workflow_id': 12, 'head_sha': 'main', 'head_branch': 'main',
                     'event': 'push', 'status': 'completed', 'conclusion': 'failure'})
        self.assertEqual(main_health('main', runs)['state'], 'broken')
        self.assertEqual(main_health('new-main', runs)['state'], 'pending')

    def test_pull_request_run_cannot_mask_failing_main_push(self):
        from agentops.maintenance_github import main_health
        runs = [{'id': 1, 'workflow_id': 10, 'head_sha': 'main', 'head_branch': 'main',
                 'event': 'push', 'status': 'completed', 'conclusion': 'failure'},
                {'id': 2, 'workflow_id': 10, 'head_sha': 'main', 'head_branch': 'main',
                 'event': 'pull_request', 'status': 'completed', 'conclusion': 'success'}]
        self.assertEqual(main_health('main', runs)['state'], 'broken')


if __name__ == '__main__':
    unittest.main()
