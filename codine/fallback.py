"""Explicit reviewed model alternatives. Never dispatch or replay a request."""
import hashlib
import json
import os
from pathlib import Path
from .conversation_rewind import state_hash
from .session_forks import fork, require_idle, settled_shared_budget

USER_FALLBACKS=Path.home()/'.config/codine/fallbacks.json'


def model_id(value):
    if not isinstance(value,str) or not value or len(value)>200 or any(c.isspace() or not c.isprintable() for c in value):
        raise ValueError('Provide one exact model ID.')
    return value


def policy(project,source=None):
    source=Path(source or USER_FALLBACKS).absolute()
    if not os.path.lexists(source):return {}
    if source.resolve().is_relative_to(Path(project).resolve()):raise ValueError('Fallback policy must be outside the project.')
    from .execution_profiles import _load_private_source
    data=_load_private_source(source)
    if not isinstance(data,dict) or set(data)!={'version','providers'} or type(data['version']) is not int or data['version']!=1 or not isinstance(data['providers'],dict):raise ValueError('Invalid fallback policy version or structure.')
    for provider,models in data['providers'].items():
        if provider not in ('nvidia','openrouter','custom') or not isinstance(models,list) or len(models)>20:raise ValueError('Invalid same-provider alternatives.')
        for model in models:model_id(model)
        if len(set(models))!=len(models):raise ValueError('Duplicate model alternatives.')
    return data['providers']


def model_bound_history(session):
    return any(any(row.get(name) for name in ('reasoning','reasoning_content','reasoning_details'))
               for row in session.messages[session.context_start:])


def guard_switch(agent,workspace):
    require_idle(agent,workspace)
    with settled_shared_budget(agent):pass
    if model_bound_history(agent.session):raise ValueError('Active provider reasoning may be model-bound. Use /fallback review MODEL then /fallback confirm DIGEST for an explicitly fresh-context child; archive and spending remain.')


def command(argument,agent,workspace,ui):
    parts=argument.split()
    if not parts or parts==['list']:
        models=policy(workspace.root).get(agent.session.provider,[])
        ui.write('Manual fallback only; never retry or switch automatically. Same-provider suggestions: '+(', '.join(models) or '(none configured)')+'\nUse /fallback review EXACT_MODEL, then /fallback confirm DIGEST. Review accepts an explicitly chosen ID even if not suggested.')
        return None
    require_idle(agent,workspace)
    if len(parts)!=2 or parts[0] not in ('review','confirm'):raise ValueError('Usage: /fallback list|review MODEL|confirm DIGEST')
    with settled_shared_budget(agent):
        expected=state_hash(agent.session,agent.redactor)
    if parts[0]=='review':
        model=model_id(parts[1])
        if model==agent.session.model:raise ValueError('Alternative must differ from the current model.')
        # Capability planning only: no metadata fetch, reservation or inference.
        agent.provider.preflight([],agent.registry.schemas(),model)
        review={'state':expected,'model':model,'provider':agent.session.provider,'session':agent.session.id}
        digest=hashlib.sha256(json.dumps(review,sort_keys=True).encode()).hexdigest()
        agent._fallback_review=(digest,review)
        ui.write('Fresh-context fallback review: '+json.dumps({**review,'digest':digest})+'\nConfirmation creates a child with the full archive intact, clears active context/summary/task plan, preserves current spending and project memory, and sends no request. Files and external effects remain. Repeat your desired task explicitly after switching.')
        return None
    saved=getattr(agent,'_fallback_review',None)
    if not saved or saved[0]!=parts[1] or saved[1]['state']!=expected or saved[1]['provider']!=agent.session.provider or saved[1]['session']!=agent.session.id:raise ValueError('No matching unchanged fallback review; review again.')
    model=saved[1]['model']
    agent.provider.preflight([],agent.registry.schemas(),model)
    child=fork(agent,workspace,'Alternative: '+model[:100],expected_state=expected,fresh_model=model)
    agent._fallback_review=None
    return child
