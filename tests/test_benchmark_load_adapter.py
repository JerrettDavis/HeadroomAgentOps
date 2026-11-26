import copy
import json
import pytest

from test_benchmark_core import manifest
from agentops.benchmarks.runner import expected_identity,run_experiment


def test_load_batches_feed_verified_pairs_without_replaying_requests_or_exporting_private_paths():
    from agentops.benchmarks.load_adapter import LoadAdapter
    class Native:
        simulator=True
        def __init__(self):self.calls=[]
        def load_batch(self,experiment,task,arm,*,concurrency,requests):
            self.calls.append((task['id'],arm,concurrency,requests))
            return {'tool':'aiperf','version':'0.14.0','concurrency':concurrency,'completed_requests':requests,
                'elapsed_seconds':1.,'requests_per_second':float(requests),'prompt_tokens':requests*100,
                'completion_tokens':requests,'measurement_basis':'gateway_first_dispatch_to_last_completion_nonstreaming',
                'native_logs':'private-secret-path','attempts':[{'tokens':100 if arm=='a1_passthrough' else 70,
                    'latency_ms':10,'success':True,'identity':copy.deepcopy(expected_identity(experiment)),
                    'optimization_enabled':arm=='b_headroom'} for _ in range(requests)]}
    native=Native();adapter=LoadAdapter(native,concurrency=2)
    result=run_experiment(manifest(),adapter)
    result['load_batches']=adapter.batches
    assert len(native.calls)==2
    assert len(result['attempts'])==10
    assert result['identity_verification']['status']=='pass'
    assert result['decision']['status']=='pass'
    assert len(adapter.batches)==2
    assert {b['requests_per_second'] for b in adapter.batches}=={5.}
    assert 'private-secret-path' not in json.dumps(result)


def test_failed_batch_is_not_automatically_replayed_for_each_pair():
    from agentops.benchmarks.load_adapter import LoadAdapter
    class Native:
        def __init__(self):self.calls=0
        def load_batch(self,*args,**kwargs):
            self.calls+=1;raise ValueError('private provider text')
    native=Native();adapter=LoadAdapter(native,concurrency=2)
    result=run_experiment(manifest(),adapter)
    assert native.calls==2
    assert result['decision']['status']=='inconclusive'
    assert 'private provider text' not in json.dumps(result)


def test_load_budget_is_rejected_before_any_upstream_call():
    from agentops.benchmarks.load_adapter import LoadAdapter
    class Forbidden:
        def load_batch(self,*args,**kwargs):raise AssertionError('Must not call upstream')
    data=manifest();data['repeats']=601
    with pytest.raises(ValueError,match='1200'):
        LoadAdapter(Forbidden(),concurrency=2).attempt(data,data['tasks'][0],'a1_passthrough')
