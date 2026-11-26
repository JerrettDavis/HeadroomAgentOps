"""Durable measured experiment groups over the existing isolated CLI runners."""
from __future__ import annotations

import hashlib
import copy
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .benchmark_experiments import FEATURES, PROVIDERS, SCENARIOS, preflight, preview, validate
from .benchmarks.cli import load_config, main as cli_main, save_json
from .benchmarks.core import digest, validate_manifest
from .benchmarks.runner import write_evidence


def freeze(payload,service):
    report=preflight(payload,service)
    if not report['ready']:
        failed=[c['description'] for c in report['checks'] if not c['ready']]
        raise ValueError('Preflight needs attention: '+'; '.join(dict.fromkeys(failed)))
    design=report['design']
    if report.get('runner_identity'):
        design['runner']['image']=report['runner_identity']
    path=design['runner'].get('config_path')
    if path:
        design['runner']['config_path']=str(Path(path).expanduser().resolve(strict=True))
        if Path(path).stat().st_size>1024*1024:
            raise ValueError('Runner configuration exceeds supported limits')
    from .benchmarks.environment import agent_identity
    return {'schema_version':1,'kind':'studio-experiment-group','template_version':1,
            'design':design,'resolved':report['resolved'],'cells':report['cells'],
            'lane':report['lane'],'charges':report['charges'],'config_digest':digest(load_config(path)),
            'tool_versions':{tool:runtime['version'] for tool,runtime in report['tool_runtimes'].items()},
            'tool_runtimes':report['tool_runtimes'],'engine_identity':agent_identity(),'scenario_version':1}


def _write(directory,state):
    from .benchmark_service import _write as atomic
    atomic(Path(directory)/'group-state.json',state)


def _verified(directory):
    """A reopened terminal group is evidence, never a reason to rerun requests."""
    directory=Path(directory)
    summary=json.loads((directory/'summary.json').read_text('utf-8'))
    name=summary['result_file']
    if Path(name).name!=name:
        raise ValueError('Invalid group evidence path')
    path=directory/name
    if path.is_symlink():
        raise ValueError('Invalid group evidence path')
    raw=path.read_bytes()
    result=json.loads(raw)
    expected=summary['result_sha256']
    if 'sha256:'+hashlib.sha256(raw).hexdigest()!=expected or digest(result)!=expected:
        raise ValueError('Group evidence could not be verified')
    return result


