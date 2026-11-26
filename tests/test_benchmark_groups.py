import json
import time
import os
from pathlib import Path
from zipfile import ZipFile
import pytest

from agentops.benchmark_experiments import catalogue
from agentops.benchmark_groups import freeze, run_group
from agentops.benchmark_service import BenchmarkService
from agentops.benchmarks.core import digest
from benchmark_fixture import build_fixture


def fixture_design(tmp_path):
    wheel,_=build_fixture(tmp_path)
    config=tmp_path/'runner.json'
    config.write_text(json.dumps({'wheelhouse':str(tmp_path),'startup_timeout':10}))
    design=catalogue()['templates'][0]['design']
    design['candidate']={'kind':'local','artifact':str(wheel),'sha':'a'*40}
    design['targets']=[{'provider':'openai','model':'pinned-model'}]
    design['runner']['config_path']=str(config)
    return design


def test_real_group_freezes_sources_and_writes_hashed_paired_cell_evidence(tmp_path):
    design=fixture_design(tmp_path)
    service=BenchmarkService(tmp_path/'ledger.sqlite')
    plan=freeze(design,service)
    assert plan['lane']=='simulator'
    assert plan['resolved']['candidate']['artifact_sha256'].startswith('sha256:')
    output=tmp_path/'group'
    result=run_group(plan,output)
    assert result['decision']['status']=='inconclusive'
    assert result['lane']=='simulator'
    assert result['cells'][0]['state']=='completed'
    assert len(result['cells'][0]['evidence']['attempts'])==10
    assert {a.get('tokens') for a in result['cells'][0]['evidence']['attempts']}=={70,100}
    assert result['cells'][0]['evidence']['identity_verification']['status']=='pass'
    assert result['cells'][0]['evidence']['manifest_digest']==result['cells'][0]['manifest_digest']
    summary=json.loads((output/'summary.json').read_text())
    assert digest(result)==summary['result_sha256']
    assert json.loads((output/'group-state.json').read_text())['state']=='completed'


def test_completed_group_reopen_verifies_evidence_without_replaying_requests(tmp_path):
    design=fixture_design(tmp_path)
    plan=freeze(design,BenchmarkService(tmp_path/'ledger.sqlite'))
    output=tmp_path/'group'
    first=run_group(plan,output)
    original=json.loads((output/'group-state.json').read_text())
    second=run_group(plan,output)
    assert second==first
    assert json.loads((output/'group-state.json').read_text())==original


def test_group_subprocess_survives_panel_reload_and_exposes_verified_results(tmp_path):
    from agentops.benchmark_studio import Studio
    design=fixture_design(tmp_path)
    service=BenchmarkService(tmp_path/'ledger.sqlite')
    job=service.submit({'action':'group','design':design})
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        job=next(j for j in service.status()['jobs'] if j['id']==job['id'])
        if job['state'] in {'completed','failed','interrupted'}:
            break
        time.sleep(.1)
    assert job['state']=='completed',job
    reloaded=Studio(BenchmarkService(tmp_path/'ledger.sqlite'))
    assert reloaded.measured_state()['groups'][0]['label']=='Candidate regression'
    result=reloaded.group_result(job['id'])
    assert result['result']['cells'][0]['state']=='completed'
    assert result['qualification']['status']=='inconclusive'
    assert result['result']['lane']=='simulator'
    assert result['sha256']==digest(result['result'])


def test_docker_cells_receive_only_selected_provider_environment_names(monkeypatch):
    from agentops.benchmark_groups import _transport
    monkeypatch.setenv('OPENAI_API_KEY','private-openai')
    monkeypatch.setenv('ANTHROPIC_API_KEY','private-anthropic')
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','private-aws')
    command=_transport({'transport':'docker','image':'runner','env':[]},'openai')
    assert 'OPENAI_API_KEY' in command
    assert 'ANTHROPIC_API_KEY' not in command
    assert 'AWS_SECRET_ACCESS_KEY' not in command
    assert not any('private-' in part for part in command)


def test_group_submission_key_reconciles_lost_reply_without_launching_twice(tmp_path,monkeypatch):
    import pytest
    from unittest.mock import Mock
    service=BenchmarkService(tmp_path/'ledger.sqlite')
    design=fixture_design(tmp_path)
    launch=Mock()
    monkeypatch.setattr('agentops.benchmark_service.subprocess.Popen',launch)
    payload={'action':'group','design':design,'client_request_id':'c'*32}
    first=service.submit(payload)
    second=service.submit(payload)
    assert first['id']==second['id']
    assert launch.call_count==1
    assert len(service.status()['jobs'])==1
    with pytest.raises(ValueError,match='different'):
        service.submit(dict(payload,design=dict(design,label='Changed intention')))


def test_two_build_group_retains_both_pinned_sources_and_optimization_modes(tmp_path):
    design=fixture_design(tmp_path)
    baseline_dir=tmp_path/'baseline';baseline_dir.mkdir()
    baseline,_=build_fixture(baseline_dir)
    with ZipFile(baseline,'a') as wheel:
        wheel.writestr('headroom/build_variant.py','BUILD = "baseline"\n')
    design['baseline']={'kind':'local','artifact':str(baseline),'sha':'b'*40}
    design['baseline_optimization']=False
    plan=freeze(design,BenchmarkService(tmp_path/'ledger.sqlite'))
    result=run_group(plan,tmp_path/'group')
    cell=result['cells'][0]
    assert cell['state']=='completed',cell
    assert cell['evidence']['schema_version']==2
    rows=cell['evidence']['attempts']
    assert {row['identity']['candidate_sha'] for row in rows if row['arm']=='baseline'}=={'b'*40}
    assert {row['identity']['candidate_sha'] for row in rows if row['arm']=='candidate'}=={'a'*40}
    assert {row['tokens'] for row in rows if row['arm']=='baseline'}=={100}
    assert {row['tokens'] for row in rows if row['arm']=='candidate'}=={70}
    assert result['decision']['status']=='inconclusive'


@pytest.mark.parametrize('tool',['inspect','aiperf'])
def test_external_tool_group_records_genuine_pinned_harness_telemetry(tmp_path,tool):
    runtime=os.environ.get('HEADROOM_BENCH_TOOL_PYTHON')
    if not runtime or not Path(runtime).is_file():
        pytest.skip('Set HEADROOM_BENCH_TOOL_PYTHON to the optional pinned harness environment')
    from agentops.benchmarks.tools import available
    if not available(tool,{'python':runtime})['available']:
        pytest.skip('Optional pinned '+tool+' runtime unavailable')
    design=fixture_design(tmp_path)
    config=Path(design['runner']['config_path'])
    data=json.loads(config.read_text());data['tool_python']=runtime;config.write_text(json.dumps(data))
    design['tools']=[tool]
    plan=freeze(design,BenchmarkService(tmp_path/'ledger.sqlite'))
    result=run_group(plan,tmp_path/'group')
    cell=result['cells'][0]
    assert cell['state']=='completed',cell
    attempts=cell['evidence']['attempts']
    assert len(attempts)==10
    assert {row.get('tokens') for row in attempts}=={70,100}
    assert {row['tool_metrics']['tool'] for row in attempts}=={tool}
    assert {row['tool_metrics']['requests'] for row in attempts}=={1}
    assert all('native_logs' not in row['tool_metrics'] for row in attempts)
    assert list((tmp_path/'group').glob('cells/*/results/tool-evidence/harness-*'))
    assert result['decision']['status']=='inconclusive'
