import json
import time
from pathlib import Path

import pytest

from agentops.benchmark_studio import catalogue, validate_design, demo
from agentops.benchmarks.core import digest


def test_measured_design_library_keeps_named_snapshots_without_launching_jobs(tmp_path):
    from agentops.benchmark_studio import Studio
    from agentops.benchmark_experiments import catalogue as measured_catalogue
    from agentops.benchmark_service import BenchmarkService
    service=BenchmarkService(tmp_path/'ledger.sqlite');studio=Studio(service)
    first=measured_catalogue()['templates'][0]['design']
    first['label']='Nightly regression'
    saved=studio.measured_library({'action':'save','design':first})['saved']
    assert studio.measured_library({'action':'save','design':first})['saved']['id']==saved['id']
    second=dict(first,label='Release comparison',seed=7)
    studio.measured_library({'action':'save','design':second})
    library=Studio(service).measured_state()['designs']
    assert {item['design']['label'] for item in library}=={'Nightly regression','Release comparison'}
    assert not service.status()['jobs']
    with pytest.raises(ValueError):
        studio.measured_library({'action':'save','design':dict(first,api_key='secret')})
    studio.measured_library({'action':'remove','id':saved['id']})
    assert len(Studio(service).measured_state()['designs'])==1


def test_design_library_rejects_tampering_without_overwriting_it(tmp_path):
    from agentops.benchmark_studio import Studio
    from agentops.benchmark_service import BenchmarkService
    from agentops.benchmark_experiments import catalogue as measured_catalogue
    studio=Studio(BenchmarkService(tmp_path/'ledger.sqlite'))
    design=measured_catalogue()['templates'][0]['design']
    studio.measured_library({'action':'save','design':design})
    path=studio.service.root/'measured-designs.json'
    records=json.loads(path.read_text());records[0]['design']['seed']=99
    original=json.dumps(records);path.write_text(original)
    with pytest.raises(ValueError,match='identity differs'):
        studio.measured_library({'action':'save','design':design})
    assert path.read_text()==original


def test_design_library_rejects_oversized_save_and_keeps_existing_library_removable(tmp_path,monkeypatch):
    import agentops.benchmark_studio as module
    from agentops.benchmark_service import BenchmarkService
    from agentops.benchmark_experiments import catalogue as measured_catalogue
    studio=module.Studio(BenchmarkService(tmp_path/'ledger.sqlite'))
    design=measured_catalogue()['templates'][0]['design']
    saved=studio.measured_library({'action':'save','design':design})['saved']
    path=studio.service.root/'measured-designs.json';original=path.read_bytes()
    monkeypatch.setattr(module,'DESIGN_LIBRARY_BYTES',len(original)+20,raising=False)
    with pytest.raises(ValueError,match='size'):
        studio.measured_library({'action':'save','design':dict(design,label='Another saved design')})
    assert path.read_bytes()==original
    assert len(studio.measured_state()['designs'])==1
    assert studio.measured_library({'action':'remove','id':saved['id']})['designs']==[]


def test_saved_design_identity_survives_new_optional_defaults(tmp_path):
    from agentops.benchmark_studio import Studio
    from agentops.benchmark_service import BenchmarkService
    from agentops.benchmark_experiments import catalogue as measured_catalogue
    studio=Studio(BenchmarkService(tmp_path/'ledger.sqlite'))
    design=measured_catalogue()['templates'][0]['design'];design.pop('load_concurrency')
    records=[{'id':digest(design),'design':design,'created_at':'legacy'}]
    (studio.service.root/'measured-designs.json').write_text(json.dumps(records))
    assert studio.measured_state()['designs']==records


def test_catalogue_is_ready_to_run_and_detached():
    data = catalogue()
    assert len(data['templates']) >= 3
    assert all(t['design']['scenarios'] for t in data['templates'])
    data['templates'].clear()
    assert catalogue()['templates']


@pytest.mark.parametrize('changes', [
    {'repeats': True}, {'repeats': 1001}, {'scenarios': []},
    {'scenarios': ['missing']}, {'features': ['secret']},
    {'tools': ['aiperf']}, {'provider': 'live'}, {'seed': -1},
    {'unknown': 'field'}, {'models': ['unknown']},
])
def test_design_rejects_invalid_or_unavailable_combinations(changes):
    design = catalogue()['templates'][0]['design']
    with pytest.raises(ValueError):
        validate_design(dict(design, **changes))