def prepare_recovery(payload,service):
    """Explicit retry/collection preserves verified cells and never replays unknown work."""
    if not isinstance(payload,dict) or set(payload)!={'id','mode','cells'} or payload['mode'] not in {'retry','collect'}:
        raise ValueError('Choose an experiment, selected cells and retry or collect')
    job=service._prior(payload['id'],'group')
    if job['state'] not in {'completed','cancelled','failed','interrupted'}:
        raise ValueError('Wait for the current group process to stop before recovery')
    from .benchmark_service import _alive
    if job['state'] in {'failed','interrupted'} and (_alive(job.get('supervisor_pid')) or _alive(job.get('child_pid'))):
        raise ValueError('Owned group work may still be running; requests were not replayed')
    previous=service.root/job['id']
    plan=json.loads((previous/'group-input.json').read_text('utf-8'))
    if digest(plan)!=job.get('group_input_digest'):
        raise ValueError('Previous group input could not be verified')
    from .benchmarks.environment import agent_identity
    if payload['mode']=='retry' and agent_identity()!=plan['engine_identity']:
        raise ValueError('Benchmark engine changed; edit a new experiment copy instead of mixing engine versions')
    result=_verified(previous) if (previous/'summary.json').is_file() else None
    progress=(json.loads((previous/'group-state.json').read_text('utf-8')) if (previous/'group-state.json').is_file()
              else {'manifest_digest':digest(plan),'cells':[dict(c,state='pending') for c in plan['cells']]})
    if progress.get('manifest_digest')!=digest(plan):
        raise ValueError('Previous group progress belongs to a different input')
    cells=payload['cells']
    known={c['id'] for c in plan['cells']}
    if not isinstance(cells,list) or not cells or any(not isinstance(c,str) or c not in known for c in cells) or len(set(cells))!=len(cells):
        raise ValueError('Select unique cells from the original experiment')
    if result and result['group_manifest_digest']!=digest(plan):
        raise ValueError('Previous group evidence does not match its input')
    source_cells={c['id']:c for c in (result['cells'] if result else progress['cells'])}
    inherited=[]
    for identity,cell in source_cells.items():
        if (identity in cells and payload['mode']=='retry') or cell.get('state')!='completed':
            continue
        if (digest(cell.get('evidence'))!=cell.get('evidence_sha256')
                or cell.get('manifest_digest')!=cell.get('evidence',{}).get('manifest_digest')):
            raise ValueError('A reused cell lacks verified terminal evidence')
        inherited.append(dict(cell,reused=True,reused_from=job['id']))
    recovered=copy.deepcopy(plan)
    recovered.pop('recovery',None)
    recovered['design']['label']=(plan['design']['label'][:108]+' · '+payload['mode'])
    # Use the group's own verified input copy rather than an external path that
    # may since have been deleted, rebuilt or replaced.
    for key,source in recovered['resolved'].items():
        local=previous/'inputs'/key/Path(source['artifact']).name
        from .benchmarks.local import verify_artifact
        verify_artifact(local,source['artifact_sha256'])
        source['artifact']=str(local)
        recovered['design'][key]={'kind':'local','artifact':str(local),'sha':source['sha']}
    base_path=previous/'base-runner.json'
    if base_path.is_file():
        if digest(load_config(base_path))!=plan['config_digest']:
            raise ValueError('Saved runner configuration changed')
        recovered['design']['runner']['config_path']=str(base_path)
    if payload['mode']=='retry':
        fresh=preflight(recovered['design'],service)
        if not fresh['ready']:
            raise ValueError('Retry preflight needs attention: '+'; '.join(c['description'] for c in fresh['checks'] if not c['ready']))
    directories={}
    for identity in cells:
        old=source_cells.get(identity,{})
        origin=(old.get('execution_directory') or plan.get('recovery',{}).get('cell_directories',{}).get(identity)
                or str(previous/'cells'/identity))
        path=Path(origin).resolve()
        if not path.is_relative_to(service.root.resolve()) or path.name!=identity or path.parent.name!='cells':
            raise ValueError('Previous cell directory identity could not be verified')
        directories[identity]=str(path)
    recovered['recovery']={'source_group':job['id'],'source_manifest_digest':digest(plan),
                           'mode':payload['mode'],'selected':cells,'inherited':inherited,
                           'preserved':[dict(c,not_retried=True) for identity,c in source_cells.items()
                                        if identity not in cells and c.get('state')!='completed'],
                           'cell_directories':directories,
                           'previous_states':{c:source_cells.get(c,{}).get('state','pending') for c in cells}}
    if payload['mode']=='collect':
        recovered['charges']=False
    return recovered


def _collect_cell(cell,directory):
    """Collect an existing remote run; never launch replacement requests here."""
    from .benchmark_service import _result
    directory=Path(directory)
    results=directory/'results'
    receipt=next((results/name for name in ('docker-receipt.json','remote-receipt.json') if (results/name).is_file()),None)
    if receipt is None:
        collected_inspection=False
        for stage_name in ('inspection','baseline-inspection'):
            stage=directory/stage_name
            inspection_receipt=next((stage/name for name in ('docker-receipt.json','remote-receipt.json') if (stage/name).is_file()),None)
            if inspection_receipt:
                collected_inspection=True
                code=cli_main(['collect',str(inspection_receipt),'--output',str(stage)])
                if code!=0 or not (stage/'inspection.json').is_file():
                    return dict(cell,state='recoverable',error='Existing remote inspection has not established terminal proof; no replacement work was started')
        if collected_inspection:
            return dict(cell,state='ready_to_retry',error='Remote inspection completed. An explicit retry can now start the comparison.')
        return dict(cell,state='recoverable',error='No collectable run receipt; use an explicit retry for stopped local work')
    code=cli_main(['collect',str(receipt),'--output',str(results)])
    try:
        summary=json.loads((results/'summary.json').read_text('utf-8'))
        decision,path=_result(results,summary,code,False)
        manifest=json.loads((directory/'manifest.json').read_text('utf-8'))
        if path is None:
            raise ValueError('Missing terminal evidence')
        evidence=json.loads(Path(path).read_text('utf-8'))
        if evidence['manifest_digest']!=digest(manifest):
            raise ValueError('Collected run does not match its pinned manifest')
        return dict(cell,state='cancelled' if evidence.get('cancelled') else 'completed',
                    manifest_digest=digest(manifest),evidence_sha256=digest(evidence),decision=decision,
                    evidence=evidence,collected=True)
    except (OSError,ValueError,KeyError):
        return dict(cell,state='recoverable',error='Existing remote run has no verified terminal evidence yet; no requests were restarted')


