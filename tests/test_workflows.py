import unittest

from agentops.workflows import expected_workflows, screen_source


class WorkflowTests(unittest.TestCase):
    def test_branch_and_path_filters_determine_required_actual_workflows(self):
        sources = {
            '.github/workflows/ci.yml': 'name: CI\non:\n  pull_request:\n    branches: [main]\n',
            '.github/workflows/node.yml': 'name: Node\non:\n  pull_request:\n    paths: ["plugins/**", "!plugins/docs/**"]\n',
            '.github/workflows/release.yml': 'name: Release\non:\n  push:\n    branches: [main]\n'}
        pr = {'base_ref': 'main', 'head_ref': 'fix', 'head_repo': 'fork/r', 'files': [{'filename': 'plugins/a.ts'}]}
        expected, unknown = expected_workflows(sources, pr, 'o/r')
        self.assertEqual(set(expected), {'.github/workflows/ci.yml', '.github/workflows/node.yml'})
        self.assertEqual(unknown, [])
        pr['base_ref'] = 'owner-stack'
        pr['files'] = [{'filename': 'plugins/docs/a.md'}]
        self.assertEqual(expected_workflows(sources, pr, 'o/r'), ([], []))

    def test_paths_ignore_does_not_skip_mixed_code_and_documentation_change(self):
        sources = {'.github/workflows/ci.yml': 'name: CI\non:\n  pull_request:\n    paths-ignore: ["docs/**"]\n'}
        pr = {'base_ref': 'main', 'head_repo': 'fork/r', 'head_ref': 'fix',
              'files': [{'filename': 'docs/a.md'}, {'filename': 'headroom/a.py'}]}
        self.assertEqual(expected_workflows(sources, pr, 'o/r')[0], ['.github/workflows/ci.yml'])

    def test_unknown_globs_or_expressions_require_review_instead_of_silent_skips(self):
        sources = {'.github/workflows/ci.yml': 'on:\n  pull_request:\n    paths: ["src/[ab].py"]\n'}
        pr = {'base_ref': 'main', 'head_repo': 'f/r', 'head_ref': 'fix', 'files': [{'filename': 'src/a.py'}]}
        self.assertTrue(expected_workflows(sources, pr, 'o/r')[1])

    def test_mutable_actions_and_downloaded_installers_are_execution_holds(self):
        unsafe = 'jobs:\n  test:\n    steps:\n      - uses: actions/checkout@main\n      - run: curl https://example.test/install.sh | sh\n'
        self.assertTrue(screen_source(unsafe))
        safe = 'jobs:\n  test:\n    steps:\n      - uses: actions/checkout@' + 'a' * 40 + '\n      - run: python -m unittest\n'
        self.assertEqual(screen_source(safe), [])


if __name__ == '__main__':
    unittest.main()
