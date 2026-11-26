"""Native paired comparisons of two independently installed, pinned Headroom builds.

Exported observations always retain each build's own identity and optimization
state. Only after checking these does an internal statistical projection replace
identity with the comparison digest so the existing paired evaluator can compare
the measurements. That projection is never exported as observed evidence.
"""

import copy
import json
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

from .core import ARMS, digest, evaluate, finite_number, schedule, validate_manifest
from .local import LocalAdapter
from .runner import expected_identity, write_evidence, public_tool_metrics
from .workspace import Workspace

COMPARISON_ARMS = ('baseline', 'candidate')


def validate_comparison(data):
    """Validate the strict v2 envelope and each strict native v1 manifest."""
    fields = {'schema_version', 'experiment_id', 'baseline', 'candidate',
              'baseline_optimization', 'candidate_optimization'}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError('comparison must contain exactly the documented fields')
    if type(data['schema_version']) is not int or data['schema_version'] != 2:
        raise ValueError('unsupported comparison version')
    if not isinstance(data['experiment_id'], str) or not data['experiment_id'].strip():
        raise ValueError('comparison experiment_id must be nonempty')
    for arm in COMPARISON_ARMS:
        if type(data[arm + '_optimization']) is not bool:
            raise ValueError('comparison optimization controls must be boolean')
        validate_manifest(data[arm])
    for field in ('tasks', 'repeats', 'seed', 'model', 'policy', 'taskset_id', 'taskset_revision'):
        if digest(data['baseline'][field]) != digest(data['candidate'][field]):
            raise ValueError('comparison arms must share ' + field)


def _matches(observed, expected):
    try:
        return digest(observed) == digest(expected)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return False


def run_comparison(experiment, baseline_adapter, candidate_adapter):
    """Execute seeded pairs, checking each observation against its own manifest."""
    validate_comparison(experiment)
    experiment = copy.deepcopy(experiment)
    common = experiment['baseline']
    plan = schedule(common['tasks'], common['repeats'], common['seed'])
    names = dict(zip(ARMS, COMPARISON_ARMS))
    plan = [{**pair, 'arms': [names[arm] for arm in pair['arms']]} for pair in plan]
    tasks = {task['id']: task for task in common['tasks']}
    adapters = dict(zip(COMPARISON_ARMS, (baseline_adapter, candidate_adapter)))
    expected = {arm: expected_identity(experiment[arm]) for arm in COMPARISON_ARMS}
    mismatches = set()
    attempts = []
    cancelled = False
    for pair in plan:
        for arm in pair['arms']:
            base = {key: pair[key] for key in ('pair_id', 'task_id', 'repeat')}
            base['arm'] = arm
            row = dict(base)
            enabled = experiment[arm + '_optimization']
            try:
                observed = adapters[arm].attempt(copy.deepcopy(experiment[arm]),
                    copy.deepcopy(tasks[pair['task_id']]), ARMS[int(enabled)])
                if 'tool_metrics' in observed:
                    row['tool_metrics'] = public_tool_metrics(observed['tool_metrics'])
                identity = observed.get('identity')
                if not isinstance(identity, dict):
                    identity = {}
                row['identity'] = {}
                for key, value in expected[arm].items():
                    matching = _matches(identity.get(key), value)
                    row['identity'][key] = copy.deepcopy(value) if matching else None
                    if not matching:
                        mismatches.add(arm + '.' + key)
                for key in ('tokens', 'latency_ms'):
                    value = observed.get(key)
                    row[key] = value if finite_number(value) else None
                for key in ('success', 'optimization_enabled'):
                    value = observed.get(key)
                    row[key] = value if type(value) is bool else None
                if row['optimization_enabled'] is not enabled:
                    mismatches.add(arm + '.optimization_enabled')
            except KeyboardInterrupt:
                row = {**base, 'error': 'cancelled'}
                cancelled = True
                mismatches.add(arm + '.unobserved_attempt_identity')
            except Exception as exc:  # noqa: BLE001 -- preserve arbitrary adapter failures without messages
                row = {**base, 'error': type(exc).__name__}
                mismatches.add(arm + '.unobserved_attempt_identity')
            attempts.append(row)
            if cancelled:
                break
        if cancelled:
            break

    # Statistical projection only: per-arm provenance is verified above, and
    # exported attempts never use this synthetic common identity.
    if mismatches or cancelled:
        decision = {'status': 'inconclusive', 'reason': 'identity mismatch or cancellation'}
    else:
        projected = [{**row, 'arm': ARMS[COMPARISON_ARMS.index(row['arm'])],
                      'identity': {'verified_comparison': digest(experiment)}} for row in attempts]
        decision = evaluate(projected, common['policy'], expected_pairs=len(plan))
    simulator_arms = [arm for arm in COMPARISON_ARMS if getattr(adapters[arm], 'simulator', False)]
    if simulator_arms:
        decision = {'status': 'inconclusive', 'reason': 'simulator is not live qualification evidence',
                    'simulated_decision': decision}
    return {
        'schema_version': 2, 'experiment_id': experiment['experiment_id'],
        'created_at': datetime.now(UTC).isoformat(), 'manifest_digest': digest(experiment),
        'arm_manifest_digests': {arm: digest(experiment[arm]) for arm in COMPARISON_ARMS},
        'expected_identities': expected,
        'optimization': {arm: experiment[arm + '_optimization'] for arm in COMPARISON_ARMS},
        'taskset_digest': digest(common['tasks']), 'taskset_revision': common['taskset_revision'],
        'seed': common['seed'], 'schedule': plan, 'attempts': attempts,
        'policy': common['policy'], 'decision': decision, 'cancelled': cancelled,
        'simulator_arms': simulator_arms,
        'identity_verification': {'status': 'inconclusive' if mismatches else 'pass',
                                  'mismatches': sorted(mismatches)},
    }