def _transport(runner,provider_id):
    argv=['--transport',runner['transport']]
    for key in ('image','host','ssh_port','ssh_config','cpus','memory_gib'):
        if runner.get(key) not in (None,''):
            argv+=['--'+key.replace('_','-'),str(runner[key])]
    names=set(runner.get('env',[]))
    # Pass names, never values, to isolated Docker/SSH runners.
    provider=next(p for p in PROVIDERS if p['id']==provider_id)
    if provider['key_env']:
        names.add(provider['key_env'])
    if provider_id=='azure':
        names.update(('AZURE_OPENAI_ENDPOINT','AZURE_OPENAI_API_VERSION','OPENAI_TARGET_API_URL'))
    if provider_id=='openai':
        names.add('OPENAI_TARGET_API_URL')
    if provider_id=='bedrock':
        names.update(('AWS_REGION','AWS_DEFAULT_REGION','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY',
                      'AWS_SESSION_TOKEN','AWS_PROFILE','AWS_CONTAINER_CREDENTIALS_RELATIVE_URI','AWS_CONTAINER_CREDENTIALS_FULL_URI'))
    import os
    for name in sorted(names):
        if name in os.environ:
            argv+=['--env',name]
    return argv


def _config(plan,cell,base_config,source_key):
    provider=next(p for p in PROVIDERS if p['id']==cell['provider'])
    config=dict(base_config,protocol=provider['id'],provider_binding=provider['id'],request_model=cell.get('request_model') or cell['model'])
    if provider['key_env'] and not plan['resolved'][source_key]['simulator']:
        config['api_key_env']=provider['key_env']
    else:
        config.pop('api_key_env',None)
    config['extras']=list(dict.fromkeys([*config.get('extras',['proxy']),*(['bedrock'] if provider['id']=='bedrock' else [])]))
    args=list(config.get('proxy_args',[]))
    if provider['id']=='bedrock':
        # An explicit selected provider must override ambient proxy defaults.
        args+=['--backend','bedrock']
    if source_key=='candidate':
        args+=next(f['args'] for f in FEATURES if f['id']==cell['feature'])
    config['proxy_args']=args
    config['benchmark_tool']=cell['tool']
    runtime=plan.get('tool_runtimes',{}).get(cell['tool'],{})
    if cell['tool']!='native' and runtime.get('python'):
        config['tool_python']=runtime['python']
    else:
        config.pop('tool_python',None)
    return config


def _native_manifest(plan,cell,source_key,observed):
    design=plan['design']
    if observed['agent']!=plan['engine_identity']:
        raise ValueError('Benchmark engine changed after preflight; create a new experiment plan')
    tasks=next(s['tasks'] for s in SCENARIOS if s['id']==cell['scenario'])
    source=plan['resolved'][source_key]
    manifest={'schema_version':1,'experiment_id':design['label']+' / '+cell['id'],
              'seed':design['seed'],'repeats':design['repeats'],
              'candidate':{'sha':source['sha'],'artifact_sha256':source['artifact_sha256']},
              'rollout':{'registry_digest':observed['runtime']['rollout_registry_digest'],
                         'snapshot_digest':observed['runtime']['rollout_snapshot_digest']},
              'model':cell['model'],'agent':observed['agent'],'taskset_id':'studio-'+cell['scenario'],
              'taskset_revision':str(plan['scenario_version']),'tasks':tasks,
              'environment':observed['environment'],'policy':design['policy']}
    validate_manifest(manifest)
    return manifest


