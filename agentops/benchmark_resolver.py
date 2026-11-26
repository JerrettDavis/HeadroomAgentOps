"""Explicit immutable source materialization. Discovery never invokes this module."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import uuid
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from .benchmark_sources import repository
from .benchmarks.core import digest

MAX_ARCHIVE = 256 * 1024 * 1024
STAGES = {'selecting':'Selecting a pinned source', 'downloading':'Downloading selected artifact',
          'checking':'Verifying artifact and wheel', 'cloning':'Fetching repository',
          'checkout':'Checking out the selected commit', 'builder':'Preparing isolated Docker builder',
          'building':'Building wheel in Docker', 'complete':'Verified wheel ready'}


def _progress(directory,stage):
    from .benchmark_service import _write
    _write(Path(directory)/'source-progress.json',{'stage':stage})


def validate_source(payload):
    if not isinstance(payload,dict) or set(payload)-{'kind','repository','sha','artifact_id'}:
        raise ValueError('Unknown source settings')
    source=dict(payload)
    repository(source.get('repository'))
    if not isinstance(source.get('sha'),str) or not re.fullmatch('[0-9a-f]{40}',source['sha']):
        raise ValueError('Select an immutable full commit SHA')
    if source.get('kind') not in {'artifact','build','prefer-artifact'}:
        raise ValueError('Choose an artifact or explicit commit build')
    if source['kind']=='artifact':
        if type(source.get('artifact_id')) is not int or not 1<=source['artifact_id']<=2**63-1:
            raise ValueError('Select a valid artifact identifier')
    elif 'artifact_id' in source:
        raise ValueError('Commit sources cannot specify an artifact identifier')
    return source


def extract_wheel(archive, directory):
    archive, directory = Path(archive), Path(directory)
    if archive.stat().st_size>MAX_ARCHIVE:
        raise ValueError('Artifact archive exceeds the 256 MiB limit')
    with ZipFile(archive) as zipped:
        entries=zipped.infolist()
        if len(entries)>5000 or sum(e.file_size for e in entries)>MAX_ARCHIVE:
            raise ValueError('Artifact expansion exceeds the supported limits')
        for entry in entries:
            raw_name=entry.orig_filename
            name=PurePosixPath(raw_name)
            if (name.is_absolute() or '..' in name.parts or ':' in raw_name
                    or '\\' in raw_name or stat.S_ISLNK(entry.external_attr>>16)):
                raise ValueError('Unsafe artifact archive path')
        wheels=[entry for entry in entries if not entry.is_dir() and entry.filename.endswith('.whl')]
        if len(wheels)!=1:
            raise ValueError('Select an artifact containing exactly one candidate wheel')
        filename=PurePosixPath(wheels[0].filename).name
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+\.whl',filename):
            raise ValueError('Unsupported wheel filename')
        directory.mkdir(parents=True,exist_ok=True)
        destination=directory/filename
        with destination.open('xb') as output, zipped.open(wheels[0]) as source:
            shutil.copyfileobj(source,output,1024*1024)
    return destination


def _command(argv, directory, *, output=None, timeout=300):
    """Private diagnostics only. No shell or credentials in arguments."""
    with (Path(directory)/'source-command.log').open('ab') as log:
        subprocess.run(argv,check=True,stdout=output if output is not None else log,
                       stderr=log,timeout=timeout,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def _artifact(source, directory, github):
    repo, identifier=source['repository'], source['artifact_id']
    metadata=github.request('GET',f'repos/{repo}/actions/artifacts/{identifier}')
    if metadata.get('id')!=identifier or metadata.get('expired'):
        raise ValueError('Selected artifact expired or is unavailable; select another artifact or build the commit')
    if (metadata.get('workflow_run') or {}).get('head_sha')!=source['sha']:
        raise ValueError('Artifact does not belong to the selected commit')
    size=metadata.get('size_in_bytes')
    if type(size) is not int or not 0<size<=MAX_ARCHIVE:
        raise ValueError('Artifact size exceeds supported limits')
    archive=directory/'artifact.zip'
    _progress(directory,'downloading')
    with archive.open('xb') as stream:
        _command(['gh','api',f'repos/{repo}/actions/artifacts/{identifier}/zip'],directory,output=stream,timeout=180)
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE:
        raise ValueError('Downloaded artifact exceeds supported limits')
    _progress(directory,'checking')
    archive_digest='sha256:'+hashlib.sha256(archive.read_bytes()).hexdigest()
    if metadata.get('digest') and metadata['digest']!=archive_digest:
        raise ValueError('Downloaded artifact digest differs from GitHub metadata')
    wheel=extract_wheel(archive,directory/'wheel')
    return wheel, {'strategy':'artifact','artifact_id':identifier,'archive_sha256':archive_digest,
                   'upstream_digest_verified':bool(metadata.get('digest')),
                   'run_id':(metadata.get('workflow_run') or {}).get('id')}


def _build(source,directory):
    """Build only in an explicitly launched, bounded Docker container."""
    checkout=directory/'checkout'
    _progress(directory,'cloning')
    _command(['gh','repo','clone',source['repository'],str(checkout),'--','--no-checkout','--depth=1'],directory)
    _command(['git','-C',str(checkout),'fetch','--depth=1','origin',source['sha']],directory)
    _progress(directory,'checkout')
    _command(['git','-C',str(checkout),'checkout','--detach',source['sha']],directory)
    observed=subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True,timeout=30,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip()
    if observed!=source['sha']:
        raise ValueError('Checkout did not resolve to the selected commit')
    image='python:3.13-slim'
    _progress(directory,'builder')
    _command(['docker','pull',image],directory,timeout=300)
    image_id=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True,timeout=30,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).strip()
    if not re.fullmatch('sha256:[0-9a-f]{64}',image_id):
        raise ValueError('Docker did not provide an immutable builder identity')
    output=directory/'wheel'
    output.mkdir()
    name='agentops-build-'+uuid.uuid4().hex
    try:
        _progress(directory,'building')
        _command(['docker','run','--rm','--name',name,'--cpus','2','--memory','4g',
                  '--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges',
                  '--mount',f'type=bind,source={checkout},target=/src',
                  '--mount',f'type=bind,source={output},target=/out',
                  '--workdir','/src',image_id,'python','-m','pip','wheel','--no-deps',
                  '--disable-pip-version-check','--wheel-dir','/out','.'],directory,timeout=900)
    finally:
        try:
            _command(['docker','rm','--force',name],directory,timeout=30)
        except (OSError,subprocess.SubprocessError):
            pass
    wheels=list(output.glob('*.whl'))
    if len(wheels)!=1 or wheels[0].is_symlink() or wheels[0].stat().st_size>MAX_ARCHIVE:
        raise ValueError('Build must produce exactly one supported candidate wheel')
    return wheels[0], {'strategy':'build','builder_image':image_id,
                       'recipe':['python','-m','pip','wheel','--no-deps','--wheel-dir','/out','.']}


def resolve(payload,directory,github=None):
    source=validate_source(payload)
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    _progress(directory,'selecting')
    if github is None:
        from .github import GitHub
        github=GitHub(source['repository'])
    chosen=dict(source)
    if source['kind']=='prefer-artifact':
        from urllib.parse import urlencode
        runs=github.request('GET',f'repos/{source["repository"]}/actions/runs?'+urlencode({'head_sha':source['sha'],'status':'success','per_page':20}))
        if runs.get('total_count',0)>20:
            raise ValueError('More than 20 successful builds exist; select an artifact explicitly or choose Build this commit')
        found=[]
        for run in runs['workflow_runs']:
            if run.get('head_sha')!=source['sha']:
                continue
            artifacts=github.request('GET',f'repos/{source["repository"]}/actions/runs/{run["id"]}/artifacts?per_page=100')
            if artifacts.get('total_count',0)>100:
                raise ValueError('Build has more than 100 artifacts; select the desired artifact explicitly')
            found.extend(a for a in artifacts['artifacts'] if not a.get('expired') and 'wheel' in a.get('name','').lower())
        if len(found)>1:
            raise ValueError('Several wheel artifacts exist; choose one explicitly to preserve provenance')
        chosen=dict(source,kind='artifact',artifact_id=found[0]['id']) if found else dict(source,kind='build')
    wheel,provenance = _artifact(chosen,directory,github) if chosen['kind']=='artifact' else _build(chosen,directory)
    record={'schema_version':1,'request':source,'resolved':chosen,'provenance':provenance,
            'wheel':str(wheel.relative_to(directory)).replace('\\','/'),
            'sha256':'sha256:'+hashlib.sha256(wheel.read_bytes()).hexdigest()}
    (directory/'source.json').write_bytes((json.dumps(record,sort_keys=True,indent=2)+'\n').encode())
    (directory/'source.sha256').write_text(digest(record),encoding='utf-8')
    _progress(directory,'complete')
    return record
