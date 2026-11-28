"""Version-one local JSONL RPC transport for the existing CLI/agent owner thread.

The reader accepts bounded requests; only the CLI thread executes user commands.
This is a trusted local IPC interface, not an authenticated network service.
"""
import json
import queue
import sys
import threading
import time
import uuid

from .events import JsonTerminal
from .command_output import OutputRedactor
from .terminal import safe_text

FRAME_LIMIT = 65536
APPROVAL_TIMEOUT = 60.0
COMMANDS = frozenset('/help /status /settings /usage /budget /policy /sandbox /plan /instructions /jobs /cancel /tasks /context /compact /model /thinking /permissions /save /resume /new /clear /artifact /changes /changeset /diff /rewind /undo /git /memory /skills /skill /trust /extensions /ext'.split())


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ValueError('Duplicate JSON field.')
        result[key] = value
    return result


class RpcTerminal(JsonTerminal):
    interactive = True
    rpc_transport = True

    def __init__(self, redactor, input_stream=None, output_stream=None):
        super().__init__(redactor)
        self.input = input_stream or sys.stdin.buffer
        self.output = output_stream or sys.stdout
        self.requests = queue.Queue(maxsize=16)
        self.outgoing = queue.Queue(maxsize=256)
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.inbox = None
        self.active = None
        self.active_error = None
        self.active_outcome = None
        self.cancelled = False
        self.approval = None
        self.seen = set()
        self.started = False
        self._approval_pending = False
        self.writer = threading.Thread(target=self._write, name='codine-rpc-output', daemon=True)
        self.reader = threading.Thread(target=self._read, name='codine-rpc-input', daemon=True)
        self.writer.start()

    def start(self):
        if not self.started:
            self.started = True
            self.reader.start()

    def _send(self, value):
        value = self.redactor.data(value)
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False)
        if len(raw.encode()) > FRAME_LIMIT:
            if 'event' not in value: raise ValueError('RPC response exceeds frame limit.')
            value['data'] = {'truncated':True, 'original_bytes':len(raw.encode()),
                             'preview':str(value.get('data'))[:8000]}
            raw = json.dumps(value, ensure_ascii=True)
        try:self.outgoing.put_nowait(raw+'\n')
        except queue.Full:
            self._stop()
            raise RuntimeError('RPC client is not consuming output; stopping without replay.') from None

    def _write(self):
        while True:
            raw=self.outgoing.get()
            if raw is None:return
            try:
                self.output.write(raw); self.output.flush()
            except (OSError, ValueError):
                self._stop(); return

    def _stop(self):
        self.stopped.set()
        with self.lock:
            if self.inbox is not None:self.inbox.cancel.set()
            if self.approval is not None:self.approval['ready'].set()

    def _reply(self, ident, result=None, error=None):
        self._send({'version':1,'id':ident, **({'error':{'message':safe_text(error)}} if error else {'result':result})})

    def _read(self):
        try:
            while not self.stopped.is_set():
                raw=self.input.readline(FRAME_LIMIT+1)
                if not raw:break
                if len(raw)>FRAME_LIMIT or not raw.endswith(b'\n'):
                    self._reply(None,error='RPC frame exceeds 64 KiB or is incomplete.');break
                ident=None
                try:
                    request=json.loads(raw, object_pairs_hook=unique_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                    if not isinstance(request,dict) or set(request)!={'version','id','method','params'} or type(request['version']) is not int or request['version']!=1:
                        raise ValueError('Expected version-one RPC request with id, method and params.')
                    ident=request['id']
                    if not isinstance(ident,str) or not 1<=len(ident)<=64 or not ident.isascii() or not ident.isprintable():
                        ident=None;raise ValueError('Request id must be 1–64 printable ASCII characters.')
                    with self.lock:
                        if ident in self.seen:raise ValueError('Duplicate request id; no action replayed.')
                        if len(self.seen)>=4096:raise ValueError('RPC request limit reached; reconnect with the saved session.')
                        self.seen.add(ident)
                    self._accept(ident,request['method'],request['params'])
                except (ValueError, TypeError, KeyError, RecursionError) as error:
                    self._reply(ident,error=str(error))
        except (OSError, RuntimeError):
            pass
        finally:self._stop()

    def _accept(self, ident, method, params):
        if not isinstance(params,dict) or not isinstance(method,str):raise ValueError('Invalid method or params.')
        if method in ('run','command'):
            if set(params)!={'text'} or not isinstance(params['text'],str) or not params['text'].strip() or len(params['text'])>16000:
                raise ValueError('Provide 1–16000 characters of text.')
            text=params['text'].strip()
            if method=='run' and (text.startswith('/') or text.lower()=='exit'):
                raise ValueError('Use command for slash commands; run accepts tasks only.')
            if method=='command':
                name,_,arg=text.partition(' ')
                if name not in COMMANDS:raise ValueError('Command is not available over RPC.')
                if name=='/resume' and not arg.strip():raise ValueError('/resume requires a session ID over RPC.')
                if name=='/model' and arg.strip() in ('','free','cached','cached free','refresh','refresh free'):
                    raise ValueError('/model requires an explicit model ID over RPC.')
            try:self.requests.put_nowait((ident,text))
            except queue.Full:raise ValueError('RPC command queue is full.') from None
            return
        if method=='approval':
            if set(params)!={'token','allow'} or not isinstance(params['token'],str) or type(params['allow']) is not bool:
                raise ValueError('Approval requires token and boolean allow.')
            with self.lock:
                item=self.approval
                if item is None or item['token']!=params['token'] or item['ready'].is_set():raise ValueError('Approval expired or does not match the active request.')
                item['allow']=params['allow'];item['ready'].set()
            self._reply(ident,{'accepted':True});return
        if params:raise ValueError('This method takes no parameters.')
        if method=='cancel':
            with self.lock:
                active=self.active
                if active is not None:
                    self.cancelled=True
                    if self.inbox is not None:self.inbox.cancel.set()
                    if self.approval is not None:self.approval['ready'].set()
            self._reply(ident,{'request_id':active,'cancel_requested':active is not None})
        elif method=='status':self._reply(ident,self.state())
        elif method=='close':
            self._reply(ident,{'closing':True});self._stop()
        else:raise ValueError('Unknown RPC method.')

    def bind_inbox(self, inbox):
        with self.lock:self.inbox=inbox

    def begin_turn(self, inbox):
        with self.lock:
            self.inbox=inbox
            inbox.cancel.clear()
            if self.cancelled or self.stopped.is_set():
                inbox.cancel.set();raise KeyboardInterrupt

    def emit(self, kind, data):
        with self.lock:
            if kind=='command_output':
                data=self.command_redactor.feed(data,self.redactor)
                data['text']=safe_text(data['text'])
                if not data['text'] and not data.get('final'):return
            if kind=='result':self.last_result=data
            if kind=='error':self.active_error=str(data)
            if kind=='run_end':self.active_outcome=data
            self.sequence+=1
            self._send({'version':1,'sequence':self.sequence,'event':kind,'request_id':self.active,'data':data})

    def welcome(self, project, session, model):
        self.emit('ready',{'protocol':1,'project':str(project),'session':session,'model':model,
                           'methods':['run','command','cancel','approval','status','close']})

    def _finish(self):
        with self.lock:
            if self.active is not None:
                self._reply(self.active, {'outcome':self.active_outcome, 'cancel_requested':self.cancelled},self.active_error)
                self.active=None;self.active_error=None;self.active_outcome=None

    def prompt(self):
        self._finish();self.start()
        while not self.stopped.is_set():
            try:ident,text=self.requests.get(timeout=.05)
            except queue.Empty:continue
            with self.lock:
                self.active=ident;self.cancelled=False
            return text
        raise EOFError

    def approve(self, description):
        with self.lock:
            if self.stopped.is_set() or self.cancelled:return False
            item={'token':uuid.uuid4().hex,'allow':False,'ready':threading.Event()}
            self.approval=item;self._approval_pending=True
            self.emit('approval',{'token':item['token'],'description':safe_text(description),'timeout_seconds':APPROVAL_TIMEOUT})
        item['ready'].wait(APPROVAL_TIMEOUT)
        with self.lock:
            allowed=item['allow'] and not self.cancelled and not self.stopped.is_set()
            self.approval=None;self._approval_pending=False
            return allowed

    def has_submitted_commands(self):return not self.requests.empty()

    def close(self):
        self._stop();self._finish()
        while True:
            try:ident,_=self.requests.get_nowait()
            except queue.Empty:break
            self._reply(ident,error='RPC closed before request execution; no action started.')
        try:self.outgoing.put(None,timeout=.2)
        except queue.Full:return
        self.writer.join(timeout=1)
