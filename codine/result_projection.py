"""Bounded typed projections of historical JSON tool results, never executable queries."""
import hashlib
import json
import math

MAX_SOURCE = 2_000_000
MAX_OUTPUT = 8192


def parse(raw):
    if not isinstance(raw,str) or len(raw.encode()) > MAX_SOURCE:
        raise ValueError('Projection source must be JSON text under 2 MB.')
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result: raise ValueError('Duplicate JSON keys are not supported.')
            result[key]=value
        return result
    def finite(value):
        result=float(value)
        if not math.isfinite(result): raise ValueError('Nonfinite JSON numbers are not supported.')
        return result
    def reject(_): raise ValueError('Nonfinite JSON constants are not supported.')
    try: value=json.loads(raw,object_pairs_hook=pairs,parse_float=finite,parse_constant=reject)
    except (RecursionError,UnicodeError,json.JSONDecodeError):
        raise ValueError('Projection source is not bounded valid JSON.') from None
    count=0
    def walk(item,depth):
        nonlocal count
        count+=1
        if depth>32 or count>20000: raise ValueError('Projection JSON exceeds depth 32 or 20,000 nodes.')
        if isinstance(item,dict):
            for child in item.values():walk(child,depth+1)
        elif isinstance(item,list):
            for child in item:walk(child,depth+1)
    walk(value,0)
    return value


def pointer(value,path):
    if not isinstance(path,str) or len(path)>512 or (path and not path.startswith('/')):
        raise ValueError('Use a JSON Pointer of at most 512 characters, or empty for the root.')
    if not path:return value
    for part in path[1:].split('/'):
        # Only RFC6901 ~0 and ~1 escapes. No wildcards, expressions or URI loads.
        if any(part[i]=='~' and (i+1==len(part) or part[i+1] not in '01') for i in range(len(part))):
            raise ValueError('Invalid JSON Pointer escape.')
        part=part.replace('~1','/').replace('~0','~')
        if isinstance(value,dict):
            if part not in value:raise ValueError('Projection path does not exist.')
            value=value[part]
        elif isinstance(value,list):
            if not part.isascii() or not part.isdecimal() or (len(part)>1 and part.startswith('0')):
                raise ValueError('Array pointers require canonical nonnegative indices.')
            index=int(part)
            if index>=len(value):raise ValueError('Projection array index is out of range.')
            value=value[index]
        else:raise ValueError('Projection path crosses a scalar.')
    return value


def source(agent,store,tool_call_id=None,message_index=None,artifact_id=None):
    if sum(v is not None for v in (tool_call_id,message_index,artifact_id)) != 1:
        raise ValueError('Choose exactly one tool_call_id, message_index or artifact_id.')
    if artifact_id is not None:
        if store is None:raise ValueError('Artifact storage is unavailable.')
        record=store.record(artifact_id)
        return record['content'],{'artifact_id':artifact_id,'source_tool':record['tool'],'source_complete':True}
    if agent is None:raise ValueError('Session history is unavailable.')
    messages=agent.session.messages
    candidates=[]
    if message_index is not None:
        if type(message_index) is not int or not 0<=message_index<len(messages) or messages[message_index].get('role')!='tool':
            raise ValueError('message_index must identify a saved tool-result message.')
        row=messages[message_index]
        candidates=[(message_index,row.get('tool_call_id'),row.get('content'))]
    else:
        if not isinstance(tool_call_id,str) or not 1<=len(tool_call_id)<=200:
            raise ValueError('Invalid tool call ID.')
        candidates=[(index,row.get('tool_call_id'),row.get('content')) for index,row in enumerate(messages)
                    if row.get('role')=='tool' and row.get('tool_call_id')==tool_call_id]
        pending=agent.session.pending
        if pending and tool_call_id in pending.get('results',{}):
            candidates.append((None,tool_call_id,pending['results'][tool_call_id]))
    if len(candidates)!=1:
        indices=[index for index,_,_ in candidates if index is not None][:16]
        raise ValueError('Tool call ID is missing or ambiguous; use a saved message_index. Candidate indices: '+str(indices))
    index,ident,content=candidates[0]
    source_tool=None
    replies=[agent.session.pending['reply']] if index is None else reversed(messages[:index])
    for message in replies:
        matching=[call for call in message.get('tool_calls',[]) if call.get('id')==ident]
        if matching:
            source_tool=matching[0]['function']['name'];break
    return content,{'message_index':index,'tool_call_id':ident,'source_tool':source_tool,'source_complete':True}


def project(agent,store,*,tool_call_id=None,message_index=None,artifact_id=None,pointer_path='',fields=None,offset=0,limit=20,decode_json=False):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=50 or type(decode_json) is not bool:
        raise ValueError('Use a nonnegative offset, 1–50 limit and boolean decode_json.')
    if fields is not None and (not isinstance(fields,list) or not 1<=len(fields)<=16 or any(not isinstance(p,str) for p in fields)):
        raise ValueError('Fields must be 1–16 JSON Pointers relative to each selected row/value.')
    raw,provenance=source(agent,store,tool_call_id,message_index,artifact_id)
    parsed=parse(raw)
    provenance['sha256']=hashlib.sha256(raw.encode()).hexdigest()
    provenance['upstream_completeness']='unknown'
    # Offloaded historical messages contain excerpts, not the full tool output.
    if isinstance(parsed,dict) and parsed.get('truncated') is True:
        provenance['source_complete']=False
        if isinstance(parsed.get('artifact_id'),str):provenance['full_source_artifact_id']=parsed['artifact_id']
    selected=pointer(parsed,pointer_path)
    if decode_json:
        selected=parse(selected)
        provenance['decoded_json_text']=True
    is_array=isinstance(selected,list)
    if not is_array and offset:raise ValueError('Offset applies only to arrays.')
    if is_array and offset>len(selected):raise ValueError('Offset exceeds array length.')
    total=len(selected) if is_array else None
    result=selected[offset:offset+limit] if is_array else selected
    def select(item):return {field:pointer(item,field) for field in fields}
    if fields is not None:result=[select(item) for item in result] if is_array else select(result)
    end=min(total,offset+limit) if is_array else None
    response={'source':provenance,'pointer':pointer_path,'value':result,'total_items':total,
              'offset':offset,'next_offset':end if is_array and end<total else None,
              'truncated':bool(is_array and (offset or end<total)),
              'notice':'Historical data projection only. No source code executed, URLs fetched, calls repeated or file edit permissions refreshed.'}
    if agent is not None:response=agent.redactor.data(response)
    if len(json.dumps(response,ensure_ascii=False,allow_nan=False).encode())>MAX_OUTPUT:
        raise ValueError('Projection exceeds 8 KiB; choose fewer fields or a smaller row limit. No source was discarded.')
    return response


def register_projection_tool(registry,store,agent=None):
    from .registry import ToolSpec
    string={'type':'string'}
    properties={'tool_call_id':string,'message_index':{'type':'integer','minimum':0},'artifact_id':string,
                'pointer_path':string,'fields':{'type':'array','items':string},
                'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':50},
                'decode_json':{'type':'boolean'}}
    registry.register(ToolSpec('project_tool_result','Select bounded typed JSON fields/array rows from exactly one historical tool_call_id, message_index or artifact_id. Duplicate IDs require an index. JSON Pointers only; optional decode_json parses selected JSON text once. Does not repeat tools or grant file-read/edit authority.',
        {'type':'object','properties':properties,'required':[],'additionalProperties':False},
        lambda **args:project(agent,store,**args),effect='read'))