def test_demo_runs_real_pairs_with_hashed_simulator_evidence(tmp_path):
    design = catalogue()['templates'][0]['design']
    result = demo(design, tmp_path)
    assert result['lane'] == 'simulator'
    assert result['decision']['status'] == 'inconclusive'
    assert result['decision']['simulated_decision']['pairs'] >= 5
    assert len(result['attempts']) == 2 * len(result['schedule'])
    assert result['identity_verification']['status'] == 'pass'
    summary = json.loads((tmp_path / 'summary.json').read_text())
    evidence = json.loads((tmp_path / summary['result_file']).read_text())
    assert digest(evidence) == summary['result_sha256']
    assert evidence['studio']['design'] == validate_design(design)
    assert evidence['studio']['template_version'] == 1


def test_service_demo_is_durable_and_has_no_external_inputs(tmp_path):
    from agentops.benchmark_service import BenchmarkService
    service = BenchmarkService(tmp_path / 'ledger.sqlite')
    job = service.submit({'action': 'demo', 'design': catalogue()['templates'][0]['design']})
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        job = next(j for j in service.status()['jobs'] if j['id'] == job['id'])
        if job['state'] in {'completed', 'failed', 'interrupted'}:
            break
        time.sleep(.1)
    assert job['state'] == 'completed', job
    assert job['decision']['lane'] == 'simulator'
    assert Path(job['paths']['result']).is_file()
    assert BenchmarkService(tmp_path / 'ledger.sqlite').status()['jobs'][0]['id'] == job['id']


def test_studio_draft_is_validated_and_survives_reload(tmp_path):
    from agentops.benchmark_studio import Studio
    from agentops.benchmark_service import BenchmarkService
    service = BenchmarkService(tmp_path / 'ledger.sqlite')
    studio = Studio(service)
    design = catalogue()['templates'][1]['design']
    assert studio.save_draft(design)['draft'] == design
    assert Studio(service).state()['draft'] == design
    with pytest.raises(ValueError):
        studio.save_draft(dict(design, tools=['unknown']))
    assert studio.state()['draft'] == design


def test_result_rejects_modified_terminal_evidence(tmp_path):
    from agentops.benchmark_studio import Studio
    from agentops.benchmark_service import BenchmarkService, _write
    service = BenchmarkService(tmp_path / 'ledger.sqlite')
    identifier = 'a' * 32
    directory = service.root / identifier
    demo(catalogue()['templates'][0]['design'], directory)
    summary = json.loads((directory / 'summary.json').read_text())
    path = directory / summary['result_file']
    _write(directory / 'job.json', {'id': identifier, 'action': 'demo', 'state': 'completed',
                                   'created_at': 'now', 'exit_code': 2,
                                   'paths': {'result': str(path)}})
    studio = Studio(service)
    assert studio.result(identifier)['result']['lane'] == 'simulator'
    path.write_text('{}')
    with pytest.raises(ValueError, match='verified'):
        studio.result(identifier)


def test_panel_studio_authenticates_reads_and_draft_preview_writes(tmp_path):
    import threading
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from agentops.panel import make_server
    from agentops.benchmark_service import BenchmarkService

    service=BenchmarkService(tmp_path / 'ledger.sqlite')
    server=make_server(tmp_path / 'ledger.sqlite',port=0,benchmark_service=service)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(base+'/api/benchmarks/studio')
        assert unauthorized.value.code==403
        design=catalogue()['templates'][0]['design']
        for endpoint in ('draft','preview','sources','artifacts'):
            with pytest.raises(HTTPError) as unauthorized:
                urlopen(Request(base+'/api/benchmarks/studio/'+endpoint,data=json.dumps(design).encode()))
            assert unauthorized.value.code==403
        with urlopen(base+'/api/launcher') as response:
            token=json.load(response)['token']
        headers={'X-Launch-Token':token,'Content-Type':'application/json','Origin':base}
        from agentops.benchmark_experiments import catalogue as measured_catalogue
        saved_payload=json.dumps({'action':'save','design':measured_catalogue()['templates'][0]['design']}).encode()
        with pytest.raises(HTTPError) as unauthorized:
            urlopen(Request(base+'/api/benchmarks/measured/designs',data=saved_payload))
        assert unauthorized.value.code==403
        with urlopen(Request(base+'/api/benchmarks/measured/designs',headers=headers,data=saved_payload)) as response:
            assert json.load(response)['saved']['design']['template']=='measured-regression'
        with urlopen(Request(base+'/api/benchmarks/studio/draft',headers=headers,data=json.dumps(design).encode())) as response:
            assert json.load(response)['draft']==design
        with urlopen(Request(base+'/api/benchmarks/studio',headers=headers)) as response:
            assert json.load(response)['draft']==design
        with urlopen(Request(base+'/api/benchmarks/studio/preview',headers=headers,data=json.dumps(design).encode())) as response:
            assert json.load(response)['requests']==10
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
