import copy
import threading
import time
import unittest
import tempfile
from pathlib import Path
from urllib.request import urlopen
import json

from agentops.merge_priority import rank_prs, collect_priority, PriorityMonitor


REPO = 'owner/repo'


def pr(number, **changes):
    return {**dict(number=number, title='feat: something', body='', author='contributor',
                association='CONTRIBUTOR', labels=[], head_ref=f'branch-{number}',
                base_ref='main', head_repo=REPO, head_sha=str(number) * 40,
                base_sha='a' * 40, ci='passed', up_to_date=True, draft=False,
                mergeable='MERGEABLE', merge_state='CLEAN', review_decision=''), **changes}


class RankingTests(unittest.TestCase):
    def test_ready_category_order_and_maintainer_inclusion(self):
        rows = rank_prs(REPO, [pr(6), pr(5, title='fix: bug'),
            pr(4, labels=['fixes-main-ci']), pr(3, title='chore(deps): update'),
            pr(2, association='MEMBER'), pr(1, association='OWNER')])
        self.assertEqual([r['number'] for r in rows], [1, 2, 3, 4, 5, 6])
        self.assertTrue(all(r['ready'] for r in rows))
        self.assertEqual(rows[3]['category'], 'Main CI fix (declared)')

    def test_readiness_precedes_category_and_unknown_is_not_ready(self):
        rows = rank_prs(REPO, [pr(1, association='OWNER', draft=True),
            pr(2, association='MEMBER', ci='failed'), pr(3, up_to_date=False),
            pr(4, ci='unknown'), pr(5, up_to_date=None),
            pr(6, mergeable='UNKNOWN'), pr(7, mergeable='CONFLICTING'),
            pr(8, review_decision='CHANGES_REQUESTED'), pr(9)])
        self.assertEqual(rows[0]['number'], 9)
        self.assertEqual([r['number'] for r in rows if r['ready']], [9])
        self.assertTrue(all(r['blockers'] for r in rows[1:]))

    def test_stacks_are_topological_and_linked_instead_of_mention_based(self):
        rows = rank_prs(REPO, [pr(1, body='Related to #3'),
            pr(2, association='OWNER', base_ref='branch-1', base_sha='1' * 40),
            pr(3, body='Depends on #2')])
        self.assertEqual([r['number'] for r in rows], [1, 2, 3])
        self.assertEqual([r['depth'] for r in rows], [0, 1, 2])
        self.assertTrue(rows[0]['ready'])
        self.assertFalse(rows[1]['ready'])
        self.assertTrue(rows[1]['ready_after_dependencies'])
        self.assertEqual(rows[2]['dependencies'][0]['url'], 'https://github.com/owner/repo/pull/2')

    def test_fork_branches_do_not_create_false_dependencies(self):
        rows = rank_prs(REPO, [pr(1, head_repo='fork/repo'),
                               pr(2, base_ref='branch-1')])
        row = next(r for r in rows if r['number'] == 2)
        self.assertEqual(row['dependencies'], [])
        self.assertIn('Base is not the default branch', row['blockers'])

    def test_missing_external_closed_and_cycles_stay_visible(self):
        rows = rank_prs(REPO, [pr(1, body='Depends on #2'),
            pr(2, body='Depends on #1'), pr(3, body='Depends on #99'),
            pr(4, body='Depends on https://github.com/other/repo/pull/5')])
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(not r['ready'] for r in rows))
        self.assertTrue(all(not r['ready_after_dependencies'] for r in rows))
        self.assertTrue(any('cycle' in b.lower() for b in next(r for r in rows if r['number'] == 1)['blockers']))
        self.assertEqual(next(r for r in rows if r['number'] == 4)['dependencies'][0]['repo'], 'other/repo')

    def test_multiple_dependencies_and_blocked_parent(self):
        rows = rank_prs(REPO, [pr(3, body='Depends on #1, #2'),
                               pr(2, ci='pending'), pr(1)])
        child = next(r for r in rows if r['number'] == 3)
        self.assertEqual({d['number'] for d in child['dependencies']}, {1, 2})
        self.assertFalse(child['ready_after_dependencies'])
        self.assertLess(rows.index(next(r for r in rows if r['number'] == 2)), rows.index(child))

    def test_stack_child_does_not_jump_a_ready_maintainer(self):
        rows = rank_prs(REPO, [pr(1, association='OWNER'),
            pr(2, base_ref='branch-1', base_sha='1' * 40), pr(3, association='OWNER')])
        self.assertEqual([r['number'] for r in rows], [1, 3, 2])

    def test_ambiguous_branch_parents_require_resolution_not_both_prs(self):
        rows = rank_prs(REPO, [pr(1, head_ref='shared'),
            pr(2, head_ref='shared', base_ref='release'), pr(3, base_ref='shared')])
        row = next(r for r in rows if r['number'] == 3)
        self.assertFalse(row['ready_after_dependencies'])
        self.assertTrue(any('Ambiguous' in b for b in row['blockers']))
        self.assertEqual(row['dependencies'], [])

    def test_explicit_dependency_resolves_ambiguous_branch_parent(self):
        rows = rank_prs(REPO, [pr(1, head_ref='shared'),
            pr(2, head_ref='shared', base_ref='release'),
            pr(3, base_ref='shared', base_sha='1' * 40, body='Depends on #1')])
        row = next(r for r in rows if r['number'] == 3)
        self.assertEqual([d['number'] for d in row['dependencies']], [1])
        self.assertTrue(row['ready_after_dependencies'])

    def test_merged_dependency_is_satisfied_and_closed_dependency_blocks(self):
        rows = rank_prs(REPO, [pr(1, body='Depends on #99')],
                        resolved={(REPO, 99): 'merged'})
        self.assertTrue(rows[0]['ready'])
        rows = rank_prs(REPO, [pr(1, body='Depends on #99')],
                        resolved={(REPO, 99): 'closed'})
        self.assertFalse(rows[0]['ready'])

    def test_declarations_in_code_and_unrelated_main_mentions_do_not_classify(self):
        rows = rank_prs(REPO, [pr(1, body='```\nDepends on #3\n```\nWe mention main CI.'),
                               pr(2, title='fix: maintain main documentation')])
        self.assertEqual(next(r for r in rows if r['number'] == 1)['dependencies'], [])
        self.assertEqual(next(r for r in rows if r['number'] == 2)['category'], 'Fix')
        self.assertEqual(next(r for r in rows if r['number'] == 1)['category'], 'Feature')

    def test_unclosed_fences_and_indented_examples_are_not_dependency_declarations(self):
        rows = rank_prs(REPO, [pr(1, body='```\nDepends on #99'),
                               pr(2, body='    Depends on #99')])
        self.assertTrue(all(r['ready'] for r in rows))


