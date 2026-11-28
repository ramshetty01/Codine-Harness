"""Explicit unmetered OpenAI-compatible transport with immutable routing snapshots."""
import copy
import json
from typing import Protocol

import httpx2 as httpx
from openai import OpenAI, DefaultHttpxClient

from .capabilities import declarations, plan_request
from .connection_retry import create_stream
from .custom_endpoint import canonical_endpoint, validate_key
from .provider import PreparedCompletion, _request_identity, collect_openrouter_stream


class ProviderAdapter(Protocol):
    def prepare_completion(self, messages, schemas, model) -> PreparedCompletion: ...
    def complete(self, messages, schemas, model, emit): ...
    def close(self): ...


class CustomProvider:
    """No pricing claims, provider fallback, redirects, proxy env, or credential reuse."""
    def __init__(self, config, capability_declarations=None):
        self.config = copy.deepcopy(config)
        self.endpoint = canonical_endpoint(config.custom_endpoint)
        if config.custom_unmetered is not True:
            raise ValueError('Custom transport requires explicit unmetered acknowledgement; cost is unknown.')
        self._key = validate_key(config.api_key, config._custom_forbidden_keys)
        if not self._key:raise ValueError('Custom transport requires an explicit credential or loopback placeholder.')
        self.capability_declarations = capability_declarations
        self._clients = set()

    def close(self):
        for client in list(self._clients):client.close()

    def preflight(self, messages, schemas, model):
        if self.config.custom_endpoint != self.endpoint or not self.config.custom_unmetered:
            raise ValueError('Custom routing/acknowledgement changed; construct a new adapter explicitly.')
        records=(self.capability_declarations or declarations)(self.config.project)
        plan=plan_request(self.config,model,schemas,declared=records.get('custom',{}).get(model),provider='custom')
        self.last_capabilities=plan
        return plan

    def prepare_completion(self,messages,schemas,model):
        identity=_request_identity(messages,schemas,model)
        plan=self.preflight(messages,schemas,model)
        if _request_identity(messages,schemas,model)!=identity:
            raise ValueError('Custom preflight changed request inputs; no request sent.')
        payload=json.dumps({'model':model,'messages':messages,'kwargs':plan.kwargs},allow_nan=False)
        # These values are captured before bind/accounting. Config or adapter
        # attribute changes cannot redirect an already prepared request.
        endpoint,key,reasoning=self.endpoint,self._key,self.config.show_reasoning
        def send(model,messages,kwargs,emit):
            client=OpenAI(api_key=key,base_url=endpoint,max_retries=0,
                timeout=httpx.Timeout(120.0,connect=5.0,write=30.0,pool=5.0),
                http_client=DefaultHttpxClient(follow_redirects=False,trust_env=False))
            self._clients.add(client)
            try:
                with create_stream(client,{'model':model,'messages':messages,**kwargs},emit,owned=True) as stream:
                    reply,finish,usage=collect_openrouter_stream(stream,emit,reasoning)
                if usage is not None:
                    usage=usage.model_dump() if hasattr(usage,'model_dump') else dict(usage)
                    usage.pop('cost',None)  # Arbitrary compatible responses are not metered billing evidence.
                return reply,finish,usage
            finally:
                client.close();self._clients.discard(client)
        return PreparedCompletion(identity,payload,plan.note+' Custom USD cost unknown; no dollar spending cap enforced.',send)

    def complete(self,messages,schemas,model,emit):
        return self.prepare_completion(messages,schemas,model).bind(messages,schemas,model)(emit)