def _cell(plan,cell,directory,artifact,base_config):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    design=plan['design']
    config=_config(plan,cell,base_config,'candidate')
    config_path=directory/'runner.json'
    save_json(config_path,config)
    shared=['--artifact',str(artifact),'--config',str(config_path),*_transport(design['runner'],cell['provider'])]
    inspection=directory/'inspection'
    inspect_code=cli_main(['inspect',*shared,'--sha256',plan['resolved']['candidate']['artifact_sha256'],
                           '--seed',str(design['seed']),'--output',str(inspection)])
    if inspect_code!=0:
        recoverable=any((inspection/name).is_file() for name in ('docker-receipt.json','remote-receipt.json'))
        return dict(cell,state='recoverable' if recoverable else 'failed',
                    error='Candidate inspection did not complete. Check runner prerequisites and private job diagnostics.')
    observed=json.loads((inspection/'inspection.json').read_text('utf-8'))
    manifest=_native_manifest(plan,cell,'candidate',observed)
    comparison_args=[]
    if 'baseline' in plan['resolved']:
        source=plan['resolved']['baseline']
        baseline_artifact=directory.parents[1]/'inputs'/'baseline'/Path(source['artifact']).name
        baseline_config=directory/'baseline-runner.json'
        save_json(baseline_config,_config(plan,cell,base_config,'baseline'))
        baseline_inspection=directory/'baseline-inspection'
        code=cli_main(['inspect','--artifact',str(baseline_artifact),'--config',str(baseline_config),
                       *_transport(design['runner'],cell['provider']),'--sha256',source['artifact_sha256'],
                       '--seed',str(design['seed']),'--output',str(baseline_inspection)])
        if code!=0:
            recoverable=any((baseline_inspection/name).is_file() for name in ('docker-receipt.json','remote-receipt.json'))
            return dict(cell,state='recoverable' if recoverable else 'failed',error='Baseline inspection did not complete; inspect runner prerequisites and retained receipts')
        baseline_observed=json.loads((baseline_inspection/'inspection.json').read_text('utf-8'))
        manifest={'schema_version':2,'experiment_id':design['label']+' / '+cell['id'],
                  'baseline':_native_manifest(plan,cell,'baseline',baseline_observed),'candidate':manifest,
                  'baseline_optimization':design['baseline_optimization'],
                  'candidate_optimization':design['candidate_optimization']}
        from .benchmarks.comparison import validate_comparison
        validate_comparison(manifest)
        comparison_args=['--baseline-artifact',str(baseline_artifact),'--baseline-config',str(baseline_config)]
    manifest_path=directory/'manifest.json'
    save_json(manifest_path,manifest)
    result_dir=directory/'results'
    exit_code=cli_main(['run',str(manifest_path),*shared,*comparison_args,'--lane',plan['lane'],'--output',str(result_dir)])
    from .benchmark_service import _result
    try:
        summary=json.loads((result_dir/'summary.json').read_text('utf-8'))
        decision,path=_result(result_dir,summary,exit_code,False)
        if path is None:
            raise ValueError('Unverified cell result')
        evidence=json.loads(Path(path).read_text('utf-8'))
        if evidence['manifest_digest']!=digest(manifest):
            raise ValueError('Cell evidence does not match its immutable manifest')
        if cell['tool']!='native':
            for attempt in evidence['attempts']:
                if attempt.get('error'):
                    continue
                runtime=attempt.get('tool_metrics',{})
                if runtime.get('tool')!=cell['tool'] or runtime.get('version')!=plan['tool_versions'][cell['tool']]:
                    raise ValueError('Tool runtime evidence differs from the pinned experiment')
    except (ValueError,KeyError,OSError):
        recoverable=any((result_dir/name).is_file() for name in ('docker-receipt.json','remote-receipt.json'))
        return dict(cell,state='recoverable' if recoverable else 'failed',
                    error='No verified terminal cell evidence. Collect existing remote receipts before retrying requests.')
    return dict(cell,state='cancelled' if evidence.get('cancelled') else 'completed',
                manifest_digest=digest(manifest),evidence_sha256=digest(evidence),
                decision=decision,evidence=evidence)


