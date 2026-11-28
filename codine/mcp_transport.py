"""Bounded dual-era MCP stdio; only the initial discovery probe may fall back."""
import json
import os
import selectors
import signal
import subprocess
import time
import tempfile
from pathlib import Path

from .mcp_settings import fingerprint

from .terminal import safe_text
from .transcripts import scrub

VERSION='2026-07-28'
LEGACY_VERSION='2025-11-25'
MODERN_ERRORS={-32020,-32021,-32022}
TIMEOUT=10.0
LIMIT=65536
STAGE_LIMIT=64*1024*1024


class MCPError(ValueError): pass


class MCPRemoteError(MCPError):
    def __init__(self, code, message):
        self.code=code
        super().__init__('MCP server rejected request: '+message[:2000])


class MCPConnection:
    def __init__(self,snapshot,credentials,redactor,cancel_event=None):
        self.process=None; self.closed=False; self.sequence=0; self.staged=None
        self.redactor=redactor; self.cancel_event=cancel_event
        self.tools_changed=False; self.stderr=''; self.buffer=bytearray()
        self.protocol=VERSION
        self._cancel()
        config=snapshot['config']
        try:
            argv=self._stage_launch(snapshot)
            self.process=subprocess.Popen(argv,cwd=snapshot['cwd'],env={'PATH':os.defpath,'LANG':'C.UTF-8',**credentials},
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,bufsize=0)
            for stream in (self.process.stdin,self.process.stdout,self.process.stderr): os.set_blocking(stream.fileno(),False)
            preference=config.get('protocol','auto')
            if preference!=LEGACY_VERSION:
                try:
                    result=self.request('server/discover',{},probe=True)
                except MCPRemoteError as error:
                    if preference!='auto' or error.code in MODERN_ERRORS:
                        raise
                    self.protocol=LEGACY_VERSION
                else:
                    versions=result.get('supportedVersions') if isinstance(result,dict) else None
                    if (not isinstance(versions,list) or not 1<=len(versions)<=16 or
                            any(not isinstance(version,str) or len(version)>32 for version in versions) or VERSION not in versions or
                            not isinstance(result.get('capabilities'),dict) or not isinstance(result['capabilities'].get('tools'),dict)):
                        raise MCPError('MCP discovery did not advertise supported 2026-07-28 tools; no downgrade or retry.')
            else:
                self.protocol=LEGACY_VERSION
            if self.protocol==LEGACY_VERSION:
                result=self.request('initialize',{'protocolVersion':LEGACY_VERSION,'capabilities':{},'clientInfo':{'name':'codine','version':'0.2.0'}})
                if (not isinstance(result,dict) or result.get('protocolVersion')!=LEGACY_VERSION or
                        not isinstance(result.get('capabilities'),dict) or not isinstance(result['capabilities'].get('tools'),dict)):
                    raise MCPError('Server does not support pinned MCP 2025-11-25 tools.')
                self.notify('notifications/initialized')
        except BaseException:
            self.close(); raise

    def _stage_launch(self, snapshot):
        """Freeze declared script arguments; cwd and host runtime remain explicit."""
        executable=snapshot['launch'][0]
        if fingerprint(executable['path']) != executable:
            raise MCPError('MCP executable changed before startup.')
        self.staged=tempfile.TemporaryDirectory(prefix='codine-mcp-')
        root=Path(self.staged.name)
        mapping={}; total=0
        for original,reviewed in zip(snapshot['config']['files'],snapshot['launch'][1:]):
            self._cancel()
            actual, data=fingerprint(reviewed['path'],capture=True)
            total += len(data)
            if actual != reviewed:
                raise MCPError('MCP declared file changed before staging.')
            if total > STAGE_LIMIT:
                raise MCPError('MCP declared snapshot exceeds 64 MiB aggregate.')
            # Retain the original directory layout, so declared sibling modules
            # and resources addressed relative to __file__ remain colocated.
            target=root.joinpath(*Path(reviewed['path']).parts[1:])
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with target.open('xb') as stream: stream.write(data)
            target.chmod(0o400)
            mapping[reviewed['path']]=str(target)
            mapping[os.path.normpath(original)]=str(target)
        argv=[executable['path']]
        for argument in snapshot['config']['argv'][1:]:
            # Only entire path tokens are rewritten: never evaluate shell/code
            # strings or guess the semantics of --option=path arguments.
            candidate=Path(argument)
            if not candidate.is_absolute(): candidate=Path(snapshot['cwd'])/candidate
            argv.append(mapping.get(os.path.normpath(str(candidate)),argument))
        if fingerprint(executable['path']) != executable:
            raise MCPError('MCP executable changed during staging.')
        return argv

    def _cancel(self, request_id=None):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self._cancel_request(request_id)
            self.close(); raise KeyboardInterrupt('MCP operation cancelled; not replayed.')

    def _cancel_request(self, request_id):
        if request_id is None or self.protocol!=VERSION or self.process is None or self.closed:return
        raw=self.encode({'jsonrpc':'2.0','method':'notifications/cancelled',
                         'params':{'requestId':request_id,'reason':'Client stopped waiting; no automatic retry'}})
        # Best effort, strictly bounded. Process-tree shutdown remains the final
        # cleanup boundary when a server ignores cancellation or stops reading.
        deadline=time.monotonic()+.1
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin,selectors.EVENT_WRITE)
                while raw and time.monotonic()<deadline:
                    if selector.select(.01):
                        try:count=os.write(self.process.stdin.fileno(),raw)
                        except BlockingIOError:continue
                        raw=raw[count:]
        except (OSError,ValueError):pass

    def clean(self,value):
        if isinstance(value,str): return safe_text(scrub(value,self.redactor))
        if isinstance(value,list): return [self.clean(v) for v in value]
        if isinstance(value,dict): return {self.clean(k):self.clean(v) for k,v in value.items()}
        return value

    @staticmethod
    def encode(value):
        data=json.dumps(value,allow_nan=False).encode()+b'\n'
        if len(data)>LIMIT: raise MCPError('MCP request exceeds 64 KiB.')
        return data

    def notify(self,method):
        self._exchange(self.encode({'jsonrpc':'2.0','method':method}),None)

    def request(self,method,params,*,probe=False):
        if self.closed: raise MCPError('MCP connection is closed; reconnect explicitly.')
        self.sequence+=1
        params=dict(params)
        if self.protocol==VERSION:
            params['_meta']={'io.modelcontextprotocol/protocolVersion':VERSION,
                'io.modelcontextprotocol/clientCapabilities':{},
                'io.modelcontextprotocol/clientInfo':{'name':'codine','version':'0.2.0'}}
        return self._exchange(self.encode({'jsonrpc':'2.0','id':self.sequence,'method':method,'params':params}),self.sequence,probe=probe)

    def _exchange(self,outgoing,expected,*,probe=False):
        outgoing=bytearray(outgoing); errors=bytearray(); frames=0; received=0
        completed=False; result_value=None; sent=False
        deadline=time.monotonic()+TIMEOUT
        try:
            with selectors.DefaultSelector() as select:
                select.register(self.process.stdin,selectors.EVENT_WRITE,'in')
                select.register(self.process.stdout,selectors.EVENT_READ,'out')
                select.register(self.process.stderr,selectors.EVENT_READ,'err')
                while True:
                    self._cancel(expected if sent and not completed else None)
                    if time.monotonic()>=deadline:
                        self._cancel_request(expected if sent and not completed else None)
                        suffix=' Select protocol=2025-11-25 explicitly for a silent legacy server and review trust again.' if probe else ''
                        raise MCPError('MCP timed out; server stopped, action not replayed.'+suffix)
                    if expected is None and not outgoing: return None
                    if completed and not outgoing: return result_value
                    for key,_ in select.select(.05):
                        if key.data=='in':
                            try: count=os.write(key.fd,outgoing[:4096])
                            except BlockingIOError: continue
                            del outgoing[:count]
                            if not outgoing:
                                sent=True; select.unregister(key.fileobj)
                            continue
                        try: chunk=os.read(key.fd,4096)
                        except BlockingIOError: continue
                        if not chunk:
                            select.unregister(key.fileobj)
                            if key.data=='out': raise MCPError('MCP disconnected; action not replayed.')
                            continue
                        if key.data=='err':
                            errors.extend(chunk)
                            if len(errors)>16384: raise MCPError('MCP stderr limit exceeded.')
                        else:
                            received+=len(chunk); self.buffer.extend(chunk)
                            if received>512*1024 or len(self.buffer)>LIMIT: raise MCPError('MCP stdout/frame limit exceeded.')
                    while b'\n' in self.buffer:
                        line,rest=self.buffer.split(b'\n',1); self.buffer=bytearray(rest); frames+=1
                        if frames>128: raise MCPError('MCP message limit exceeded.')
                        try:
                            message=json.loads(line,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
                        except (ValueError,RecursionError): raise MCPError('Invalid MCP JSON.') from None
                        if not isinstance(message,dict) or message.get('jsonrpc')!='2.0': raise MCPError('Invalid MCP JSON-RPC envelope.')
                        if 'method' in message:
                            if not isinstance(message['method'],str): raise MCPError('Invalid MCP method.')
                            if 'id' in message:
                                if self.protocol==VERSION:raise MCPError('Modern MCP forbids server-to-client requests; no client actions executed.')
                                if type(message['id']) not in (str,int): raise MCPError('Invalid MCP server request ID.')
                                # No sampling, roots, credentials, elicitation or other
                                # server-driven actions. Never dispatch to a provider.
                                response=self.encode({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32601,'message':'Client capability unsupported'}})
                                outgoing.extend(response)
                                if len(outgoing)>LIMIT: raise MCPError('MCP pending reply limit exceeded.')
                                try: select.get_key(self.process.stdin)
                                except KeyError: select.register(self.process.stdin,selectors.EVENT_WRITE,'in')
                            elif message['method']=='notifications/tools/list_changed': self.tools_changed=True
                            continue
                        if completed or type(message.get('id')) is not int or message['id']!=expected or ('result' in message)==('error' in message):
                            raise MCPError('Unexpected MCP response ID or fields.')
                        self.stderr=self.clean(errors.decode(errors='replace'))
                        if 'error' in message:
                            error=message['error']
                            if not isinstance(error,dict) or type(error.get('code')) is not int or not isinstance(error.get('message'),str):
                                raise MCPError('Invalid MCP error response.')
                            raise MCPRemoteError(error['code'],self.clean(error['message']))
                        if self.protocol==VERSION and (not isinstance(message['result'],dict) or message['result'].get('resultType','complete')!='complete'):
                            raise MCPError('Unsupported MCP result type; no input, sampling, or automatic retry performed.')
                        # A server request may precede this result in the same
                        # read. Flush its unsupported-capability response before
                        # returning, rather than silently dropping that reply.
                        result_value=self.clean(message['result']); completed=True
        except MCPRemoteError:
            if not probe:self.close()
            raise
        except BaseException:
            self.close(); raise

    def close(self):
        if self.closed: return
        self.closed=True
        if self.process is None:
            if self.staged is not None: self.staged.cleanup()
            return
        # MCP stdio shutdown starts with stdin EOF, then bounded termination.
        self.process.stdin.close()
        try: self.process.wait(timeout=.1)
        except subprocess.TimeoutExpired:
            try: os.killpg(self.process.pid,signal.SIGTERM)
            except (ProcessLookupError,PermissionError): pass
            try: self.process.wait(timeout=.1)
            except subprocess.TimeoutExpired: pass
        # Also retire descendants left behind after the direct child exited.
        try: os.killpg(self.process.pid,signal.SIGKILL)
        except (ProcessLookupError,PermissionError):
            if self.process.poll() is None:
                try: self.process.kill()
                except ProcessLookupError: pass
        try: self.process.wait(timeout=2)
        except subprocess.TimeoutExpired: pass
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr): stream.close()
        if self.staged is not None: self.staged.cleanup()
