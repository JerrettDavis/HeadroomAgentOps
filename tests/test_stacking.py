"""Stack membership is a deterministic graph problem, never a model decision."""
import tempfile
import unittest
from pathlib import Path

from agentops.stacking import StackStore, plan_stack


def inventory():
    def pr(n, **extra):
        return dict(number=n, title=f'Change {n}', body='', author='contributor',
                    head_sha=str(n) * 40, head_ref=f'pr-{n}', head_repo='owner/repo',
                    base_ref='main', base_sha='a' * 40, draft=False, ci='passed',
                    up_to_date=True, review_decision='APPROVED', mergeable='MERGEABLE',
                    merge_state='CLEAN', **extra)
    return {'repo': 'owner/repo', 'default_branch': 'main', 'default_sha': 'a' * 40,
            'prs': [pr(3), pr(2), pr(1)], 'resolved_dependencies': []}


class PlanningTests(unittest.TestCase):
    def test_dependency_closure_and_stable_order(self):
        data = inventory()
        data['prs'][0]['body'] = 'Depends on #1'
        a = plan_stack(data, [3, 2])
        b = plan_stack(data, [2, 3])
        self.assertEqual([p['number'] for p in a['members']], [1, 2, 3])
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(a['blockers'], [])
        self.assertEqual(a['included_prerequisites'], [1])
        data['prs'][0]['head_sha'] = 'b' * 40
        self.assertNotEqual(a['id'], plan_stack(data, [2, 3])['id'])

    def test_branch_parent_and_cycle_fail_closed(self):
        data = inventory()
        data['prs'][0].update(base_ref='pr-1', base_sha='1' * 40)
        self.assertEqual([p['number'] for p in plan_stack(data, [3])['members']], [1, 3])
        data['prs'][2]['body'] = 'Depends on #3'
        self.assertTrue(any('cycle' in b.lower() for b in plan_stack(data, [3])['blockers']))

    def test_missing_dependencies_and_gates_explained(self):
        for field, value in [('draft', True), ('ci', 'unknown'),
                             ('review_decision', 'CHANGES_REQUESTED'),
                             ('up_to_date', None), ('mergeable', 'UNKNOWN')]:
            with self.subTest(field=field):
                data = inventory()
                data['prs'][0][field] = value
                self.assertTrue(plan_stack(data, [3])['blockers'])
        data = inventory()
        data['prs'][0]['body'] = 'Requires #19'
        self.assertIn('#19', ' '.join(plan_stack(data, [3])['blockers']))
        data['resolved_dependencies'] = [{'repo': 'owner/repo', 'number': 19, 'state': 'merged'}]
        self.assertEqual(plan_stack(data, [3])['blockers'], [])

    def test_invalid_selection_and_duplicate_inventory_rejected(self):
        for numbers in [[], [True], ['1'], [0], [99], list(range(1, 102))]:
            with self.subTest(numbers=numbers), self.assertRaises(ValueError):
                plan_stack(inventory(), numbers)
        data = inventory()
        data['prs'].append(data['prs'][0])
        with self.assertRaises(ValueError):
            plan_stack(data, [1])


class StoreTests(unittest.TestCase):
    def test_persisted_identity_and_compare_and_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'ledger.sqlite3'
            store = StackStore(path)
            plan = plan_stack(inventory(), [1, 2])
            first = store.create(plan)
            self.assertEqual(store.create(plan)['id'], first['id'])
            self.assertTrue(store.claim(first['id'], 'owner', ['planned']))
            self.assertFalse(store.claim(first['id'], 'other', ['planned']))
            store.update(first['id'], {'state': 'prepared', 'revision': 'd' * 40}, owner='owner')
            reopened = StackStore(path)
            self.assertEqual(reopened.get(first['id'])['revision'], 'd' * 40)
            self.assertEqual(len(reopened.list()), 1)
            with self.assertRaises(ValueError):
                store.update(first['id'], {'state': 'published'}, owner='other')


if __name__ == '__main__':
    unittest.main()
