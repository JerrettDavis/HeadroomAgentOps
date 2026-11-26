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
        return {'catalogue': catalogue(), 'draft': draft, 'active': state['active'],
                'jobs': [j for j in state['jobs'] if j['action'] == 'demo']}

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
