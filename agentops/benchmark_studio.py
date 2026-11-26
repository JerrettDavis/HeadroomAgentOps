"""Bundled Studio catalogue and explicit, credential-free paired demonstrations.

Demonstrations exercise scheduling/evaluation/evidence storage, not a model or
Headroom installation. They can never qualify measured provider performance.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .benchmarks.core import ARMS, digest, validate_manifest
from .benchmarks.runner import expected_identity, run_experiment, write_evidence

DESIGN_LIBRARY_BYTES=2*1024*1024

SCENARIOS = [
    {'id': 'conversation', 'name': 'Conversation', 'description': 'Short multi-turn support requests.', 'tokens': 400},
    {'id': 'documents', 'name': 'Long documents', 'description': 'Repeated context in document questions.', 'tokens': 4000},
    {'id': 'code', 'name': 'Code assistance', 'description': 'Code context with exact-answer checks.', 'tokens': 1200},
]
FEATURES = [
    {'id': 'compression', 'name': 'Context compression', 'description': 'Simulate fewer input tokens with retained answers.'},
    {'id': 'latency', 'name': 'Latency tradeoff', 'description': 'Simulate optimization overhead and compare paired latency.'},
]
MODELS = [
    {'id': 'demo-balanced', 'name': 'Balanced simulator'},
    {'id': 'demo-fast', 'name': 'Fast simulator'},
]
POLICY = {'version': 1, 'method': 'paired_t_95', 'minimum_pairs': 5,
          'minimum_savings': .2, 'maximum_accuracy_regression': 0.0,
          'maximum_latency_ratio': 1.2, 'minimum_accuracy': .9,
          'missing_data_status': 'inconclusive'}


def catalogue():
    base = {'template': 'introduction', 'label': 'My first comparison',
            'scenarios': ['conversation'], 'features': ['compression'],
            'tools': ['native'], 'provider': 'simulator', 'models': ['demo-balanced'],
            'seed': 42, 'repeats': 5}
    templates = []
    for identifier, name, description, overrides in [
        ('introduction', 'Your first comparison', 'See paired requests, token savings and a decision. No setup or charges.', {}),
        ('context-sweep', 'Explore context sizes', 'Compare conversations, documents and code with the same settings.', {'scenarios': [s['id'] for s in SCENARIOS]}),
        ('model-comparison', 'Compare model profiles', 'Explore a two-model matrix with simulated latency tradeoffs.', {'models': [m['id'] for m in MODELS], 'features': ['compression', 'latency']}),
    ]:
        templates.append({'id': identifier, 'version': 1, 'name': name,
                          'description': description, 'lane': 'simulator',
                          'design': dict(base, **overrides, template=identifier)})
    return copy.deepcopy({'version': 1, 'templates': templates, 'scenarios': SCENARIOS,
                          'features': FEATURES, 'models': MODELS,
                          'providers': [{'id': 'simulator', 'name': 'Offline simulator', 'available': True}],
                          'tools': [{'id': 'native', 'name': 'Native paired runner', 'available': True},
                                    {'id': 'inspect', 'name': 'Inspect AI', 'available': False, 'reason': 'Execution adapter not installed in Studio yet.'},
                                    {'id': 'aiperf', 'name': 'AIPerf', 'available': False, 'reason': 'Execution adapter not installed in Studio yet.'}],
                          'limits': {'cells': 24, 'requests': 1200, 'repeats': 100}})


def validate_design(payload):
    defaults = catalogue()['templates'][0]['design']
    if not isinstance(payload, dict) or set(payload) - set(defaults):
        raise ValueError('Unknown experiment settings')
    data = dict(defaults, **payload)
    if data['template'] not in {t['id'] for t in catalogue()['templates']}:
        raise ValueError('Unknown experiment template')
    if not isinstance(data['label'], str) or not 1 <= len(data['label'].strip()) <= 120 or any(ord(c) < 32 for c in data['label']):
        raise ValueError('Experiment name must contain 1–120 printable characters')
    data['label'] = data['label'].strip()
    for key, allowed, empty in [('scenarios', {s['id'] for s in SCENARIOS}, False),
                                ('features', {f['id'] for f in FEATURES}, True),
                                ('models', {m['id'] for m in MODELS}, False),
                                ('tools', {'native'}, False)]:
        values = data[key]
        if (not isinstance(values, list) or (not values and not empty)
                or any(not isinstance(v, str) or v not in allowed for v in values)
                or len(set(values)) != len(values)):
            raise ValueError('Choose supported, unique ' + key)
    if data['provider'] != 'simulator':
        raise ValueError('This template requires the offline simulator')
    if type(data['repeats']) is not int or not 5 <= data['repeats'] <= 100:
        raise ValueError('Repeats must be between 5 and 100')
    if type(data['seed']) is not int or not 0 <= data['seed'] <= 2**32 - 1:
        raise ValueError('Seed must be an integer between 0 and 4294967295')
    cells = len(data['scenarios']) * len(data['models']) * len(data['tools'])
    if cells > 24 or cells * data['repeats'] * 2 > 1200:
        raise ValueError('Experiment exceeds the 24-cell or 1200-request limit')
    return data


def preview(payload):
    design = validate_design(payload)
    cells = [{'id': f'{scenario}:{model}:native', 'scenario': scenario, 'model': model, 'tool': 'native'}
             for scenario in design['scenarios'] for model in design['models']]
    return {'design': design, 'cells': cells, 'requests': len(cells) * design['repeats'] * 2,
            'lane': 'simulator', 'charges': False,
            'notice': 'Synthetic demonstration. No provider requests. Results do not establish measured performance.'}


def demo(payload, directory):
    design = validate_design(payload)
    tasks = [{'id': cell['id'], 'messages': [{'role': 'user', 'content': 'Demonstration ' + cell['scenario']}],
              'expected': 'demonstration'} for cell in preview(design)['cells']]
    source = digest({'simulator': 1, 'scenarios': SCENARIOS, 'models': MODELS})
    manifest = {'schema_version': 1, 'experiment_id': design['label'],
                'seed': design['seed'], 'repeats': design['repeats'],
                'candidate': {'sha': hashlib.sha1(source.encode()).hexdigest(), 'artifact_sha256': source},
                'rollout': {'registry_digest': digest(FEATURES), 'snapshot_digest': digest(design['features'])},
                'model': 'studio-simulator-v1', 'agent': 'synthetic-paired-v1',
                'taskset_id': 'studio-demo-v1', 'taskset_revision': 'v1', 'tasks': tasks,
                'environment': {'runner': 'simulator', 'platform': 'portable', 'image': 'bundled-simulator-v1',
                                'resource_limits': {'cpu': 1, 'memory_gib': 1}}, 'policy': dict(POLICY)}
    validate_manifest(manifest)

    class Simulator:
        def attempt(self, experiment, task, arm):
            scenario, model, _ = task['id'].split(':')
            tokens = next(s['tokens'] for s in SCENARIOS if s['id'] == scenario)
            optimized = arm == ARMS[1]
            compression = optimized and 'compression' in design['features']
            overhead = 1.08 if optimized and 'latency' in design['features'] else 1
            return {'identity': expected_identity(experiment), 'optimization_enabled': optimized,
                    'success': True, 'tokens': int(tokens * (.7 if compression else 1)),
                    'latency_ms': (80 if model == 'demo-fast' else 160) * overhead}

    result = run_experiment(manifest, Simulator())
    result.update(lane='simulator', studio={'design': design, 'template_version': 1,
                  'cells': preview(design)['cells'], 'manifest': manifest,
                  'notice': preview(design)['notice']})
    result['decision'] = {'status': 'inconclusive', 'reason': 'simulator is not live qualification evidence',
                          'simulated_decision': result['decision']}
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = write_evidence(result, directory)
    (directory / 'summary.json').write_text(json.dumps({'status': 'inconclusive', 'lane': 'simulator',
        'result_file': path.name, 'result_sha256': digest(result)}), encoding='utf-8')
    return result


class Studio:
    """Private panel facade; durable runs remain owned by BenchmarkService."""

    def __init__(self, service):
        self.service = service
        self.draft_path = service.root / 'studio-draft.json'

    def state(self):
        draft = None
        if self.draft_path.is_file():
            draft = validate_design(json.loads(self.draft_path.read_text('utf-8')))
        state = self.service.status()
        from .benchmark_resolver import STAGES
        sources=[]
        for job in state['jobs']:
            if job['action'] != 'source':
                continue
            progress = None
            try:
                progress = STAGES.get(json.loads((self.service.root / job['id'] / 'source-progress.json').read_text('utf-8')).get('stage'))
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            sources.append(dict(job, progress=progress))
        return {'catalogue': catalogue(), 'draft': draft, 'active': state['active'],
                'sources': sources,
                'jobs': [j for j in state['jobs'] if j['action'] == 'demo']}

    def measured_state(self):
        from .benchmark_experiments import catalogue as measured_catalogue, validate
        draft_path=self.service.root/'measured-draft.json'
        draft=validate(json.loads(draft_path.read_text('utf-8'))) if draft_path.is_file() else None
        state=self.service.status()
        groups=[]
        setups=[]
        for job in state['jobs']:
            if job['action']=='tool':
                ready=None
                try:
                    path=self.service.root/job['id']/'tool-setup.json'
                    if path.stat().st_size<=65536:
                        ready=json.loads(path.read_text('utf-8'))
                except (OSError,ValueError,TypeError):
                    pass
                setups.append(dict(job,setup=ready,collectable=(self.service.root/job['id']/'tool-setup-receipt.json').is_file()))
            if job['action']!='group':
                continue
            cells=[]
            group_state=job['state']
            try:
                progress=json.loads((self.service.root/job['id']/'group-state.json').read_text('utf-8'))
                group_state=progress['state']
                fields={'id','tool','scenario','feature','provider','model','concurrency','state','decision','error','reused'}
                cells=[{k:v for k,v in c.items() if k in fields} for c in progress['cells']]
            except (OSError,KeyError,ValueError,TypeError):
                try:
                    plan=json.loads((self.service.root/job['id']/'group-input.json').read_text('utf-8'))
                    if digest(plan)==job.get('group_input_digest'):
                        cells=[dict(c,state='pending') for c in plan['cells']]
                except (OSError,KeyError,ValueError,TypeError):
                    pass
            groups.append(dict(job,cells=cells,group_state=group_state))
        return {'catalogue':measured_catalogue(),'draft':draft,'designs':self._measured_designs(),'groups':groups,'active':state['active'],'tool_setups':setups,
                'sources':[j for j in state['jobs'] if j['action']=='source' and j['state']=='completed']}

    def measured_draft(self,payload):
        from .benchmark_experiments import validate
        from .benchmark_service import _guard,_write
        design=validate(payload)
        with _guard(self.service.root):
            _write(self.service.root/'measured-draft.json',design)
        return {'draft':design}

    def _measured_designs(self):
        from .benchmark_experiments import validate
        path=self.service.root/'measured-designs.json'
        if not path.is_file():
            return []
        if path.stat().st_size>DESIGN_LIBRARY_BYTES:
            raise ValueError('Saved design library exceeds its supported size')
        records=json.loads(path.read_text('utf-8'))
        if not isinstance(records,list) or len(records)>100:
            raise ValueError('Invalid saved design library')
        seen=set()
        for record in records:
            if not isinstance(record,dict) or set(record)!={'id','design','created_at'}:
                raise ValueError('Invalid saved experiment design')
            design=validate(record['design'])
            if record['id']!=digest(record['design']) or record['id'] in seen or not isinstance(record['created_at'],str):
                raise ValueError('Saved experiment design identity differs')
            seen.add(record['id'])
        return records

    def measured_library(self,payload):
        from datetime import datetime,UTC
        from .benchmark_experiments import validate
        from .benchmark_service import _guard,_write
        if not isinstance(payload,dict):
            raise ValueError('Choose a design library action')
        action=payload.get('action')
        if action=='save' and set(payload)=={'action','design'}:
            design=validate(payload['design'])
            identity=digest(design)
        elif action=='remove' and set(payload)=={'action','id'} and isinstance(payload['id'],str):
            identity=payload['id']
        else:
            raise ValueError('Choose save or remove with an exact design identity')
        with _guard(self.service.root):
            records=self._measured_designs()
            saved=next((r for r in records if r['id']==identity),None)
            if action=='save' and saved is None:
                if len(records)>=100:
                    raise ValueError('Design library is full; remove an unused saved design')
                saved={'id':identity,'design':design,'created_at':datetime.now(UTC).isoformat()}
                records.insert(0,saved)
            elif action=='remove':
                if saved is None:
                    raise ValueError('Saved design is unavailable; refresh the library')
                records.remove(saved)
            if len(json.dumps(records,allow_nan=False).encode('utf-8'))>DESIGN_LIBRARY_BYTES:
                raise ValueError('Saved design library exceeds its supported size; remove an unused design')
            _write(self.service.root/'measured-designs.json',records)
        return {'designs':records,**({'saved':saved} if action=='save' else {})}

    def group_result(self,identifier):
        from .benchmark_service import _result
        job=self.service._prior(identifier,'group')
        if job['state'] not in {'completed','cancelled'}:
            raise ValueError('Verified results are available after the group process stops')
        directory=self.service.root/identifier
        summary=json.loads((directory/'summary.json').read_text('utf-8'))
        qualification,path=_result(directory,summary,job.get('exit_code'),job['state']=='cancelled')
        if path is None:
            raise ValueError('Group result could not be verified')
        result=json.loads(Path(path).read_text('utf-8'))
        if result.get('kind')!='studio-experiment-group' or result['group_manifest_digest']!=job.get('group_input_digest'):
            raise ValueError('Group input/evidence identity mismatch')
        return {'id':identifier,'result':result,'qualification':qualification,'sha256':digest(result)}

    def group_plan(self,identifier):
        job=self.service._prior(identifier,'group')
        plan=json.loads((self.service.root/identifier/'group-input.json').read_text('utf-8'))
        if digest(plan)!=job.get('group_input_digest'):
            raise ValueError('Saved group input could not be verified')
        progress=next((g for g in self.measured_state()['groups'] if g['id']==identifier),None)
        return {'id':identifier,'design':plan['design'],'cells':progress['cells'] if progress else plan['cells'],
                'state':job['state'],'manifest_digest':digest(plan)}

    def save_draft(self, payload):
        from .benchmark_service import _guard, _write
        design = validate_design(payload)
        with _guard(self.service.root):
            _write(self.draft_path, design)
        return {'draft': design}

    def result(self, identifier):
        from .benchmark_service import _result
        job = self.service._prior(identifier, 'demo')
        if job['state'] != 'completed':
            raise ValueError('Results are available after completion')
        directory = self.service.root / identifier
        summary = json.loads((directory / 'summary.json').read_text('utf-8'))
        _, verified_path = _result(directory, summary, job['exit_code'], False)
        if verified_path is None:
            raise ValueError('Result evidence could not be verified')
        result = json.loads(Path(verified_path).read_text('utf-8'))
        return {'id': identifier, 'result': result, 'sha256': digest(result)}

    def source(self, identifier):
        from .benchmark_resolver import validate_source
        job = self.service._prior(identifier, 'source')
        if job['state'] != 'completed':
            raise ValueError('Wait for source preparation to complete')
        directory = self.service.root / identifier
        record = json.loads((directory / 'source.json').read_text('utf-8'))
        if digest(record) != (directory / 'source.sha256').read_text('utf-8'):
            raise ValueError('Source provenance could not be verified')
        request = validate_source(record['request'])
        if request != json.loads(job['argv'][2]):
            raise ValueError('Source request differs from the selected immutable source')
        wheel = (directory / record['wheel']).resolve()
        if (not wheel.is_relative_to(directory.resolve()) or not wheel.is_file()
                or wheel.suffix != '.whl' or wheel.stat().st_size > 256 * 1024 * 1024
                or 'sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest() != record['sha256']):
            raise ValueError('Candidate wheel could not be verified')
        return {'id': identifier, 'artifact': str(wheel), 'sha': request['sha'],
                'repository': request['repository'],
                'wheel_name': wheel.name, 'platform_tag': wheel.stem.rsplit('-',1)[-1],
                'sha256': record['sha256'], 'provenance': record['provenance']}