def run_local_comparison(args):
    """Install both pinned artifacts separately and persist canonical evidence."""
    # Lazy import keeps the CLI free to import this handler during dispatch.
    from .cli import SetupFailure, load_config, load_json, save_json

    experiment = load_json(args.manifest)
    validate_comparison(experiment)
    candidate_config = load_config(getattr(args, 'config', None))
    configs = {'candidate': candidate_config,
               'baseline': load_config(args.baseline_config) if getattr(args, 'baseline_config', None)
               else copy.deepcopy(candidate_config)}
    if any(config.get('load_concurrency') for config in configs.values()):
        raise ValueError('Concurrent load execution currently requires a same-build passthrough baseline')
    artifacts = {'baseline': args.baseline_artifact, 'candidate': args.artifact}
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    for arm, config in configs.items():
        if config.get('benchmark_tool', 'native') != 'native':
            config['tool_artifacts_dir'] = str(output / 'tool-evidence' / arm)
    with ExitStack() as stack:
        adapters, errors = {}, {}
        for arm in COMPARISON_ARMS:
            workspace = stack.enter_context(Workspace(prefix=arm + '-', dir=output))
            try:
                adapters[arm] = LocalAdapter(experiment[arm], artifacts[arm], configs[arm], workspace)
            except KeyboardInterrupt:
                errors[arm] = 'cancelled'
                # Do not execute the other build after setup cancellation.
                adapters = {name: SetupFailure(KeyboardInterrupt()) for name in COMPARISON_ARMS}
                break
            except Exception as exc:  # noqa: BLE001 -- installer errors become terminal evidence
                adapters[arm] = SetupFailure(exc)
                errors[arm] = type(exc).__name__
        result = run_comparison(experiment, adapters['baseline'], adapters['candidate'])
        result['runner_config_digests'] = {
            arm: digest({key: value for key, value in configs[arm].items()
                         if key not in ('diagnostics_dir', 'wheelhouse', 'tool_artifacts_dir')}) for arm in COMPARISON_ARMS}
        lane = 'simulator' if result['simulator_arms'] else getattr(args, 'lane', 'live')
        result['lane'] = lane
        if errors:
            result['setup_errors'] = errors
        if lane == 'simulator' and not result['simulator_arms']:
            result['decision'] = {'status': 'inconclusive',
                'reason': 'simulator is not live qualification evidence',
                'simulated_decision': result['decision']}
        path = write_evidence(result, output)
    summary = {'status': result['decision']['status'], 'result_file': path.name,
               'result_sha256': digest(result), 'lane': lane}
    save_json(output / 'summary.json', summary)
    print(json.dumps(summary, sort_keys=True))
    return {'pass': 0, 'fail': 1, 'inconclusive': 2}[summary['status']]