def node(number=1, **changes):
    result = dict(number=number, title='fix: bug', body='', author={'login': 'a'},
        authorAssociation='MEMBER', isDraft=False, headRefName=f'branch-{number}',
        baseRefName='main', headRefOid='b' * 40, baseRefOid='a' * 40,
        baseRef={'target': {'oid': 'a' * 40}},
        headRepository={'nameWithOwner': REPO}, mergeable='MERGEABLE',
        mergeStateStatus='CLEAN', reviewDecision='REVIEW_REQUIRED',
        labels={'nodes': [], 'pageInfo': {'hasNextPage': False}},
        commits={'nodes': [{'commit': {'oid': 'b' * 40, 'statusCheckRollup':
            {'state': 'SUCCESS', 'contexts': {'totalCount': 2}}}}]})
    return {**result, **changes}


class Source:
    repo = REPO
    owner = 'owner'
    name = 'repo'

    def __init__(self):
        self.nodes = [node()]
        self.calls = []
        self.compare = {'status': 'ahead', 'merge_base_commit': {'sha': 'a' * 40}}

    def graphql(self, query, variables):
        self.calls.append(('graphql', variables))
        return {'repository': {'defaultBranchRef': {'name': 'main', 'target': {'oid': 'a' * 40}},
            'pullRequests': {'nodes': copy.deepcopy(self.nodes), 'totalCount': len(self.nodes),
                'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}

    def request(self, method, endpoint):
        self.calls.append((method, endpoint))
        return self.compare


class CollectionTests(unittest.TestCase):
    def test_current_head_ci_and_immutable_ancestry(self):
        source = Source()
        result = collect_priority(source)
        self.assertTrue(result['prs'][0]['ready'])
        self.assertIn('a' * 40 + '...' + 'b' * 40, source.calls[-1][1])
        self.assertIn('authorAssociation', __import__('agentops.merge_priority', fromlist=['QUERY']).QUERY)

    def test_ancestry_and_missing_ci_fail_closed(self):
        source = Source()
        source.compare = {'status': 'diverged', 'merge_base_commit': {'sha': 'c' * 40}}
        self.assertFalse(collect_priority(source)['prs'][0]['ready'])
        source.nodes = [node(commits={'nodes': []})]
        result = collect_priority(source)
        self.assertEqual(result['prs'][0]['ci'], 'unknown')
        self.assertFalse(result['prs'][0]['ready'])

    def test_compare_failure_is_a_visible_per_pr_unknown(self):
        source = Source()
        source.request = lambda *args: (_ for _ in ()).throw(RuntimeError('private diagnostic'))
        row = collect_priority(source)['prs'][0]
        self.assertIsNone(row['up_to_date'])
        self.assertFalse(row['ready'])
        self.assertNotIn('private diagnostic', str(row))

    def test_immutable_comparisons_are_reused_but_new_head_is_rechecked(self):
        source, cache = Source(), {}
        collect_priority(source, cache)
        collect_priority(source, cache)
        self.assertEqual(len([c for c in source.calls if c[0] == 'GET']), 1)
        source.nodes[0]['headRefOid'] = 'd' * 40
        source.nodes[0]['commits']['nodes'][0]['commit']['oid'] = 'd' * 40
        collect_priority(source, cache)
        self.assertEqual(len([c for c in source.calls if c[0] == 'GET']), 2)

    def test_rollup_for_different_commit_is_not_accepted(self):
        source = Source()
        source.nodes = [node(headRefOid='c' * 40)]
        self.assertEqual(collect_priority(source)['prs'][0]['ci'], 'unknown')

    def test_historical_pr_base_oid_is_not_current_branch_freshness(self):
        source = Source()
        source.nodes = [node(baseRefOid='c' * 40)]
        row = collect_priority(source)['prs'][0]
        self.assertEqual(row['base_sha'], 'a' * 40)
        self.assertTrue(row['up_to_date'])
        self.assertIn('a' * 40 + '...' + 'b' * 40, source.calls[-1][1])

    def test_nondefault_base_uses_current_ref_target_and_missing_ref_is_unknown(self):
        source = Source()
        source.nodes = [node(baseRefName='release', baseRefOid='c' * 40)]
        row = collect_priority(source)['prs'][0]
        self.assertEqual(row['base_sha'], 'a' * 40)
        source.nodes = [node(baseRefName='release', baseRef=None)]
        self.assertIsNone(collect_priority(source)['prs'][0]['up_to_date'])

    def test_complete_pagination_and_inventory_change_detection(self):
        source = Source()
        original = source.graphql
        def paged(query, variables):
            result = original(query, variables)
            page = result['repository']['pullRequests']
            page.update(totalCount=2, nodes=[node(2 if variables['cursor'] else 1)])
            page['pageInfo'] = {'hasNextPage': not bool(variables['cursor']), 'endCursor': 'next'}
            return result
        source.graphql = paged
        self.assertEqual(len(collect_priority(source)['prs']), 2)
        def changed(query, variables):
            result = paged(query, variables)
            if variables['cursor']:
                result['repository']['defaultBranchRef']['target']['oid'] = 'c' * 40
            return result
        source.graphql = changed
        with self.assertRaisesRegex(ValueError, 'changed'):
            collect_priority(source)

    def test_count_mismatch_and_repeated_cursor_are_rejected(self):
        source = Source()
        original = source.graphql
        def incomplete(query, variables):
            result = original(query, variables)
            result['repository']['pullRequests']['totalCount'] = 2
            return result
        source.graphql = incomplete
        with self.assertRaisesRegex(ValueError, 'Incomplete'):
            collect_priority(source)
        def loop(query, variables):
            result = original(query, variables)
            result['repository']['pullRequests'].update(nodes=[], totalCount=2,
                pageInfo={'hasNextPage': True, 'endCursor': 'same'})
            return result
        source.graphql = loop
        with self.assertRaisesRegex(ValueError, 'pagination'):
            collect_priority(source)

    def test_partial_or_duplicate_inventory_rejected(self):
        source = Source()
        source.nodes = [node(), node()]
        with self.assertRaises(ValueError):
            collect_priority(source)


class MonitorTests(unittest.TestCase):
    def test_background_refresh_retains_but_marks_old_data_and_suppresses_duplicates(self):
        gate = threading.Event()
        calls = []
        def reader():
            calls.append(1)
            gate.wait(2)
            if len(calls) > 1:
                raise RuntimeError('credential-secret')
            return {'repo': REPO, 'prs': rank_prs(REPO, [pr(1)])}
        now = [100.0]
        monitor = PriorityMonitor(reader, interval=60, max_age=60, clock=lambda: now[0])
        first = monitor.status()
        self.assertTrue(first['refreshing'])
        self.assertFalse(first['fresh'])
        for _ in range(5):
            monitor.status()
        self.assertEqual(len(calls), 1)
        gate.set()
        monitor.wait(2)
        self.assertTrue(monitor.status()['fresh'])
        now[0] += 61
        monitor.status()
        monitor.wait(2)
        result = monitor.status()
        self.assertFalse(result['fresh'])
        self.assertFalse(result['prs'][0]['ready'])
        self.assertNotIn('credential-secret', str(result))
        self.assertTrue(result['error'])
        self.assertEqual(len(calls), 2)

    def test_slow_success_is_usable_but_expires_at_explicit_maximum_age(self):
        now = [100.0]
        def reader():
            now[0] += 90
            return {'repo': REPO, 'prs': rank_prs(REPO, [pr(1)])}
        monitor = PriorityMonitor(reader, max_age=180, clock=lambda: now[0])
        monitor.status()
        monitor.wait(2)
        # A refresh starts immediately when the prior collection took >60s.
        # Freeze it so the status can be inspected without a second observation.
        monitor.attempted = now[0]
        self.assertTrue(monitor.status()['fresh'])
        self.assertEqual(monitor.status()['freshness_seconds'], 180)
        now[0] += 181
        monitor.attempted = now[0]
        self.assertFalse(monitor.status()['fresh'])


class EndpointTests(unittest.TestCase):
    def test_endpoint_is_independent_of_ledger_and_never_launches_reviews(self):
        from agentops.panel import make_server
        class Monitor:
            def status(self):
                return {'repo': REPO, 'fresh': True, 'prs': rank_prs(REPO, [pr(1)])}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'absent.sqlite3'
            server = make_server(path, port=0, priority_monitor=Monitor())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/api/merge-priority') as response:
                    self.assertTrue(json.load(response)['prs'][0]['ready'])
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertFalse(path.exists())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
