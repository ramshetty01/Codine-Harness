"""Explicitly trusted, approved local MCP tools; no sampling/provider bridge."""
import copy
import hashlib
import json
import os
import re

from .config import Redactor
from .extension_api import parameters
from .mcp_settings import MCPSettings, WARNING
from .mcp_transport import MCPConnection, MCPError
from .registry import ToolSpec
from .terminal import safe_text
from .transcripts import scrub


def schema(value):
    value=copy.deepcopy(value)
    def normalize(node, depth=0):
        if depth>12 or not isinstance(node,dict): raise ValueError('Invalid MCP input schema.')
        dialect=node.pop('$schema',None)
        if dialect not in (None,'https://json-schema.org/draft/2020-12/schema','https://json-schema.org/draft/2020-12/schema#'):
            raise ValueError('Unsupported MCP schema dialect; use the supported JSON Schema 2020-12 subset.')
        if node.get('type')=='object':
            node.setdefault('additionalProperties',False)
            if not isinstance(node.get('properties',{}),dict): raise ValueError('Invalid MCP schema properties.')
            for child in node.get('properties',{}).values(): normalize(child,depth+1)
        elif node.get('type')=='array': normalize(node.get('items'),depth+1)
    normalize(value); parameters(value)
    if value['type']!='object': raise ValueError('MCP tools require object arguments.')
    return value


class MCPManager:
    def __init__(self,workspace,registry,redactor,settings=None,environment=None):
        self.workspace=workspace; self.registry=registry; self.redactor=redactor
        self.settings=settings or MCPSettings()
        self.workspace.protected_roots.add(self.settings.root.resolve())
        self.environment=dict(os.environ if environment is None else environment)
        self.loaded={}

    def allowed(self):
        if self.workspace.mode=='plan': raise MCPError('MCP host processes are disabled in plan mode.')

    def connect(self,name):
        self.allowed()
        if name in self.loaded: raise MCPError('MCP server is already connected; disconnect before reconnecting.')
        snapshot=self.settings.snapshot(name,self.workspace.root)
        if not self.settings.trusted(snapshot): raise MCPError('Review /mcp trust NAME before connecting.')
        credentials={}
        for key in snapshot['config']['credential_env']:
            value=self.environment.get(key)
            if not isinstance(value,str) or len(value)<8 or len(value)>8192:
                raise MCPError('Explicit MCP credential is missing or outside its 8–8192 character bound: '+key)
            credentials[key]=value
        redactor=Redactor((*self.redactor.secrets,*credentials.values()))
        if scrub(json.dumps(snapshot['config']),redactor)!=json.dumps(snapshot['config']):
            raise MCPError('Do not put secrets in MCP argv/config; use credential_env names.')
        if self.settings.snapshot(name,self.workspace.root)!=snapshot or not self.settings.trusted(snapshot):
            raise MCPError('MCP launch changed before startup; review again.')
        connection=MCPConnection(snapshot,credentials,redactor,getattr(self.workspace,'cancel_event',None))
        try:
            specs={}; cursor=None; seen_cursors=set(); toolnames=set()
            availability=lambda:self._available(name)
            for _ in range(4):
                result=connection.request('tools/list',{'cursor':cursor} if cursor else {})
                if not isinstance(result,dict) or not isinstance(result.get('tools'),list): raise MCPError('Invalid MCP tool discovery.')
                for tool in result['tools']:
                    if (not isinstance(tool,dict) or not isinstance(tool.get('name'),str) or
                            not re.fullmatch('[A-Za-z0-9_.:-]{1,128}',tool['name']) or tool['name'] in toolnames or
                            not isinstance(tool.get('description',''),str) or len(tool.get('description',''))>4000):
                        raise MCPError('Invalid or duplicate MCP tool metadata.')
                    toolnames.add(tool['name'])
                    if len(toolnames)>32: raise MCPError('At most 32 MCP tools per server are supported.')
                    input_schema=schema(tool.get('inputSchema'))
                    full='mcp_'+name[:20]+'_'+hashlib.sha256((name+':'+tool['name']).encode()).hexdigest()[:16]
                    if full in self.registry.tools or full in specs: raise MCPError('MCP tool namespace collision.')
                    specs[full]=ToolSpec(full,safe_text(tool.get('description') or 'MCP tool '+tool['name']),input_schema,
                                         self.handler(name,tool['name']),effect='execute',deferred=True,
                                         availability=availability)
                cursor=result.get('nextCursor')
                if cursor is None: break
                if not isinstance(cursor,str) or len(cursor)>1024 or cursor in seen_cursors: raise MCPError('Invalid MCP pagination cursor.')
                seen_cursors.add(cursor)
            else: raise MCPError('MCP tool discovery exceeds four pages.')
            if self.settings.snapshot(name,self.workspace.root)!=snapshot or not self.settings.trusted(snapshot):
                raise MCPError('MCP trust or launch changed during discovery.')
            connection.tools_changed=False
            self.registry.tools.update(specs)
            self.loaded[name]={'snapshot':snapshot,'connection':connection,'tools':list(specs)}
            return {'name':name,'status':'connected','protocol':connection.protocol,'tools':list(specs)}
        except BaseException:
            connection.close(); raise

    def _available(self,name):
        entry=self.loaded.get(name)
        return bool(entry and not entry['connection'].closed and not entry['connection'].tools_changed
                    and self.settings.trusted(entry['snapshot']))

    def handler(self,name,tool):
        def invoke(**arguments):
            self.allowed()
            entry=self.loaded.get(name)
            if entry is None: raise MCPError('MCP server is disconnected.')
            connection=entry['connection']
            def gate():
                if (connection.closed or connection.tools_changed or
                        self.settings.snapshot(name,self.workspace.root)!=entry['snapshot'] or
                        not self.settings.trusted(entry['snapshot'])):
                    connection.close(); raise MCPError('MCP trust, launch or tools changed; reconnect after review.')
            gate()
            prompt=('Call MCP '+name+':'+tool+'?\nServer tool annotations do not grant permissions.\nArguments: '+
                    safe_text(scrub(json.dumps(arguments),connection.redactor)))
            if not self.workspace.approve(prompt): return {'status':'declined'}
            gate()
            connection.cancel_event=getattr(self.workspace,'cancel_event',None)
            result=connection.request('tools/call',{'name':tool,'arguments':arguments})
            if (not isinstance(result,dict) or not isinstance(result.get('content'),list) or
                    ('isError' in result and type(result['isError']) is not bool)):
                connection.close(); raise MCPError('Invalid MCP tool result.')
            for item in result['content']:
                if not isinstance(item,dict) or item.get('type')!='text' or not isinstance(item.get('text'),str):
                    connection.close(); raise MCPError('Only MCP text content results are supported; no external resources are fetched.')
            return {'server':name,'tool':tool,'untrusted_server_result':result}
        return invoke

    def disconnect(self,name):
        if name not in self.loaded: raise MCPError('MCP server is not connected.')
        item=self.loaded.pop(name)
        try: item['connection'].close()
        finally:
            for tool in item['tools']: self.registry.tools.pop(tool,None)
        return {'name':name,'status':'disconnected'}

    def close(self):
        for name in list(self.loaded): self.disconnect(name)

    def list(self):
        return [{'name':name,'status':('disabled' if self.loaded[name]['connection'].closed else 'connected') if name in self.loaded else 'disconnected',
                 'protocol':self.loaded[name]['connection'].protocol if name in self.loaded else row.get('protocol','auto'),
                 'credential_env':row['credential_env']} for name,row in self.settings.definitions().items()]