def run_group(plan,directory):
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    design=validate(plan['design'])
    if (plan.get('kind')!='studio-experiment-group' or plan.get('schema_version')!=1
            or plan['cells']!=preview(design)['cells']):
        raise ValueError('Invalid immutable experiment group plan')
    if (directory/'summary.json').exists():
        existing=_verified(directory)
        if existing['group_manifest_digest']!=digest(plan):
            raise ValueError('Existing group belongs to a different immutable plan')
        return existing
    from .benchmarks.environment import agent_identity
    if plan.get('recovery',{}).get('mode')!='collect' and agent_identity()!=plan['engine_identity']:
        raise ValueError('Benchmark engine changed after preflight; no requests were started')
    manifest_path=directory/'group-manifest.json'
    if manifest_path.exists():
        if digest(json.loads(manifest_path.read_text('utf-8')))!=digest(plan):
            raise ValueError('Existing group plan changed; do not restart it')
        # This process has no proof that a prior running attempt stopped.
        raise ValueError('Existing nonterminal group requires explicit recovery; requests were not replayed')
    base_config=load_config(design['runner'].get('config_path'))
    if digest(base_config)!=plan['config_digest']:
        raise ValueError('Runner configuration changed after preflight')
    from .benchmarks.local import verify_artifact
    materialized={}
    for key,source in plan['resolved'].items():
        original=verify_artifact(source['artifact'],source['artifact_sha256'])
        inputs=directory/'inputs'/key
        inputs.mkdir(parents=True)
        artifact=inputs/original.name
        shutil.copyfile(original,artifact)
        verify_artifact(artifact,source['artifact_sha256'])
        materialized[key]=artifact
    save_json(manifest_path,plan)
    save_json(directory/'base-runner.json',base_config)
    cells=[dict(c,state='pending') for c in plan['cells']]
    recovery=plan.get('recovery',{})
    inherited={c['id']:c for c in recovery.get('inherited',[])}
    preserved={c['id']:c for c in recovery.get('preserved',[])}
    for index,cell in enumerate(cells):
        if cell['id'] in inherited:
            cells[index]=copy.deepcopy(inherited[cell['id']])
        elif cell['id'] in preserved:
            cells[index]=copy.deepcopy(preserved[cell['id']])
    state={'state':'running','manifest_digest':digest(plan),'cells':cells}
    _write(directory,state)
    cancelled=False
    for index,cell in enumerate(plan['cells']):
        if cell['id'] in inherited:
            continue
        if recovery and cell['id'] not in recovery['selected']:
            continue
        cells[index]=dict(cell,state='running')
        _write(directory,state)
        try:
            old_dir=recovery.get('cell_directories',{}).get(cell['id'])
            old_state=recovery.get('previous_states',{}).get(cell['id'])
            remote_unknown=old_dir and old_state!='completed' and any(
                (Path(old_dir)/stage/name).is_file() for stage in ('results','inspection','baseline-inspection')
                for name in ('docker-receipt.json','remote-receipt.json'))
            if recovery.get('mode')=='collect' or remote_unknown:
                cells[index]=_collect_cell(cell,old_dir)
                cells[index]['execution_directory']=old_dir
                if cells[index]['state']=='ready_to_retry' and recovery.get('mode')=='retry':
                    cells[index]=_cell(plan,cell,directory/'cells'/cell['id'],materialized['candidate'],base_config)
                    cells[index]['execution_directory']=str(directory/'cells'/cell['id'])
            else:
                cells[index]=_cell(plan,cell,directory/'cells'/cell['id'],materialized['candidate'],base_config)
                cells[index]['execution_directory']=str(directory/'cells'/cell['id'])
        except KeyboardInterrupt:
            cells[index]=dict(cell,state='cancelled')
        except (ValueError,KeyError,OSError,RuntimeError) as exc:
            cells[index]=dict(cell,state='failed',error='Cell setup/evidence failure: '+type(exc).__name__)
        _write(directory,state)
        if cells[index]['state'] in {'recoverable','cancelled'}:
            cancelled=cells[index]['state']=='cancelled'
            break
    complete=all(c['state']=='completed' for c in cells)
    statuses=[c.get('decision',{}).get('status','inconclusive') for c in cells]
    decision={'status':'inconclusive','reason':'Incomplete cells or insufficient qualification'}
    if complete and all(s in {'pass','fail'} for s in statuses) and plan['lane']=='live':
        decision={'status':'fail' if 'fail' in statuses else 'pass'}
    if plan['lane']=='simulator':
        decision={'status':'inconclusive','reason':'simulator is not live qualification evidence'}
    result={'schema_version':1,'kind':'studio-experiment-group','experiment_id':design['label'],
            'created_at':datetime.now(UTC).isoformat(),'group_manifest_digest':digest(plan),
            'design':design,'sources':plan['resolved'],'tool_versions':plan['tool_versions'],
            'cells':cells,'decision':decision,'lane':plan['lane'],'cancelled':cancelled}
    path=write_evidence(result,directory)
    save_json(directory/'summary.json',{'status':decision['status'],'lane':plan['lane'],
                                      'result_file':path.name,'result_sha256':digest(result)})
    state['state']='cancelled' if cancelled else 'completed' if complete else 'needs_attention'
    _write(directory,state)
    return result
