import unittest

from agentops.discussion import fingerprint
from agentops.model import digest


class DiscussionTests(unittest.TestCase):
    def test_only_exact_causal_publication_entries_are_ignored_for_freshness(self):
        original = {'body': 'intent', 'comments': [{'id': 1, 'body': 'Author context', 'user': {'login': 'author'}}],
                    'reviews': [], 'review_comments': [], 'threads': []}
        current = {**original, 'comments': [*original['comments'], {'id': 2, 'body': 'Our known feedback'}]}
        ignored = [{'kind': 'comment', 'id': 2, 'body_hash': digest('Our known feedback')}]
        self.assertEqual(fingerprint(original, ignored), fingerprint(current, ignored))
        current['comments'].append({'id': 3, 'body': 'New author evidence'})
        self.assertNotEqual(fingerprint(original, ignored), fingerprint(current, ignored))

    def test_editing_an_own_comment_or_dismissing_a_review_is_new_evidence(self):
        old = {'body': '', 'comments': [], 'reviews': [], 'review_comments': [], 'threads': []}
        ignored = [{'kind': 'comment', 'id': 2, 'body_hash': digest('Original body')},
                   {'kind': 'review', 'id': 3, 'body_hash': digest('Review body'), 'state': 'APPROVED'}]
        current = {**old, 'comments': [{'id': 2, 'body': 'Edited body'}]}
        self.assertNotEqual(fingerprint(old, ignored), fingerprint(current, ignored))
        current = {**old, 'reviews': [{'id': 3, 'body': 'Review body', 'state': 'DISMISSED'}]}
        self.assertNotEqual(fingerprint(old, ignored), fingerprint(current, ignored))

    def test_reactions_and_account_metadata_do_not_require_new_reasoning(self):
        before = {'body': 'intent', 'comments': [{'id': 1, 'body': 'context', 'user': {'login': 'author'}}],
                  'reviews': [], 'review_comments': [], 'threads': []}
        after = {**before, 'comments': [{**before['comments'][0], 'reactions': {'+1': 10}, 'updated_at': 'later'}]}
        self.assertEqual(fingerprint(before), fingerprint(after))


if __name__ == '__main__':
    unittest.main()