def command(argument,agent,workspace,ui):
    if getattr(agent,'_in_turn',False): raise MCPError('MCP lifecycle commands require an idle prompt.')
    action,_,name=argument.strip().partition(' ')
    manager=agent.mcp_manager
    if action in ('','list') and not name: result=manager.list()
    elif action=='tools' and not name:
        result={key:value['tools'] for key,value in manager.loaded.items()}
    elif action in ('trust','revoke','connect','disconnect') and name and ' ' not in name:
        if action in ('trust','connect'): manager.allowed()
        if action=='trust':
            row=manager.settings.definitions().get(name,{})
            redactor=Redactor((*agent.redactor.secrets,*(manager.environment.get(key,'') for key in row.get('credential_env',[]))))
            result={'trusted':manager.settings.approve(name,workspace.root,ui,redactor=redactor)}
        elif action=='revoke':
            manager.settings.revoke(name,workspace.root)
            if name in manager.loaded: manager.disconnect(name)
            result={'name':name,'status':'revoked'}
        elif action=='connect':
            # A previous turn's cancelled inbox is not this explicit idle action.
            from .extension_commands import idle_execution
            with idle_execution(workspace,True):
                result=manager.connect(name)
        else: result=manager.disconnect(name)
    else: raise MCPError('Usage: /mcp list|tools|trust NAME|revoke NAME|connect NAME|disconnect NAME')
    ui.write(safe_text(scrub(json.dumps(result,indent=2),agent.redactor)))
    return result
