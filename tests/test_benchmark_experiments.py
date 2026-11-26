import copy
import json

import pytest

from agentops.benchmark_experiments import catalogue, validate, preview, preflight


def design():
    data=copy.deepcopy(catalogue()['templates'][0]['design'])
    data['targets']=[{'provider':'openai','model':'pinned-model'}]
    return data


def test_prepared_source_preserves_repository_in_exported_provenance(tmp_path,monkeypatch):
    import agentops.benchmark_experiments as experiments
    from agentops.benchmark_studio import Studio
    wheel=tmp_path/'candidate.whl';wheel.write_bytes(b'fixture')
    monkeypatch.setattr(Studio,'source',lambda *args:{'id':'source-job','artifact':str(wheel),
        'sha':'a'*40,'repository':'owner/repo','provenance':{'strategy':'artifact'}})
    monkeypatch.setattr(experiments,'inspect_wheel',lambda path:{'sha256':'sha256:'+'b'*64,
        'features':['default'],'simulator':True})
    from types import SimpleNamespace
    resolved=experiments.resolve_source({'kind':'prepared','job_id':'source-job'},SimpleNamespace(root=tmp_path))
    assert resolved['provenance']=={'strategy':'artifact','repository':'owner/repo','source_job_id':'source-job'}


def test_configuration_schema_is_read_from_selected_wheel_without_executing_it(tmp_path):
    from zipfile import ZipFile
    from types import SimpleNamespace
    from agentops.benchmark_experiments import capabilities
    wheel=tmp_path/'candidate.whl'
    with ZipFile(wheel,'w') as archive:
        archive.writestr('headroom/proxy/server.py',"raise RuntimeError('must never execute')\nparser.add_argument('--no-cache',action='store_true',default=True,help='Selected version cache control')\nparser.add_argument('--compress-user-messages',action='store_false')\n")
    result=capabilities({'candidate':{'kind':'local','artifact':str(wheel),'sha':'a'*40}},SimpleNamespace(root=tmp_path))
    assert result['supported_features']==['default','no-cache']
    schema=next(f for f in result['features'] if f['id']=='no-cache')['configuration_schema']
    assert schema=={'type':'boolean','enum':[False,True],'default':True,'cli_argument':'--no-cache',
                    'title':'No cache','description':'Selected version cache control'}


def test_measured_templates_expand_explicit_matrix_without_external_requests():
    data=design()
    data['scenarios']=['smoke','long-context']
    data['targets'].append({'provider':'anthropic','model':'pinned-claude'})
    result=preview(data)
    assert len(result['cells'])==4
    assert len({c['id'] for c in result['cells']})==4
    assert result['requests']==40
    assert result['charges'] is True
    assert result['design']['candidate']['kind']=='prepared'


@pytest.mark.parametrize('changes', [
    {'tools':[]},{'tools':['unknown']},{'scenarios':[]},{'features':['invented']},
    {'targets':[{'provider':'unknown','model':'model'}]},
    {'targets':[{'provider':'openai','model':'m','api_key':'secret'}]},
    {'repeats':True},{'seed':-1},{'unknown':'field'},
])
def test_unknown_or_ambiguous_design_choices_are_rejected(changes):
    with pytest.raises(ValueError):
        validate(dict(design(),**changes))


def test_matrix_and_request_limits_account_for_every_scenario_target_and_feature():
    data=design()
    data['targets']=[{'provider':'openai','model':f'pinned-{i}'} for i in range(10)]
    data['scenarios']=['smoke','long-context','code']
    with pytest.raises(ValueError,match='24'):
        preview(data)


def test_load_sweep_expands_concurrency_and_counts_all_upstream_requests():
    template=next(t for t in catalogue()['templates'] if t['id']=='measured-load')
    result=preview(template['design'])
    assert [cell['concurrency'] for cell in result['cells']]==[1,2,4]
    assert result['requests']==120
    assert len({cell['id'] for cell in result['cells']})==3
    with pytest.raises(ValueError,match='1200'):
        preview(dict(template['design'],repeats=100,load_concurrency=list(range(1,8))))
    for change in ({'tools':['native']},{'baseline':{'kind':'prepared','job_id':''}},
                   {'targets':[{'provider':'anthropic','model':'pinned'}]}, {'load_concurrency':[1,1]},
                   {'load_concurrency':[65]},{'load_concurrency':[True]}):
        with pytest.raises(ValueError):
            preview(dict(template['design'],**change))


def test_request_budget_counts_every_task_in_a_scenario(tmp_path,monkeypatch):
    import agentops.benchmark_experiments as module
    scenarios=copy.deepcopy(module.SCENARIOS)
    smoke=next(s for s in scenarios if s['id']=='smoke')
    smoke['tasks'].append(dict(smoke['tasks'][0],id='second-task'))
    monkeypatch.setattr(module,'SCENARIOS',scenarios)
    data=design();data['repeats']=100
    assert preview(data)['requests']==400
    data['targets']=[{'provider':'openai','model':str(i)} for i in range(4)]
    with pytest.raises(ValueError,match='1200'):
        preview(data)


def test_preflight_reports_missing_sources_and_credentials_without_leaking_values(tmp_path,monkeypatch):
    from agentops.benchmark_service import BenchmarkService
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    report=preflight(design(),BenchmarkService(tmp_path/'ledger.sqlite'))
    assert not report['ready']
    assert any(c['id']=='candidate' and not c['ready'] for c in report['checks'])
    assert any(c['id']=='credentials:openai' and not c['ready'] for c in report['checks'])
    monkeypatch.setenv('OPENAI_API_KEY','do-not-return-this-value')
    report=preflight(design(),BenchmarkService(tmp_path/'ledger.sqlite'))
    assert 'do-not-return-this-value' not in json.dumps(report)
    assert any(c['id']=='credentials:openai' and c['ready'] for c in report['checks'])


def test_ssh_preflight_checks_remote_credentials_instead_of_local_keys(tmp_path,monkeypatch):
    import agentops.benchmark_experiments as experiments
    from agentops.benchmark_service import BenchmarkService
    from types import SimpleNamespace
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setattr(experiments,'resolve_source',lambda *args:{'artifact':'candidate.whl','sha':'a'*40,
        'artifact_sha256':'sha256:'+'b'*64,'features':['default'],'simulator':False})
    monkeypatch.setattr(experiments.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(
        stdout=json.dumps({'OPENAI_API_KEY':True,'python_ready':True}).encode()))
    monkeypatch.setattr(experiments.shutil,'which',lambda name:'ssh')
    data=design();data['runner']={'transport':'ssh','host':'bench-host','env':[]}
    report=preflight(data,BenchmarkService(tmp_path/'ledger.sqlite'))
    assert report['ready']
    assert any(c['id']=='credentials:openai' and c['ready'] for c in report['checks'])
