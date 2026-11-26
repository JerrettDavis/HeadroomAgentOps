"""Two-build comparisons keep both installed builds' observed provenance."""

import copy
import json
from types import SimpleNamespace

import pytest

from test_benchmark_core import manifest
from agentops.benchmarks.core import ARMS, digest
from agentops.benchmarks.runner import expected_identity


def comparison():
    baseline, candidate = manifest(), manifest()
    baseline['candidate'] = {'sha': 'f' * 40, 'artifact_sha256': 'sha256:' + 'e' * 64}
    baseline['rollout']['snapshot_digest'] = 'sha256:' + 'a' * 64
    baseline['environment']['image'] = 'baseline-image'
    return {'schema_version': 2, 'experiment_id': 'two-builds',
            'baseline': baseline, 'candidate': candidate,
            'baseline_optimization': True, 'candidate_optimization': True}


class Adapter:
    def __init__(self, tokens=100, damage=None):
        self.tokens, self.damage, self.calls = tokens, damage, []

    def attempt(self, experiment, task, arm):
        self.calls.append((copy.deepcopy(experiment), task['id'], arm))
        row = {'identity': expected_identity(experiment), 'optimization_enabled': arm == ARMS[1],
               'tokens': self.tokens, 'latency_ms': 10, 'success': True}
        if self.damage:
            self.damage(row)
        return row


def test_comparison_uses_both_builds_and_retains_distinct_identities():
    from agentops.benchmarks.comparison import run_comparison
    experiment = comparison()
    baseline, candidate = Adapter(), Adapter(70)
    result = run_comparison(experiment, baseline, candidate)
    assert result['decision']['status'] == 'pass'
    assert result['decision']['mean_savings'] == pytest.approx(.3)
    assert result['identity_verification']['status'] == 'pass'
    for arm, adapter in [('baseline', baseline), ('candidate', candidate)]:
        assert len(adapter.calls) == 5
        assert all(call[0] == experiment[arm] and call[2] == ARMS[1] for call in adapter.calls)
        rows = [row for row in result['attempts'] if row['arm'] == arm]
        assert all(row['identity'] == expected_identity(experiment[arm]) for row in rows)
    assert all(set(pair['arms']) == {'baseline', 'candidate'} for pair in result['schedule'])
    assert result['manifest_digest'] == digest(experiment)
    assert result['schedule'] == run_comparison(experiment, Adapter(), Adapter(70))['schedule']


@pytest.mark.parametrize('field', ['tasks', 'repeats', 'seed', 'model', 'policy', 'taskset_id', 'taskset_revision'])
def test_comparison_rejects_unpaired_experiment_inputs(field):
    from agentops.benchmarks.comparison import validate_comparison
    data = comparison()
    changes = {'tasks': [{**data['candidate']['tasks'][0], 'expected': 'other'}],
               'repeats': 6, 'seed': 13, 'model': 'other',
               'policy': {**data['candidate']['policy'], 'minimum_savings': .1},
               'taskset_id': 'other', 'taskset_revision': 'v2'}
    data['candidate'][field] = changes[field]
    with pytest.raises(ValueError):
        validate_comparison(data)


@pytest.mark.parametrize('damage', [lambda d: d.update(extra='secret'),
    lambda d: d.update(schema_version=True), lambda d: d.update(experiment_id=''),
    lambda d: d.update(baseline_optimization=1),
    lambda d: d['baseline'].update(extra='secret')])
def test_comparison_strict_manifest(damage):
    from agentops.benchmarks.comparison import validate_comparison
    data = comparison()
    damage(data)
    with pytest.raises(ValueError):
        validate_comparison(data)


@pytest.mark.parametrize('damage', [
    lambda r: r['identity'].update(model='secret-token'),
    lambda r: r['identity'].update(environment={'secret': 'secret-token'}),
    lambda r: r.update(identity=None),
    lambda r: r['identity'].update(model=float('nan')),
    lambda r: r.update(tokens=float('inf')),
    lambda r: r.update(latency_ms=-1),
    lambda r: r.update(success='secret-token'),
    lambda r: r.update(optimization_enabled=False),
])
def test_untrusted_observations_are_inconclusive_and_sanitized(damage):
    from agentops.benchmarks.comparison import run_comparison
    result = run_comparison(comparison(), Adapter(damage=damage), Adapter(70))
    assert result['decision']['status'] == 'inconclusive'
    assert 'secret-token' not in json.dumps(result, allow_nan=False)


def test_explicit_optimization_flags_control_actual_adapter_arms():
    from agentops.benchmarks.comparison import run_comparison
    data = comparison()
    data['baseline_optimization'] = False
    baseline = Adapter()
    result = run_comparison(data, baseline, Adapter(70))
    assert result['decision']['status'] == 'pass'
    assert all(call[2] == ARMS[0] for call in baseline.calls)
    assert all(row['optimization_enabled'] is False for row in result['attempts'] if row['arm'] == 'baseline')


@pytest.mark.parametrize('exception', [RuntimeError('secret-token'), KeyboardInterrupt()])
def test_errors_and_cancellation_preserve_actual_attempts_without_error_text(exception):
    from agentops.benchmarks.comparison import run_comparison
    def fail(row):
        raise exception
    result = run_comparison(comparison(), Adapter(damage=fail), Adapter(70))
    assert result['decision']['status'] == 'inconclusive'
    assert result['attempts']
    assert 'secret-token' not in json.dumps(result)
    assert result['cancelled'] is isinstance(exception, KeyboardInterrupt)


