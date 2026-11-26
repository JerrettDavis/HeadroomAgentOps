import json

from agentops.benchmark_models import Models


def test_missing_credentials_are_actionable_and_make_no_requests(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    calls=[]
    result=Models(lambda *args:calls.append(args)).list('openai')
    assert result['status']=='missing_credentials'
    assert 'OPENAI_API_KEY' in result['message']
    assert not calls


def test_model_discovery_is_cached_without_returning_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','private-test-value')
    calls=[]
    def fetch(url,headers):
        calls.append((url,headers))
        return {'data':[{'id':'model-v1'},{'id':'model-v2'}]}
    models=Models(fetch)
    result=models.list('openai')
    assert [m['id'] for m in result['models']]==['model-v1','model-v2']
    assert models.list('openai')==result
    assert len(calls)==1
    assert 'private-test-value' not in json.dumps(result)
    monkeypatch.setenv('OPENAI_API_KEY','different-account-key')
    models.list('openai')
    assert len(calls)==2


def test_provider_errors_do_not_echo_response_bodies_or_credentials(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','private-test-value')
    def broken(*args):
        raise ValueError('private-test-value')
    result=Models(broken).list('anthropic')
    assert result['status']=='unavailable'
    assert 'private-test-value' not in json.dumps(result)
