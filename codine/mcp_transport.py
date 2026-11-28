"""Bounded, non-retrying MCP 2025-11-25 stdio JSON-RPC transport."""
import json
import os
import selectors
import signal
import subprocess
import time

from .terminal import safe_text
from .transcripts import scrub

VERSION='2025-11-25'
TIMEOUT=10.0
LIMIT=65536


class MCPError(ValueError): pass


class MCPConnection:
    def __init__(self,snapshot,credentials,redactor,cancel_event=None):
        self.process=None; self.closed=False; self.sequence=0
        self.redactor=redactor; self.cancel_event=cancel_event
        self.tools_changed=False; self.stderr=''; self.buffer=bytearray()
        self._cancel()
        config=snapshot['config']
        argv=[snapshot['launch'][0]['path'],*config['argv'][1:]]
        try:
            self.process=subprocess.Popen(argv,cwd=snapshot['cwd'],env={'PATH':os.defpath,'LANG':'C.UTF-8',**credentials},
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,bufsize=0)
            for stream in (self.process.stdin,self.process.stdout,self.process.stderr): os.set_blocking(stream.fileno(),False)
            result=self.request('initialize',{'protocolVersion':VERSION,'capabilities':{},'clientInfo':{'name':'codine','version':'0.2.0'}})
            if (not isinstance(result,dict) or result.get('protocolVersion')!=VERSION or
                    not isinstance(result.get('capabilities'),dict) or not isinstance(result['capabilities'].get('tools'),dict)):
                raise MCPError('Server does not support pinned MCP 2025-11-25 tools.')
            self.notify('notifications/initialized')
        except BaseException:
            self.close(); raise

    def _cancel(self):
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.close(); raise KeyboardInterrupt('MCP operation cancelled; not replayed.')

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

    def request(self,method,params):
        if self.closed: raise MCPError('MCP connection is closed; reconnect explicitly.')
        self.sequence+=1
        return self._exchange(self.encode({'jsonrpc':'2.0','id':self.sequence,'method':method,'params':params}),self.sequence)

    def _exchange(self,outgoing,expected):
        outgoing=bytearray(outgoing); errors=bytearray(); frames=0; received=0
        deadline=time.monotonic()+TIMEOUT
        try:
            with selectors.DefaultSelector() as select:
                select.register(self.process.stdin,selectors.EVENT_WRITE,'in')
                select.register(self.process.stdout,selectors.EVENT_READ,'out')
                select.register(self.process.stderr,selectors.EVENT_READ,'err')
                while True:
                    self._cancel()
                    if time.monotonic()>=deadline: raise MCPError('MCP timed out; server stopped, action not replayed.')
                    if expected is None and not outgoing: return None
                    for key,_ in select.select(.05):
                        if key.data=='in':
                            try: count=os.write(key.fd,outgoing[:4096])
                            except BlockingIOError: continue
                            del outgoing[:count]
                            if not outgoing: select.unregister(key.fileobj)
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
                        if type(message.get('id')) is not int or message['id']!=expected or ('result' in message)==('error' in message):
                            raise MCPError('Unexpected MCP response ID or fields.')
                        self.stderr=self.clean(errors.decode(errors='replace'))
                        if 'error' in message: raise MCPError('MCP server rejected request: '+self.clean(str(message['error']))[:2000])
                        return self.clean(message['result'])
        except BaseException:
            self.close(); raise

    def close(self):
        if self.closed: return
        self.closed=True
        if self.process is None: return
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
