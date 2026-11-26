"""Measured Studio configuration, capability checks and immutable matrix plans."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from .benchmarks.core import digest, evaluate

PROVIDERS = [
    {'id':'openai','name':'OpenAI-compatible chat','key_env':'OPENAI_API_KEY','model_hint':'Choose a pinned chat model. OPENAI_TARGET_API_URL can select a compatible gateway; model discovery lists the host OpenAI account.'},
    {'id':'anthropic','name':'Anthropic','key_env':'ANTHROPIC_API_KEY','model_hint':'Choose a pinned Claude model version.'},
    {'id':'azure','name':'Azure OpenAI','key_env':'AZURE_OPENAI_API_KEY','model_hint':'Use the model version returned by your Azure deployment.'},
    {'id':'bedrock','name':'Amazon Bedrock','key_env':None,'model_hint':'Use a Bedrock model ID available in your AWS region.'},
]
SCENARIOS = [
    {'id':'smoke','name':'Protocol smoke test','description':'A short, exact-answer request to verify the whole request path.',
     'tasks':[{'id':'arithmetic','messages':[{'role':'user','content':'What is 19 + 23? Reply with only the integer.'}],'expected':'42','max_tokens':32}]},
    {'id':'long-context','name':'Repeated context','description':'A document-like repeated context with a retained answer.',
     'tasks':[{'id':'release-code','messages':[{'role':'user','content':('This is supporting context for a release note. '*80)+'The release code is ORCHID-17. Reply with only the release code.'}],'expected':'ORCHID-17','max_tokens':64}]},
    {'id':'code','name':'Code comprehension','description':'An exact-answer code task to check retained behavior.',
     'tasks':[{'id':'python-sum','messages':[{'role':'user','content':'In Python, what does sum([10, 12, 20]) return? Reply with only the integer.'}],'expected':'42','max_tokens':32}]},
]
FEATURES = [
    {'id':'default','name':'Headroom defaults','description':'Use the selected build’s default optimization settings.','args':[]},
    {'id':'compress-user-messages','name':'Compress user messages','description':'Include user messages in context compression.','args':['--compress-user-messages']},
    {'id':'no-cache','name':'Disable optimization cache','description':'Measure without the proxy’s optimization cache.','args':['--no-cache']},
]
POLICY = {'version':1,'method':'paired_t_95','minimum_pairs':5,'minimum_savings':.2,
          'maximum_accuracy_regression':0.0,'maximum_latency_ratio':1.2,
          'minimum_accuracy':.9,'missing_data_status':'inconclusive'}
TOOLS = [{'id':'native','name':'Native paired runner','available':True,'version':'1'},
         {'id':'inspect','name':'Inspect AI','available':True,'version':'0.3.292','reason':'Pinned Inspect evaluation through the selected proxy. OpenAI/Azure, nonstreaming text.'},
         {'id':'aiperf','name':'AIPerf','available':True,'version':'0.14.0','reason':'Pinned single-request paired profiling. OpenAI/Azure, nonstreaming single-user text. No TTFT estimates.'}]


def catalogue():
    base={'template':'measured-regression','label':'Candidate regression',
          'candidate':{'kind':'prepared','job_id':''},'baseline':{'kind':'passthrough'},
          'targets':[{'provider':'openai','model':''}], 'tools':['native'],
        'scenarios':['smoke'],'features':['default'],'repeats':5,'seed':42,'load_concurrency':[],
          'baseline_optimization':True,'candidate_optimization':True,
          'runner':{'transport':'local','config_path':'','env':[]},'policy':dict(POLICY)}
    templates=[]
    for identifier,name,description,changes in [
        ('measured-regression','Measured regression','Compare an actual candidate with passthrough using exact-answer tasks.',{}),
        ('measured-context','Compression and quality','Compare short, long-context and code requests with the same provider.',{'scenarios':['smoke','long-context','code']}),
        ('measured-features','Feature comparison','Compare default settings and available cache/compression variants.',{'features':['default','no-cache']}),
        ('measured-builds','Compare two builds','Compare two immutable builds, each with its own observed runtime identity.',{'baseline':{'kind':'prepared','job_id':''}}),
        ('measured-quality','Quality retention','Check exact-answer task success alongside compression and latency, with zero allowed accuracy regression.',
         {'scenarios':['smoke','long-context','code'],'policy':dict(POLICY,maximum_accuracy_regression=0,minimum_accuracy=1)}),
        ('measured-providers','Provider and model comparison','Run the same paired tasks on independently configured provider/model targets.',
         {'targets':[{'provider':'openai','model':''},{'provider':'anthropic','model':''}]}),
        ('measured-load','Latency and throughput sweep','Measure bounded concurrent AIPerf requests at several load levels on each native optimization arm.',
         {'tools':['aiperf'],'repeats':20,'load_concurrency':[1,2,4]}),
    ]:
        templates.append({'id':identifier,'name':name,'description':description,'version':1,
                          'design':dict(base,**changes,template=identifier)})
    return copy.deepcopy({'version':1,'templates':templates,'providers':PROVIDERS,
        'scenarios':[{**{k:v for k,v in s.items() if k!='tasks'},'preview':copy.deepcopy(s['tasks'])} for s in SCENARIOS],
                          'features':FEATURES,'tools':TOOLS,'limits':{'cells':24,'requests':1200}})


def _text(value,label,maximum=200,empty=False):
    if not isinstance(value,str) or len(value)>maximum or (not empty and not value.strip()) or any(ord(c)<32 for c in value):
        raise ValueError('Invalid '+label)
    return value.strip()


def _source(value,baseline=False):
    if not isinstance(value,dict):
        raise ValueError('Choose a candidate source')
    if baseline and value=={'kind':'passthrough'}:
        return dict(value)
    if value.get('kind')=='prepared' and set(value)=={'kind','job_id'}:
        identifier=_text(value['job_id'],'prepared source',32,True)
        if identifier and not re.fullmatch('[a-f0-9]{32}',identifier):
            raise ValueError('Invalid prepared source identifier')
        return {'kind':'prepared','job_id':identifier}
    if value.get('kind')=='local' and set(value)=={'kind','artifact','sha'}:
        sha=_text(value['sha'],'commit SHA',40,True)
        if sha and not re.fullmatch('[a-f0-9]{40}',sha):
            raise ValueError('Choose the full immutable commit SHA')
        return {'kind':'local','artifact':_text(value['artifact'],'wheel path',4096,True),'sha':sha}
    raise ValueError('Unknown candidate source settings')


def validate(payload):
    defaults=catalogue()['templates'][0]['design']
    if not isinstance(payload,dict) or set(payload)-set(defaults):
        raise ValueError('Unknown measured experiment settings')
    data=copy.deepcopy(dict(defaults,**payload))
    if data['template'] not in {t['id'] for t in catalogue()['templates']}:
        raise ValueError('Unknown measured experiment template')
    data['label']=_text(data['label'],'experiment name',120)
    data['candidate']=_source(data['candidate'])
    data['baseline']=_source(data['baseline'],True)
    if type(data['baseline_optimization']) is not bool or type(data['candidate_optimization']) is not bool:
        raise ValueError('Optimization controls must be boolean')
    if data['baseline']['kind']=='passthrough':
        data['baseline_optimization']=False
    for key,allowed in [('tools',{t['id'] for t in TOOLS}),('scenarios',{s['id'] for s in SCENARIOS}),('features',{f['id'] for f in FEATURES})]:
        values=data[key]
        if not isinstance(values,list) or not values or any(not isinstance(v,str) or v not in allowed for v in values) or len(set(values))!=len(values):
            raise ValueError('Choose supported, unique '+key)
    if not isinstance(data['targets'],list) or not 1<=len(data['targets'])<=24:
        raise ValueError('Choose between 1 and 24 provider/model targets')
    targets=[]
    for target in data['targets']:
        if (not isinstance(target,dict) or not {'provider','model'}<=set(target)
                or set(target)-{'provider','model','request_model'} or not isinstance(target['provider'],str)
                or target['provider'] not in {p['id'] for p in PROVIDERS}):
            raise ValueError('Unknown provider/model target')
        row={'provider':target['provider'],'model':_text(target['model'],'model identifier',200,True)}
        if 'request_model' in target:
            row['request_model']=_text(target['request_model'],'request model/deployment',200,True)
        targets.append(row)
    if len({digest(t) for t in targets})!=len(targets):
        raise ValueError('Provider/model targets must be unique')
    data['targets']=targets
    if type(data['repeats']) is not int or not 5<=data['repeats']<=100 or type(data['seed']) is not int or not 0<=data['seed']<=2**32-1:
        raise ValueError('Use 5–100 repeats and an integer seed between 0 and 4294967295')
    levels=data['load_concurrency']
    if (not isinstance(levels,list) or len(levels)>8 or any(type(v) is not int or not 1<=v<=min(64,data['repeats']) for v in levels)
            or len(set(levels))!=len(levels)):
        raise ValueError('Choose up to eight unique load concurrency levels between 1 and the request count (maximum 64)')
    if levels and (data['tools']!=['aiperf'] or data['baseline']['kind']!='passthrough'
            or any(t['provider'] not in {'openai','azure'} for t in targets)):
        raise ValueError('Load sweeps require AIPerf, OpenAI/Azure targets and a same-build passthrough baseline')
    runner=data['runner']
    allowed={'transport','config_path','env','image','host','ssh_port','ssh_config','cpus','memory_gib'}
    if not isinstance(runner,dict) or set(runner)-allowed or runner.get('transport') not in {'local','docker','ssh'}:
        raise ValueError('Unknown runner settings')
    for key in ('config_path','image','host','ssh_config'):
        if key in runner:
            runner[key]=_text(runner[key],key,4096 if key in {'config_path','ssh_config'} else 200,True)
    if 'host' in runner and runner['host'] and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[A-Za-z0-9][A-Za-z0-9_.-]*)?',runner['host']):
        raise ValueError('Invalid SSH host')
    if 'ssh_port' in runner and (type(runner['ssh_port']) is not int or not 1<=runner['ssh_port']<=65535):
        raise ValueError('Invalid SSH port')
    for key in ('cpus','memory_gib'):
        if key in runner and (type(runner[key]) not in (int,float) or not 0<runner[key]<=1024):
            raise ValueError('Invalid runner resource limit')
    names=runner.get('env',[])
    if not isinstance(names,list) or len(names)>64 or any(not isinstance(n,str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',n) for n in names):
        raise ValueError('Use environment variable names only')
    runner['env']=sorted(set(names))
    # Use the same statistical validation as the native evaluator.
    evaluate([],data['policy'],5)
    cells=len(data['targets'])*len(data['tools'])*len(data['scenarios'])*len(data['features'])*max(1,len(levels))
    requests=sum(len(s['tasks']) for s in SCENARIOS if s['id'] in data['scenarios'])*len(data['targets'])*len(data['tools'])*len(data['features'])*max(1,len(levels))*data['repeats']*2
    if cells>24 or requests>1200:
        raise ValueError('Experiment exceeds the 24-cell or 1200-request limit')
    return data


def preview(payload):
    design=validate(payload)
    cells=[]
    for tool in design['tools']:
        for scenario in design['scenarios']:
            for feature in design['features']:
                for target in design['targets']:
                    for level in design['load_concurrency'] or [None]:
                        axes={'tool':tool,'scenario':scenario,'feature':feature,**target}
                        if level is not None:axes['concurrency']=level
                        cells.append(dict(axes,id=digest(axes).split(':')[1][:24]))
    task_counts={s['id']:len(s['tasks']) for s in SCENARIOS}
    return {'design':design,'cells':cells,'requests':sum(task_counts[c['scenario']] for c in cells)*design['repeats']*2,
            'charges':True,'notice':'Measured runs may send requests to the selected providers and incur usage charges.'}


def inspect_wheel(path):
    """Read declared CLI feature capabilities without importing candidate code."""
    flags=set()
    declarations={}
    simulator=False
    with zipfile.ZipFile(path) as archive:
        for item in archive.infolist():
            if item.filename.endswith('.dist-info/METADATA') and item.file_size<1024*1024:
                simulator |= b'Name: headroom-benchmark-fixture\n' in archive.read(item)
            if item.filename=='headroom/proxy/server.py' and item.file_size<4*1024*1024:
                try:
                    tree=ast.parse(archive.read(item).decode('utf-8'))
                    for node in ast.walk(tree):
                        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='add_argument':
                            action=next((kw.value.value for kw in node.keywords if kw.arg=='action' and isinstance(kw.value,ast.Constant)),None)
                            if action=='store_true':
                                names=[a.value for a in node.args if isinstance(a,ast.Constant) and isinstance(a.value,str)]
                                flags.update(names)
                                help_text=next((kw.value.value for kw in node.keywords if kw.arg=='help' and isinstance(kw.value,ast.Constant) and isinstance(kw.value.value,str)),'')
                                default_node=next((kw.value for kw in node.keywords if kw.arg=='default'),None)
                                default={} if default_node is not None else {'default':False}
                                if isinstance(default_node,ast.Constant) and type(default_node.value) is bool:
                                    default={'default':default_node.value}
                                for name in names:
                                    declarations[name]={'type':'boolean','enum':[False,True],**default,
                                        'cli_argument':name,'title':name.lstrip('-').replace('-',' ').capitalize(),'description':help_text}
                except (SyntaxError,UnicodeError):
                    pass
    supported=[f for f in FEATURES if not f['args'] or set(f['args'])<=flags]
    return {'features':[f['id'] for f in supported],
            'feature_schema':[{**copy.deepcopy(f),'configuration_schema':declarations.get(f['args'][0]) if f['args'] else None} for f in supported],
            'simulator':simulator,'sha256':'sha256:'+hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def resolve_source(source,service):
    from .benchmark_files import resolve_local_file
    if source['kind']=='prepared':
        if not source['job_id']:
            raise ValueError('Select a prepared wheel or choose a local wheel')
        from .benchmark_studio import Studio
        record=Studio(service).source(source['job_id'])
        path=resolve_local_file(record['artifact'],'wheel')
        sha=record['sha']
        provenance={**record['provenance'],'repository':record['repository'],'source_job_id':record['id']}
    else:
        path=resolve_local_file(source['artifact'],'wheel')
        sha=source['sha']
        provenance={'strategy':'local'}
        if not source['artifact'] or not sha:
            raise ValueError('Choose a wheel and its full source commit SHA')
    if not path.is_file() or path.suffix!='.whl' or path.stat().st_size>256*1024*1024:
        raise ValueError('Candidate wheel unavailable or exceeds supported limits')
    observed=inspect_wheel(path)
    return {'artifact':str(path),'sha':sha,'artifact_sha256':observed['sha256'],
            'features':observed['features'],'feature_schema':observed.get('feature_schema',[]),
            'simulator':observed['simulator'],'provenance':provenance}


def capabilities(payload,service):
    if not isinstance(payload,dict) or set(payload)!={'candidate'}:
        raise ValueError('Choose one candidate source for configuration discovery')
    source=resolve_source(_source(payload['candidate']),service)
    return {'sha':source['sha'],'artifact_sha256':source['artifact_sha256'],
            'features':source['feature_schema'],'supported_features':source['features']}


def preflight(payload,service):
    design=validate(payload)
    checks=[]
    resolved={}
    def check(identifier,ready,description):
        checks.append({'id':identifier,'ready':bool(ready),'description':description})
    for key in ('candidate','baseline'):
        if design[key]['kind']=='passthrough':
            continue
        try:
            resolved[key]=resolve_source(design[key],service)
            check(key,True,'Verified immutable wheel and source commit')
        except (ValueError,OSError,zipfile.BadZipFile,KeyError) as exc:
            check(key,False,str(exc) if isinstance(exc,ValueError) else 'Selected wheel is unavailable or invalid')
    simulator=bool(resolved.get('candidate',{}).get('simulator'))
    if 'baseline' in resolved:
        simulator |= bool(resolved['baseline']['simulator'])
        check('two-build',True,'Both immutable builds will be independently inspected and paired')
    elif not design['candidate_optimization']:
        check('optimization',False,'Use two-build comparison mode to explicitly disable candidate optimization')
    all_simulator=bool(resolved) and all(source['simulator'] for source in resolved.values())
    for key,source in resolved.items():
        missing=(set(design['features']) if key=='candidate' else {'default'})-set(source['features'])
        check('features:'+source['artifact_sha256'],not missing,'Supported by the selected build' if not missing else 'Unsupported feature variants: '+', '.join(sorted(missing)))
    runner=design['runner']
    credentials={name:bool(os.environ.get(name)) for name in (
        'OPENAI_API_KEY','ANTHROPIC_API_KEY','AZURE_OPENAI_API_KEY','AZURE_OPENAI_ENDPOINT','OPENAI_TARGET_API_URL',
        'AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_PROFILE','AWS_CONTAINER_CREDENTIALS_RELATIVE_URI',
        'AWS_CONTAINER_CREDENTIALS_FULL_URI')}
    credentials['aws_profile_file']=(Path.home()/'.aws/credentials').is_file()
    if runner['transport']=='ssh' and not all_simulator:
        try:
            from types import SimpleNamespace
            from .benchmarks.transports import ssh_command
            script='\n'.join([
                'import json,os,sys', 'from pathlib import Path',
                'path=Path.home()/".config/headroom-benchmark/env.json"', 'stored={}',
                'if path.is_file():',
                ' if path.stat().st_mode & 0o077: raise ValueError("Remote environment must have mode 0600")',
                ' stored=json.loads(path.read_text())',
                ' if not isinstance(stored,dict) or any(not isinstance(v,str) for v in stored.values()): raise ValueError("Invalid remote environment")',
                'names='+repr([n for n in credentials if n!='aws_profile_file']),
                'data={n:bool(stored.get(n,os.getenv(n))) for n in names}',
                'data["aws_profile_file"]=(Path.home()/".aws/credentials").is_file()',
                'data["python_ready"]=sys.version_info>=(3,12)', 'print(json.dumps(data))'])
            command=ssh_command(SimpleNamespace(host=runner.get('host',''),ssh_port=runner.get('ssh_port',22),
                                                ssh_config=runner.get('ssh_config') or None),['python3','-c',script])
            response=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True,
                                    timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if len(response.stdout)>16384:
                raise ValueError('Invalid remote readiness response')
            observed=json.loads(response.stdout)
            credentials={name:observed.get(name) is True for name in credentials}
            check('ssh-runtime',observed.get('python_ready') is True,'Remote runner Python 3.12+ readiness checked')
        except (OSError,ValueError,subprocess.SubprocessError):
            credentials={name:False for name in credentials}
            check('ssh-runtime',False,'Could not verify remote Python/credentials. Check SSH access and runner configuration')
    for target in design['targets']:
        provider=next(p for p in PROVIDERS if p['id']==target['provider'])
        check('model:'+digest(target),bool(target['model']),'A pinned response model identifier is required')
        if provider['key_env']:
            configured=credentials[provider['key_env']]
            check('credentials:'+provider['id'],configured or all_simulator,
                  'Offline fixture; no provider credentials required' if all_simulator else provider['key_env']+(' is configured on the runner' if configured else ' is not configured on the runner'))
            if provider['id']=='azure':
                check('azure-endpoint',credentials['AZURE_OPENAI_ENDPOINT'] or credentials['OPENAI_TARGET_API_URL'] or all_simulator,
                      'Configure AZURE_OPENAI_ENDPOINT or the v1-compatible OPENAI_TARGET_API_URL on the runner')
        else:
            configured=credentials['AWS_ACCESS_KEY_ID'] and credentials['AWS_SECRET_ACCESS_KEY']
            configured |= credentials['AWS_CONTAINER_CREDENTIALS_RELATIVE_URI'] or credentials['AWS_CONTAINER_CREDENTIALS_FULL_URI']
            if runner['transport']!='docker':
                configured |= credentials['AWS_PROFILE'] or credentials['aws_profile_file']
            check('credentials:bedrock',configured or all_simulator,'AWS credentials/profile required on the runner' if not all_simulator else 'Offline fixture; no provider credentials required')
    runner_identity=None
    if runner['transport']=='docker':
        try:
            if not runner.get('image'):
                raise ValueError('Choose a runner image')
            response=subprocess.run(['docker','image','inspect','--format','{{.Id}}',runner['image']],
                                    check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=15,
                                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            observed=response.stdout.decode('utf-8').strip()
            if not re.fullmatch('sha256:[a-f0-9]{64}',observed):
                raise ValueError('Invalid image identity')
            runner_identity=observed
            check('runner',True,'Docker runner image resolved to immutable content identity')
        except (OSError,ValueError,subprocess.SubprocessError):
            check('runner',False,'Choose an available benchmark runner image and verify Docker is running')
    elif runner['transport']=='ssh':
        check('runner',bool(shutil.which('ssh') and runner.get('host')),'SSH and a configured benchmark host are required')
    else:
        check('runner',True,'Local runner selected')
    base_config={}
    if runner.get('config_path'):
        try:
            from .benchmarks.cli import load_config
            base_config=load_config(runner['config_path'])
            check('config',True,'Runner configuration validated')
        except (ValueError,OSError,KeyError):
            check('config',False,'Runner configuration is unavailable or invalid')
    runtimes={}
    for tool in design['tools']:
        if tool=='native':
            runtimes[tool]={'available':True,'version':'1'}
            check('tool:native',True,'Native paired execution available')
            continue
        if runner['transport']=='local' and base_config.get('tool_python'):
            from .benchmarks.tools import available
            runtime=available(tool,{'python':base_config['tool_python']})
        else:
            from .benchmark_tool_setup import runtime as check_runtime
            runtime=check_runtime(tool,service.root/'.tools',runner,runner_identity,refresh=True)
        runtimes[tool]=runtime
        check('tool:'+tool,runtime['available'],runtime.get('reason') or 'Pinned '+tool+' runtime verified on the selected runner')
        compatible=all(t['provider'] in {'openai','azure'} for t in design['targets'])
        check('tool-protocol:'+tool,compatible,'This tool adapter uses OpenAI/Azure nonstreaming chat; use the native runner for Anthropic/Bedrock')
        selected_tasks=[task for s in SCENARIOS if s['id'] in design['scenarios'] for task in s['tasks']]
        supported=all(not task.get('stream') and not task.get('tools') and (tool!='aiperf' or
                      len(task['messages'])==1 and task['messages'][0].get('role')=='user') for task in selected_tasks)
        check('tool-scenarios:'+tool,supported,'Selected scenarios must be supported by every selected tool; incompatible cells are never silently omitted')
    return {**preview(design),'ready':all(c['ready'] for c in checks),'checks':checks,
            'resolved':resolved,'tool_runtimes':runtimes,'runner_identity':runner_identity,'lane':'simulator' if simulator else 'live','charges':not all_simulator}
