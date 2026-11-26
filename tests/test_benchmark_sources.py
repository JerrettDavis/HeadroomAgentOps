import pytest

from agentops.benchmark_sources import Sources


def test_repository_rejects_unbounded_components_without_regex_backtracking():
    from agentops.benchmark_sources import repository
    with pytest.raises(ValueError):
        repository('-' * 100000 + '/repo')


class Remote:
    def __init__(self):
        self.calls = []

    def request(self, method, endpoint):
        self.calls.append((method, endpoint))
        commit = {'sha': 'a' * 40, 'commit': {'message': 'Improve context (#42)\nDetails',
                  'author': {'name': 'Ada', 'date': '2026-11-25'}}, 'author': {'login': 'ada'}}
        if '/pulls/42/commits?' in endpoint:
            return [commit]
        if endpoint.startswith('search/commits?'):
            return {'total_count': 1, 'items': [commit]}
        if '/actions/artifacts?' in endpoint:
            return {'total_count': 2, 'artifacts': [
                {'id': 12, 'name': 'wheel', 'size_in_bytes': 1024, 'expired': False,
                 'created_at': '2026-11-25', 'expires_at': '2026-12-25',
                 'workflow_run': {'id': 7, 'head_sha': 'a'*40}},
                {'id': 13, 'name': 'old', 'expired': True, 'workflow_run': {'id': 6, 'head_sha': 'b'*40}}
            ]}
        return [commit]


def test_commit_search_is_scoped_cached_bounded_and_immutable():
    remote = Remote()
    source = Sources(remote)
    first = source.search('owner/repo', 'context author:ada')
    assert first['items'][0]['sha'] == 'a'*40
    assert first['items'][0]['subject'] == 'Improve context (#42)'
    assert first['items'][0]['author'] == 'ada'
    assert source.search('owner/repo', 'context author:ada') == first
    assert len(remote.calls) == 1
    assert 'repo%3Aowner%2Frepo' in remote.calls[0][1]
    assert 'per_page=20' in remote.calls[0][1]
    source.search('owner/other', 'context author:ada')
    assert len(remote.calls) == 2


@pytest.mark.parametrize('repo,query,page', [('../repo','ok',1),('owner/repo','x'*121,1),
    ('owner/repo','ok',0),('owner/repo','ok',True),('owner/repo','ok',21),
    ('owner/repo','bad\nquery',1)])
def test_source_search_rejects_unbounded_or_invalid_queries(repo,query,page):
    remote = Remote()
    with pytest.raises(ValueError):
        Sources(remote).search(repo,query,page)
    assert not remote.calls


@pytest.mark.parametrize('query', ['repo:other/project', 'context OR org:other',
                                  'author:ada user:other', 'REPO:Other/Project'])
def test_search_cannot_override_selected_repository(query):
    remote = Remote()
    with pytest.raises(ValueError, match='scope'):
        Sources(remote).search('owner/repo',query)
    assert not remote.calls


def test_pr_number_search_lists_pinned_commits_without_building():
    remote = Remote()
    data = Sources(remote).search('owner/repo','#42')
    assert data['items'][0]['sha'] == 'a'*40
    assert '/pulls/42/commits?' in remote.calls[0][1]
    assert all(method == 'GET' for method,_ in remote.calls)


def test_artifacts_are_bound_to_selected_commit_and_show_expiry():
    source = Sources(Remote())
    data = source.artifacts('owner/repo','a'*40)
    assert [a['id'] for a in data['items']] == [12]
    assert data['items'][0]['run_id'] == 7
    assert data['items'][0]['expired'] is False
    with pytest.raises(ValueError):
        source.artifacts('owner/repo','main')


def test_cache_expires_and_cannot_grow_unbounded():
    now=[0]
    remote=Remote()
    source=Sources(remote,clock=lambda:now[0])
    source.search('owner/repo','context')
    now[0]=61
    source.search('owner/repo','context')
    assert len(remote.calls)==2
    for i in range(130):
        source.search('owner/repo',f'query {i}')
    assert len(source.cache)==128


def test_foreign_search_results_are_not_mislabeled_as_selected_repository():
    class Foreign(Remote):
        def request(self,method,endpoint):
            data=super().request(method,endpoint)
            data['items'][0]['repository']={'full_name':'other/project'}
            return data
    assert Sources(Foreign()).search('owner/repo','context OR other')['items']==[]
