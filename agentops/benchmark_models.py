"""Explicit provider model discovery; credentials never leave request headers."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from urllib.request import Request,urlopen


def _fetch(url,headers):
    with urlopen(Request(url,headers=headers),timeout=15) as response:
        raw=response.read(4*1024*1024+1)
        if len(raw)>4*1024*1024:
            raise ValueError('Model response exceeds supported limits')
        return json.loads(raw)


class Models:
    def __init__(self,fetcher=_fetch,ttl=120):
        self.fetcher,self.ttl=fetcher,ttl
        self.cache={}
        self.lock=threading.Lock()

    def list(self,provider):
        if provider not in {'openai','anthropic','azure','bedrock'}:
            raise ValueError('Choose a supported provider')
        if provider=='azure':
            model=os.environ.get('AZURE_OPENAI_MODEL_VERSION','')
            deployment=os.environ.get('AZURE_OPENAI_DEPLOYMENT','')
            return {'status':'configured' if model and deployment else 'manual',
                    'models':[{'id':model,'request_model':deployment}] if model and deployment else [],
                    'message':'Azure uses a deployment name and its returned model version. Configure AZURE_OPENAI_DEPLOYMENT and AZURE_OPENAI_MODEL_VERSION, or enter both below.'}
        if provider=='bedrock':
            if not shutil.which('aws'):
                return {'status':'manual','models':[],'message':'Install/configure AWS CLI for model discovery, or enter a Bedrock model ID for the runner.'}
            try:
                command=['aws','bedrock','list-foundation-models','--output','json','--no-cli-pager']
                region=os.environ.get('AWS_REGION') or os.environ.get('AWS_DEFAULT_REGION')
                if region:
                    command+=['--region',region]
                response=subprocess.run(command,check=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                                        timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if len(response.stdout)>4*1024*1024:
                    raise ValueError('Model response exceeds limits')
                data=json.loads(response.stdout)
                models=[{'id':m['modelId'],'name':m.get('modelName',m['modelId'])}
                        for m in data['modelSummaries'] if 'TEXT' in m.get('inputModalities',[])]
                return {'status':'ready','models':models[:1000],'message':'Models listed by Bedrock in the configured AWS region; account access may vary.'}
            except (OSError,ValueError,KeyError,subprocess.SubprocessError):
                return {'status':'unavailable','models':[],'message':'Bedrock model discovery unavailable. Check AWS credentials and region, or enter the model ID.'}
        name='OPENAI_API_KEY' if provider=='openai' else 'ANTHROPIC_API_KEY'
        key=os.environ.get(name)
        if not key:
            return {'status':'missing_credentials','models':[],'message':'Configure '+name+' on the runner host, then load available models.'}
        # A credential rotation must not reuse another account's model catalogue.
        identity=(provider,hashlib.sha256(key.encode()).hexdigest())
        with self.lock:
            cached=self.cache.get(identity)
            if cached and time.monotonic()-cached[0]<self.ttl:
                return copy.deepcopy(cached[1])
        headers={'Authorization':'Bearer '+key} if provider=='openai' else {'x-api-key':key,'anthropic-version':'2023-06-01'}
        endpoint='https://api.openai.com/v1/models' if provider=='openai' else 'https://api.anthropic.com/v1/models?limit=100'
        try:
            data=self.fetcher(endpoint,headers)
            rows=[]
            for model in data['data'][:1000]:
                identifier=model.get('id')
                if isinstance(identifier,str) and 0<len(identifier)<=200 and all(ord(c)>=32 for c in identifier):
                    rows.append({'id':identifier,'name':str(model.get('display_name') or identifier)[:200]})
            result={'status':'ready','models':sorted(rows,key=lambda m:m['id']),
                    'message':'Available account models. Choose a model compatible with this scenario; pinned versions give reproducible identities.'}
            with self.lock:
                if len(self.cache)>=16:
                    self.cache.clear()
                self.cache[identity]=(time.monotonic(),copy.deepcopy(result))
            return result
        except (OSError,ValueError,KeyError,TypeError):
            return {'status':'unavailable','models':[],'message':'Provider model discovery unavailable. Check credentials and network access, or enter the model ID.'}