def test_either_simulator_adapter_prevents_live_qualification():
    from agentops.benchmarks.comparison import run_comparison
    baseline = Adapter()
    baseline.simulator = True
    result = run_comparison(comparison(), baseline, Adapter(70))
    assert result['decision']['status'] == 'inconclusive'
    assert result['simulator_arms'] == ['baseline']


def test_local_setup_failure_preserves_both_arm_evidence(tmp_path, monkeypatch):
    from agentops.benchmarks import comparison as module
    experiment = comparison()
    source = tmp_path / 'comparison.json'
    source.write_text(json.dumps(experiment), encoding='utf-8')
    constructed = []
    def install(experiment, artifact, config, workspace):
        constructed.append((artifact, str(workspace)))
        if artifact == 'bad.whl':
            raise RuntimeError('secret-token')
        return Adapter(70)
    monkeypatch.setattr(module, 'LocalAdapter', install)
    args = SimpleNamespace(manifest=str(source), artifact='good.whl', baseline_artifact='bad.whl',
                           config=None, baseline_config=None, output=str(tmp_path / 'output'), lane='live')
    assert module.run_local_comparison(args) == 2
    summary = json.loads((tmp_path / 'output/summary.json').read_text())
    result = json.loads((tmp_path / 'output' / summary['result_file']).read_text())
    assert result['setup_errors'] == {'baseline': 'RuntimeError'}
    assert len(result['attempts']) == 10
    assert len({workspace for artifact, workspace in constructed}) == 2
    assert summary['result_sha256'] == digest(result)
    assert 'secret-token' not in json.dumps(result)


@pytest.mark.parametrize('candidate_tokens,status,exit_status', [(70, 'pass', 0), (100, 'fail', 1)])
def test_local_wrapper_returns_measured_decision_status(tmp_path, monkeypatch, candidate_tokens, status, exit_status):
    from agentops.benchmarks import comparison as module
    experiment = comparison()
    source = tmp_path / 'comparison.json'
    source.write_text(json.dumps(experiment), encoding='utf-8')
    def install(experiment, artifact, config, workspace):
        return Adapter(100 if artifact == 'baseline.whl' else candidate_tokens)
    monkeypatch.setattr(module, 'LocalAdapter', install)
    args = SimpleNamespace(manifest=str(source), artifact='candidate.whl', baseline_artifact='baseline.whl',
                           config=None, output=str(tmp_path / 'output'), lane='live')
    assert module.run_local_comparison(args) == exit_status
    summary = json.loads((tmp_path / 'output/summary.json').read_text())
    assert summary['status'] == status
    assert summary['lane'] == 'live'


def test_real_two_wheel_execution_keeps_separate_provenance_and_simulator_boundary(tmp_path):
    import hashlib
    from zipfile import ZipFile
    from benchmark_fixture import build_fixture
    from agentops.benchmarks.comparison import run_comparison
    from agentops.benchmarks.local import LocalAdapter
    from agentops.benchmarks.workspace import Workspace

    # Both real proxy processes are controlled local fixtures. Neither calls a
    # provider. The baseline intentionally uses more tokens even when optimized.
    baseline_dir, candidate_dir = tmp_path / 'baseline', tmp_path / 'candidate'
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    baseline_artifact, _ = build_fixture(baseline_dir)
    candidate_artifact, candidate_digest = build_fixture(candidate_dir)
    with ZipFile(baseline_artifact) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    server = 'headroom/proxy/server.py'
    contents[server] = contents[server].replace(b'tokens = 70 if enabled else 100', b'tokens = 100')
    contents[server] = contents[server].replace(b"'d' * 64", b"'a' * 64")
    with ZipFile(baseline_artifact, 'w') as archive:
        for name, payload in contents.items():
            archive.writestr(name, payload)
    baseline_digest = 'sha256:' + hashlib.sha256(baseline_artifact.read_bytes()).hexdigest()
    experiment = comparison()
    experiment['baseline']['candidate']['artifact_sha256'] = baseline_digest
    experiment['candidate']['candidate']['artifact_sha256'] = candidate_digest
    for arm in ('baseline', 'candidate'):
        experiment[arm]['policy']['maximum_latency_ratio'] = 1000
    with Workspace(prefix='baseline-test-', dir=tmp_path) as baseline_workspace, \
         Workspace(prefix='candidate-test-', dir=tmp_path) as candidate_workspace:
        baseline = LocalAdapter(experiment['baseline'], baseline_artifact,
                                {'wheelhouse': str(baseline_dir), 'startup_timeout': 10}, baseline_workspace)
        candidate = LocalAdapter(experiment['candidate'], candidate_artifact,
                                 {'wheelhouse': str(candidate_dir), 'startup_timeout': 10}, candidate_workspace)
        assert baseline.python != candidate.python
        for arm, adapter in [('baseline', baseline), ('candidate', candidate)]:
            observed = adapter.probe()
            experiment[arm]['environment'] = observed['environment']
            experiment[arm]['agent'] = observed['agent']
        result = run_comparison(experiment, baseline, candidate)
    assert result['decision']['status'] == 'inconclusive'
    assert result['decision']['simulated_decision']['status'] == 'pass'
    assert result['identity_verification']['status'] == 'pass'
    assert result['simulator_arms'] == ['baseline', 'candidate']
    assert len(result['attempts']) == 10
    for row in result['attempts']:
        assert row['identity'] == expected_identity(experiment[row['arm']])
        assert row['tokens'] == (100 if row['arm'] == 'baseline' else 70)
        assert row['optimization_enabled'] is True
