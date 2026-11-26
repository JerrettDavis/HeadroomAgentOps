import hashlib
import json
from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest

from agentops.benchmark_resolver import extract_wheel, validate_source


def archive(entries):
    buffer=BytesIO()
    with ZipFile(buffer,'w') as zipped:
        for name,data in entries:
            entry=ZipInfo()
            entry.filename=name
            entry.orig_filename=name
            zipped.writestr(entry,data)
    return buffer.getvalue()


def test_source_requires_immutable_commit_and_explicit_strategy():
    request={'kind':'artifact','repository':'owner/repo','sha':'a'*40,'artifact_id':12}
    assert validate_source(request)==request
    for changes in ({'sha':'main'},{'artifact_id':True},{'artifact_id':0},
                    {'repository':'https://host/repo'},{'kind':'shell'}, {'command':'build'}):
        with pytest.raises(ValueError):
            validate_source(dict(request,**changes))


def test_archive_extracts_one_wheel_and_pins_exact_bytes(tmp_path):
    content=b'candidate wheel bytes'
    data=archive([('nested/headroom-1.0-py3-none-any.whl',content)])
    path=tmp_path/'artifact.zip'
    path.write_bytes(data)
    wheel=extract_wheel(path,tmp_path/'output')
    assert wheel.name=='headroom-1.0-py3-none-any.whl'
    assert hashlib.sha256(wheel.read_bytes()).hexdigest()==hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize('entries', [
    [('../escape.whl',b'bad')], [('C:/escape.whl',b'bad')],
    [('folder\\escape.whl',b'bad')], [('/escape.whl',b'bad')],
    [('first.whl',b'a'),('second.whl',b'b')], [('readme.txt',b'no wheel')],
])
def test_ambiguous_or_unsafe_archives_are_rejected_without_extracting(tmp_path,entries):
    path=tmp_path/'artifact.zip'
    path.write_bytes(archive(entries))
    with pytest.raises(ValueError):
        extract_wheel(path,tmp_path/'output')
    assert not (tmp_path/'escape.whl').exists()


def test_artifact_resolution_checks_upstream_digest_and_retains_provenance(tmp_path,monkeypatch):
    import agentops.benchmark_resolver as resolver
    data=archive([('headroom-1.0-py3-none-any.whl',b'wheel bytes')])
    expected='sha256:'+hashlib.sha256(data).hexdigest()
    class Remote:
        def request(self,method,endpoint):
            return {'id':12,'expired':False,'size_in_bytes':len(data),'digest':expected,
                    'workflow_run':{'head_sha':'a'*40,'id':9}}
    monkeypatch.setattr(resolver,'_command',lambda argv,directory,output=None,timeout=None:output.write(data))
    result=resolver.resolve({'kind':'artifact','repository':'owner/repo','sha':'a'*40,'artifact_id':12},tmp_path,Remote())
    assert result['provenance']['upstream_digest_verified']
    assert result['provenance']['run_id']==9
    assert (tmp_path/result['wheel']).read_bytes()==b'wheel bytes'
    assert json.loads((tmp_path/'source.json').read_text())==result
    assert json.loads((tmp_path/'source-progress.json').read_text())['stage']=='complete'


@pytest.mark.parametrize('metadata', [
    {'expired':True}, {'workflow_run':{'head_sha':'b'*40}}, {'size_in_bytes':2**40},
])
def test_expired_mismatched_or_oversized_artifact_never_downloads(tmp_path,monkeypatch,metadata):
    import agentops.benchmark_resolver as resolver
    class Remote:
        def request(self,*args):
            return dict({'id':12,'expired':False,'size_in_bytes':100,'workflow_run':{'head_sha':'a'*40}},**metadata)
    def forbidden(*args,**kwargs):
        raise AssertionError('must reject before downloading')
    monkeypatch.setattr(resolver,'_command',forbidden)
    with pytest.raises(ValueError):
        resolver.resolve({'kind':'artifact','repository':'owner/repo','sha':'a'*40,'artifact_id':12},tmp_path,Remote())


def test_prepared_source_is_verified_before_reusing_in_a_run(tmp_path,monkeypatch):
    import agentops.benchmark_resolver as resolver
    from agentops.benchmark_service import BenchmarkService, _write
    from agentops.benchmark_studio import Studio
    data=archive([('headroom-1.0-py3-none-any.whl',b'candidate bytes')])
    class Remote:
        def request(self,*args):
            return {'id':12,'expired':False,'size_in_bytes':len(data),'workflow_run':{'head_sha':'a'*40}}
    monkeypatch.setattr(resolver,'_command',lambda argv,directory,output=None,timeout=None:output.write(data))
    service=BenchmarkService(tmp_path/'ledger.sqlite')
    identifier='a'*32
    directory=service.root/identifier
    source={'kind':'artifact','repository':'owner/repo','sha':'a'*40,'artifact_id':12}
    argv,paths=service._argv({'action':'source','source':source},directory)
    record=resolver.resolve(source,directory,Remote())
    _write(directory/'job.json',{'id':identifier,'action':'source','state':'completed',
        'created_at':'now','exit_code':0,'argv':argv,'paths':paths})
    selected=Studio(service).source(identifier)
    assert selected['sha']==source['sha']
    assert selected['sha256']==record['sha256']
    (directory/record['wheel']).write_bytes(b'changed wheel')
    with pytest.raises(ValueError,match='verified'):
        Studio(service).source(identifier)


def test_download_size_is_checked_before_hashing_or_opening_archive(tmp_path,monkeypatch):
    import agentops.benchmark_resolver as resolver
    monkeypatch.setattr(resolver,'MAX_ARCHIVE',100)
    class Remote:
        def request(self,*args):
            return {'id':12,'expired':False,'size_in_bytes':50,'workflow_run':{'head_sha':'a'*40}}
    monkeypatch.setattr(resolver,'_command',lambda argv,directory,output=None,timeout=None:output.write(b'x'*101))
    with pytest.raises(ValueError,match='limits'):
        resolver.resolve({'kind':'artifact','repository':'owner/repo','sha':'a'*40,'artifact_id':12},tmp_path,Remote())


def test_prefer_artifact_does_not_choose_from_an_incomplete_build_search(tmp_path,monkeypatch):
    import agentops.benchmark_resolver as resolver
    class Remote:
        def request(self,method,endpoint):
            if '/actions/runs?' in endpoint:
                return {'total_count':21,'workflow_runs':[{'id':9,'head_sha':'a'*40}]}
            return {'total_count':1,'artifacts':[{'id':12,'name':'wheel','expired':False}]}
    def forbidden(*args,**kwargs):
        raise AssertionError('Incomplete discovery cannot choose a unique source')
    monkeypatch.setattr(resolver,'_artifact',forbidden)
    monkeypatch.setattr(resolver,'_build',forbidden)
    with pytest.raises(ValueError,match='20'):
        resolver.resolve({'kind':'prefer-artifact','repository':'owner/repo','sha':'a'*40},tmp_path,Remote())
