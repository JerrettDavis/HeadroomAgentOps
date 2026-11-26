import json
from pathlib import Path

from agentops.benchmark_groups import freeze,run_group,prepare_recovery
from agentops.benchmark_service import BenchmarkService,_write
from agentops.benchmarks.core import digest
from test_benchmark_groups import fixture_design


def record(service,identifier,plan,result=None):
    path=service.root/identifier
    path.mkdir(parents=True,exist_ok=True)
    _write(path/'group-input.json',plan)
    _write(path/'job.json',{'id':identifier,'action':'group','state':'completed','created_at':1,
                          'exit_code':2,'group_input_digest':digest(plan),'paths':{}})
    return path


def completed(cell):
    evidence={'manifest_digest':'sha256:'+'a'*64,'lane':'simulator','attempts':[],
              'decision':{'status':'inconclusive'}}
    return dict(cell,state='completed',manifest_digest=evidence['manifest_digest'],
                evidence_sha256=digest(evidence),evidence=evidence,decision={'status':'inconclusive'})


def test_explicit_retry_keeps_verified_complete_cells_without_replaying_them(tmp_path,monkeypatch):
    import agentops.benchmark_groups as groups
    service=BenchmarkService(tmp_path/'ledger.sqlite')
    data=fixture_design(tmp_path);data['scenarios']=['smoke','code']
    plan=freeze(data,service)
    original=record(service,'a'*32,plan)
    def first(plan,cell,*args):
        return completed(cell) if cell['scenario']=='smoke' else dict(cell,state='failed',error='Controlled failure')
    monkeypatch.setattr(groups,'_cell',first)
    result=run_group(plan,original)
    missing=next(c['id'] for c in result['cells'] if c['state']=='failed')
    retry=prepare_recovery({'id':'a'*32,'mode':'retry','cells':[missing]},service)
    calls=[]
    def rerun(plan,cell,*args):
        calls.append(cell['id']);return completed(cell)
    monkeypatch.setattr(groups,'_cell',rerun)
    second=run_group(retry,service.root/('b'*32))
    assert calls==[missing]
    assert sum(c.get('reused',False) for c in second['cells'])==1
    assert all(c['state']=='completed' for c in second['cells'])


def test_recovery_chain_preserves_unknown_remote_handle_and_never_blindly_replays(tmp_path,monkeypatch):
    import agentops.benchmark_groups as groups
    service=BenchmarkService(tmp_path/'ledger.sqlite')
    plan=freeze(fixture_design(tmp_path),service)
    original=record(service,'a'*32,plan)
    identity=plan['cells'][0]['id']
    def uncertain(plan,cell,directory,*args):
        receipt=Path(directory)/'results/docker-receipt.json'
        receipt.parent.mkdir(parents=True,exist_ok=True)
        _write(receipt,{'controlled':'unknown remote handle'})
        return dict(cell,state='recoverable')
    monkeypatch.setattr(groups,'_cell',uncertain)
    run_group(plan,original)
    first=prepare_recovery({'id':'a'*32,'mode':'collect','cells':[identity]},service)
    collected=[]
    def collect(cell,directory):
        collected.append(str(directory));return dict(cell,state='recoverable')
    monkeypatch.setattr(groups,'_collect_cell',collect)
    next_dir=record(service,'b'*32,first)
    run_group(first,next_dir)
    retry=prepare_recovery({'id':'b'*32,'mode':'retry','cells':[identity]},service)
    def forbidden(*args):
        raise AssertionError('Unknown remote work must not be replayed')
    monkeypatch.setattr(groups,'_cell',forbidden)
    run_group(retry,service.root/('c'*32))
    assert len(collected)==2
    assert collected[0]==collected[1]==str(original/'cells'/identity)


def test_remote_inspection_is_collected_before_any_explicit_retry(tmp_path,monkeypatch):
    import agentops.benchmark_groups as groups
    directory=tmp_path/'cell'
    receipt=directory/'inspection/remote-receipt.json'
    receipt.parent.mkdir(parents=True)
    _write(receipt,{'controlled':'inspection'})
    calls=[]
    def pending(argv):
        calls.append(argv);return 75
    monkeypatch.setattr(groups,'cli_main',pending)
    cell={'id':'cell'}
    assert groups._collect_cell(cell,directory)['state']=='recoverable'
    assert all(argv[0]=='collect' for argv in calls)
    def terminal(argv):
        calls.append(argv);_write(receipt.parent/'inspection.json',{'controlled':'terminal proof'});return 0
    monkeypatch.setattr(groups,'cli_main',terminal)
    assert groups._collect_cell(cell,directory)['state']=='ready_to_retry'
    assert all(argv[0]=='collect' for argv in calls)
