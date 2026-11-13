import copy
import base64
import hashlib
import tempfile
import unittest
from pathlib import Path

from agentops.github import GitHub, gh_transport
from agentops.planning import plan, packet
from agentops.evidence import Evidence


SHA = 'a' * 40
HEAD = 'b' * 40


def raw_pr(number, author='someone', updated='2026-11-01T00:00:00Z'):
    return {'number': number, 'title': f'PR {number}', 'body': 'why',
            'url': f'https://github.com/o/r/pull/{number}', 'author': {'login': author},
            'headRefOid': HEAD, 'baseRefName': 'main', 'baseRefOid': SHA,
            'headRefName': f'fix-{number}', 'isDraft': False,
            'updatedAt': updated, 'reviewDecision': 'REVIEW_REQUIRED',
            'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN',
            'headRepository': {'nameWithOwner': 'o/r'}}


class FakeAPI:
    def __init__(self):
        self.prs = [raw_pr(1), raw_pr(2)]
        self.main_reads = 0
        self.main_changes = False
        self.fail_second = False
        self.thread_more = True

    def __call__(self, method, endpoint, payload=None):
        if endpoint == 'repos/o/r/commits/main':
            self.main_reads += 1
            return {'sha': 'c' * 40 if self.main_changes and self.main_reads > 1 else SHA}
        if endpoint == 'graphql':
            query = payload['query']
            variables = payload.get('variables', {})
            if 'AgentOpsQueue' in query:
                if variables.get('cursor'):
                    if self.fail_second:
                        raise RuntimeError('rate limit')
                    return {'data': {'repository': {'pullRequests': {
                        'totalCount': 2,
                        'nodes': [self.prs[1]],
                        'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}
                return {'data': {'repository': {'pullRequests': {
                    'totalCount': 2,
                    'nodes': [self.prs[0]],
                    'pageInfo': {'hasNextPage': True, 'endCursor': 'next'}}}}}
            if 'AgentOpsThreads' in query:
                if variables.get('cursor'):
                    return {'data': {'repository': {'pullRequest': {'reviewThreads': {
                        'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}
                return {'data': {'repository': {'pullRequest': {'reviewThreads': {
                    'nodes': [{'id': 'thread-1', 'isResolved': False, 'isOutdated': False,
                               'comments': {'nodes': [{'id': 'c1', 'body': 'Devin finding'}],
                               'pageInfo': {'hasNextPage': self.thread_more, 'endCursor': 'c-next'}}}],
                    'pageInfo': {'hasNextPage': True, 'endCursor': 't-next'}}}}}}
            if 'AgentOpsThreadComments' in query:
                return {'data': {'node': {'comments': {'nodes': [{'id': 'c2', 'body': 'author reply'}],
                          'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}
            raise AssertionError(query)
        if endpoint == 'repos/o/r/pulls/1':
            return {'number': 1, 'state': 'open', 'title': 'PR 1', 'body': 'why',
                    'user': {'login': 'someone'}, 'updated_at': '2026-11-01T00:00:00Z',
                    'draft': False, 'mergeable': True, 'changed_files': 2,
                    'head': {'sha': HEAD, 'ref': 'fix', 'repo': {'full_name': 'o/r'}},
                    'base': {'sha': SHA, 'ref': 'main'}}
        source = b'name: CI\non: pull_request\n'
        source_sha = hashlib.sha1(b'blob ' + str(len(source)).encode() + b'\0' + source).hexdigest()
        if endpoint.startswith('repos/o/r/contents/.github/workflows?'):
            return [{'path': '.github/workflows/ci.yml', 'type': 'file', 'sha': source_sha}]
        if endpoint.startswith('repos/o/r/contents/.github/workflows/ci.yml?'):
            return {'content': base64.b64encode(source).decode(), 'encoding': 'base64', 'sha': source_sha}
        if endpoint.startswith('repos/o/r/pulls/1/files?'):
            page = int(endpoint.split('page=')[-1])
            return [{'filename': 'headroom/a.py' if page == 1 else 'tests/test_a.py'}] if page <= 2 else []
        if endpoint.startswith('repos/o/r/issues/1/comments?'):
            page = int(endpoint.split('page=')[-1])
            return [{'id': page, 'body': f'comment {page}'}] if page <= 2 else []
        if endpoint.startswith('repos/o/r/pulls/1/reviews?') or endpoint.startswith('repos/o/r/pulls/1/comments?'):
            return []
        if '/check-runs?' in endpoint:
            return {'total_count': 1, 'check_runs': [{'id': 10, 'head_sha': HEAD, 'conclusion': 'success'}]}
        if '/actions/runs?' in endpoint:
            return {'total_count': 1, 'workflow_runs': [{'id': 20, 'head_sha': HEAD, 'conclusion': 'success'}]}
        if '/statuses?' in endpoint:
            return []
        raise AssertionError((method, endpoint, payload))


class InventoryTests(unittest.TestCase):
    def test_read_transport_retries_transient_gateway_failure(self):
        import subprocess
        from unittest.mock import patch
        failed = subprocess.CompletedProcess([], 1, '', 'gh: HTTP 502')
        good = subprocess.CompletedProcess([], 0, '{"sha":"abc"}', '')
        with patch('agentops.github.subprocess.run', side_effect=[failed, good]), \
                patch('agentops.github.time.sleep'):
            self.assertEqual(gh_transport('GET', 'repos/o/r/commits/main'), {'sha': 'abc'})

    def test_empty_successful_mutation_response_is_not_misclassified_as_lost_write(self):
        import subprocess
        from unittest.mock import patch
        with patch('agentops.github.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            self.assertEqual(gh_transport('POST', 'repos/o/r/actions/runs/1/rerun'), {})

    def test_mutation_gateway_errors_are_never_blindly_retried(self):
        import subprocess
        from unittest.mock import patch
        outputs = iter([subprocess.CompletedProcess([], 1, '', 'gh: HTTP 502'),
                        subprocess.CompletedProcess([], 0, '{"unexpected_second_write":true}', '')])
        with patch('agentops.github.subprocess.run', side_effect=lambda *a, **k: next(outputs)):
            with self.assertRaises(RuntimeError):
                gh_transport('POST', 'repos/o/r/issues/1/comments', {'body': 'finding'})

    def test_queue_is_complete_across_graphql_pages(self):
        snapshot = GitHub('o/r', FakeAPI(), page_size=1).snapshot()
        self.assertEqual([p['number'] for p in snapshot['prs']], [1, 2])
        self.assertEqual(snapshot['main_sha'], SHA)
        self.assertTrue(snapshot['complete'])

    def test_scheduling_inventory_ignores_discussion_timestamp_changes(self):
        api = FakeAPI()
        def comments_arrive(method, endpoint, payload=None):
            data = copy.deepcopy(api(method, endpoint, payload))
            if endpoint == 'graphql' and 'AgentOpsQueueIdentities' in payload['query']:
                for pr in data['data']['repository']['pullRequests']['nodes']:
                    pr['updatedAt'] = '2026-11-13T22:00:00Z'
            return data
        self.assertTrue(GitHub('o/r', comments_arrive).snapshot(queue_only=True)['complete'])
        with self.assertRaises(ValueError):
            GitHub('o/r', comments_arrive).snapshot()

    def test_scheduling_inventory_still_rejects_head_draft_and_target_changes(self):
        for field, changed in (('headRefOid', 'c'*40), ('isDraft', True),
                               ('baseRefName', 'other'), ('headRefName', 'other')):
            with self.subTest(field=field):
                api = FakeAPI()
                def mutate(method, endpoint, payload=None):
                    data = copy.deepcopy(api(method, endpoint, payload))
                    if endpoint == 'graphql' and 'AgentOpsQueueIdentities' in payload['query']:
                        data['data']['repository']['pullRequests']['nodes'][0][field] = changed
                    return data
                with self.assertRaises(ValueError):
                    GitHub('o/r', mutate).snapshot(queue_only=True)

    def test_selected_inventory_does_not_read_unrelated_queue(self):
        calls = []
        def selected(method, endpoint, payload=None):
            if endpoint == 'repos/o/r/commits/main':
                return {'sha': SHA}
            calls.append(payload)
            self.assertIn('AgentOpsSelection', payload['query'])
            self.assertIn('pullRequest(number:1)', payload['query'])
            self.assertNotIn('pullRequests(', payload['query'])
            return {'data': {'repository': {'pr1': {**raw_pr(1), 'state': 'OPEN'}}}}
        snapshot = GitHub('o/r', selected).snapshot(queue_only=True, numbers=[1])
        self.assertEqual([p['number'] for p in snapshot['prs']], [1])
        self.assertTrue(snapshot['complete'])
        self.assertEqual(len(calls), 1)

    def test_selected_inventory_closed_pr_is_not_open_and_missing_alias_is_incomplete(self):
        for row in ({'pr1': {**raw_pr(1), 'state': 'CLOSED'}}, {}):
            def selected(method, endpoint, payload=None):
                return {'sha': SHA} if endpoint != 'graphql' else {'data': {'repository': row}}
            if row:
                self.assertEqual(GitHub('o/r', selected).snapshot(queue_only=True, numbers=[1])['prs'], [])
            else:
                with self.assertRaises(ValueError):
                    GitHub('o/r', selected).snapshot(queue_only=True, numbers=[1])

    def test_partial_queue_is_never_accepted_on_api_failure(self):
        api = FakeAPI()
        api.fail_second = True
        with self.assertRaises(RuntimeError):
            GitHub('o/r', api, page_size=1).snapshot()

    def test_main_change_during_inventory_requires_fresh_snapshot(self):
        api = FakeAPI()
        api.main_changes = True
        with self.assertRaisesRegex(ValueError, 'main changed'):
            GitHub('o/r', api, page_size=1).snapshot()

    def test_membership_change_between_inventory_passes_is_not_a_complete_queue(self):
        api = FakeAPI()
        def changed(method, endpoint, payload=None):
            data = api(method, endpoint, payload)
            if endpoint == 'graphql' and 'AgentOpsQueueIdentities' in payload['query']:
                connection = data['data']['repository']['pullRequests']
                if payload['variables'].get('cursor'):
                    connection['nodes'] = []
            return data
        with self.assertRaisesRegex(ValueError, 'changed|complete'):
            GitHub('o/r', changed, page_size=1).snapshot()

    def test_pull_collects_nested_threads_comments_and_all_rest_pages(self):
        pr = GitHub('o/r', FakeAPI(), page_size=1).pull(1)
        self.assertEqual(len(pr['files']), 2)
        self.assertEqual([c['body'] for c in pr['comments']], ['comment 1', 'comment 2'])
        self.assertEqual([c['body'] for c in pr['threads'][0]['comments']],
                         ['Devin finding', 'author reply'])
        self.assertFalse(pr['threads'][0]['isResolved'])
        self.assertEqual(pr['workflows'][0]['id'], 20)
        self.assertEqual(pr['expected_workflows'], ['.github/workflows/ci.yml'])
        self.assertTrue(pr['expected_workflows_verified'])

    def test_missing_changed_files_fails_closed(self):
        api = FakeAPI()
        def truncated(method, endpoint, payload=None):
            data = api(method, endpoint, payload)
            if endpoint == 'repos/o/r/pulls/1':
                data['changed_files'] = 3001
            return data
        with self.assertRaisesRegex(ValueError, 'files'):
            GitHub('o/r', truncated, page_size=1).pull(1)

    def test_graphql_partial_errors_are_rejected(self):
        def broken(method, endpoint, payload=None):
            if endpoint == 'graphql':
                return {'data': {'repository': None}, 'errors': [{'message': 'forbidden'}]}
            return {'sha': SHA}
        with self.assertRaisesRegex(ValueError, 'GraphQL'):
            GitHub('o/r', broken).snapshot()

    def test_repeated_pagination_cursor_is_rejected(self):
        api = FakeAPI()
        def stuck(method, endpoint, payload=None):
            if endpoint == 'graphql' and 'AgentOpsQueue' in payload['query']:
                return api(method, endpoint, {**payload, 'variables': {}})
            return api(method, endpoint, payload)
        with self.assertRaisesRegex(ValueError, 'cursor'):
            GitHub('o/r', stuck).snapshot()

    def test_priority_order_reuses_prior_revision_knowledge(self):
        snapshot = {'prs': [
            {'number': 1, 'author': 'known', 'updated_at': '2026-11-10', 'head_sha': HEAD, 'review_decision': 'APPROVED'},
            {'number': 2, 'author': 'chopratejas', 'updated_at': '2026-11-01', 'head_sha': HEAD, 'review_decision': 'APPROVED'},
            {'number': 3, 'author': 'new', 'updated_at': '2026-11-09', 'head_sha': HEAD, 'review_decision': None}]}
        result = plan(snapshot, {1: {'head_sha': HEAD}, 2: {'head_sha': 'c' * 40}})
        self.assertEqual([p['number'] for p in result], [2, 3, 1])

    def test_full_evidence_is_hashed_and_packet_bounded_without_silent_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Evidence(directory)
            pr = GitHub('o/r', FakeAPI(), page_size=1).pull(1)
            pr['body'] = 'x' * 40000
            output = packet(pr, {'repo': 'o/r', 'main_sha': SHA}, [], evidence, 24576)
            self.assertLessEqual(len(output.encode('utf8')), 24576)
            import json
            parsed = json.loads(output)
            stored = evidence.get(parsed['evidence']['sha256'])
            self.assertEqual(stored['body'], 'x' * 40000)
            self.assertTrue(parsed['requires_full_evidence_read'])
            with self.assertRaisesRegex(ValueError, 'budget'):
                packet(pr, {'repo': 'o/r', 'main_sha': SHA}, [], evidence, 5)

    def test_evidence_rejects_hash_tampering_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Evidence(directory)
            reference = evidence.put({'truth': 1})
            Path(directory, reference['path']).write_text('{"truth":2}', encoding='utf8')
            with self.assertRaisesRegex(ValueError, 'hash'):
                evidence.get(reference['sha256'])
            with self.assertRaises(ValueError):
                evidence.get('../secret')

    def test_packet_keeps_discussion_but_removes_irrelevant_api_metadata_from_agent_input(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            evidence = Evidence(directory)
            pr = GitHub('o/r', FakeAPI(), page_size=1).pull(1)
            pr['checks'][0]['app'] = {'huge_badge_metadata': 'x' * 20000}
            pr['comments'][0]['user'] = {'login': 'author', 'avatar_url': 'https://example.test/avatar'}
            parsed = json.loads(packet(pr, {'repo': 'o/r', 'main_sha': SHA}, [], evidence))
            normalized = evidence.get(parsed['evidence']['sha256'])
            self.assertNotIn('app', normalized['checks'][0])
            self.assertEqual(normalized['comments'][0]['body'], 'comment 1')
            self.assertEqual(normalized['comments'][0]['author'], 'author')
            self.assertEqual(evidence.get(parsed['raw_api_evidence']['sha256'])['checks'][0]['app'],
                             {'huge_badge_metadata': 'x' * 20000})

    def test_repository_and_pr_identifiers_cannot_be_endpoint_injection(self):
        with self.assertRaises(ValueError):
            GitHub('o/r/../../user', FakeAPI())
        with self.assertRaises(ValueError):
            GitHub('o/r', FakeAPI()).pull('1/comments')


if __name__ == '__main__':
    unittest.main()
