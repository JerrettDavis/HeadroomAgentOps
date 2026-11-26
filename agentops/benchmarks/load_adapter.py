"""Feed bounded per-arm load batches into the native paired evidence pipeline."""
from __future__ import annotations

import copy

from .core import finite_number
from .runner import public_tool_metrics


class LoadAdapter:
    def __init__(self,adapter,*,concurrency):
        if type(concurrency) is not int or not 1<=concurrency<=64:
            raise ValueError('Load concurrency must be between 1 and 64')
        self.adapter=adapter
        self.concurrency=concurrency
        self.simulator=bool(getattr(adapter,'simulator',False))
        self.batches=[]
        self._samples={}
        self._indices={}

    def attempt(self,experiment,task,arm):
        if len(experiment['tasks'])*experiment['repeats']*2>1200:
            raise ValueError('Load experiment exceeds the 1200-request budget')
        if self.concurrency>experiment['repeats']:
            raise ValueError('Load concurrency exceeds the planned requests per arm')
        key=(task['id'],arm)
        if key not in self._samples:
            # Reserve this batch before invoking the harness: failed or partial
            # batches must never be replayed by later schedule observations.
            self._samples[key]=None
            batch=self.adapter.load_batch(experiment,task,arm,concurrency=self.concurrency,
                requests=experiment['repeats'])
            telemetry=public_tool_metrics({'tool':batch['tool'],'version':batch['version'],
                'requests':1,'latency_basis':'proxy_roundtrip_nonstreaming'})
            if (batch.get('concurrency')!=self.concurrency
                or batch.get('completed_requests')!=experiment['repeats']
                or len(batch.get('attempts',[]))!=experiment['repeats']
                or batch.get('measurement_basis')!='gateway_first_dispatch_to_last_completion_nonstreaming'):
                raise ValueError('Load batch differs from the bounded request plan')
            numeric=('elapsed_seconds','requests_per_second','prompt_tokens','completion_tokens')
            if any(not finite_number(batch.get(k)) or batch[k]<0 for k in numeric):
                raise ValueError('Load batch has unavailable measured telemetry')
            self.batches.append({'task_id':task['id'],'arm':arm,'tool':telemetry['tool'],
                'version':telemetry['version'],'concurrency':self.concurrency,
                'completed_requests':batch['completed_requests'],
                **{k:batch[k] for k in numeric},'measurement_basis':batch['measurement_basis']})
            self._samples[key]=[{**copy.deepcopy(row),'tool_metrics':telemetry} for row in batch['attempts']]
            self._indices[key]=0
        samples=self._samples[key]
        if samples is None:
            raise ValueError('Previous load batch failed; no requests replayed')
        index=self._indices[key]
        if index>=len(samples):
            raise ValueError('Load schedule exceeds reserved observations')
        self._indices[key]=index+1
        return copy.deepcopy(samples[index])
