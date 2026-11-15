import unittest
from agentops.disposition import determination


class DeterminationTests(unittest.TestCase):
    def test_qualified_ready_result_proposes_approval(self):
        self.assertEqual(determination({'outcome': 'ready', 'publication_qualification': 'ready'},
                                       'contributor', 'maintainer')['event'], 'APPROVE')

    def test_unresolved_important_findings_request_changes(self):
        action = determination({'outcome': 'held', 'findings': [
            {'severity': 'important', 'path': 'a.py', 'line': 2, 'description': 'Wrong output',
             'evidence': 'Unit test fails'}]}, 'contributor', 'maintainer')
        self.assertEqual(action['event'], 'REQUEST_CHANGES')
        self.assertIn('Wrong output', action['body'])

    def test_missing_qualification_is_visible_without_approval(self):
        action = determination({'outcome': 'held', 'qualification_limits': ['Passing tests missing']},
                               'contributor', 'maintainer')
        self.assertEqual(action['event'], 'COMMENT')
        self.assertIn('Passing tests missing', action['body'])

    def test_own_prs_receive_comment_instead_of_changes_request(self):
        for author in ('maintainer',):
            action = determination({'outcome': 'held', 'findings': [{'severity': 'important',
                'path': 'a', 'line': 1, 'description': 'Bug', 'evidence': 'Static proof'}]}, author, 'maintainer')
            self.assertEqual(action['event'], 'COMMENT')

    def test_dependabot_regressions_need_maintainer_fix_without_changes_request(self):
        action = determination({'outcome':'held','findings':[{'severity':'important',
            'path':'package.json','line':1,'description':'Verified compatibility regression'}]},
            'dependabot[bot]', 'maintainer')
        self.assertEqual(action['event'], 'COMMENT')
        self.assertIn('maintainer fix', action['body'])


if __name__ == '__main__':
    unittest.main()
