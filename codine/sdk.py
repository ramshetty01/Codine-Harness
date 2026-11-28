"""Public synchronous Python client for Codine's persistent local RPC process.

Calls can be made concurrently for cancellation/status. Agent turns remain serial.
No provider SDK, keys or alternate accounting path lives in this client.
"""
import json
import os
from pathlib import Path
import queue
import selectors
import signal
import subprocess
import sys
import threading
import time
import uuid

API_VERSION = 1
FRAME_LIMIT = 65536


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError('Duplicate RPC response field.')
        result[key]=value
    return result


class CodineError(RuntimeError):
    pass


class Client:
    def __init__(self, project, *, provider=None, model=None, resume=None, executor=None,
                 plan=False, endpoint=None, allow_unmetered_custom=None,
                 on_event=None, approve=None, startup_timeout=20, command=None):
        self.on_event, self.approve = on_event, approve
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.callback_lock = threading.RLock()
        self.pending = {}
        self.sent_ids = set()
        self.response_ids = set()
        self.event_sequence = 0
        self.callback_state = threading.local()
        self.notifications = queue.Queue(maxsize=512)
        self.ready = None
        self.failure = None
        self.closed = False
        self.stderr = bytearray()
        if command is None:
            # Explicit trusted package path also supports a source checkout client
            # launched against another project directory, without project PYTHONPATH.
            source = 'import sys; sys.path.insert(0, '+repr(str(Path(__file__).resolve().parent.parent))+'); from codine.launcher import main; raise SystemExit(main())'
            command = [sys.executable, '-I', '-c', source]
        if not isinstance(command,(list,tuple)) or not command or any(not isinstance(v,str) for v in command):
            raise ValueError('command must be a trusted executable argument list.')
        argv=[*command,'--rpc','--project',str(Path(project).resolve())]
        for flag,value in (('--provider',provider),('--model',model),('--resume',resume),('--executor',executor),('--endpoint',endpoint)):
            if value is not None:argv.extend([flag,str(value)])
        if allow_unmetered_custom is not None:
            if type(allow_unmetered_custom) is not bool:
                raise ValueError('allow_unmetered_custom must be bool or None.')
            argv.append('--allow-unmetered-custom' if allow_unmetered_custom else '--no-allow-unmetered-custom')
        if plan:argv.append('--plan')
        self.process=subprocess.Popen(argv,cwd=project,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                      start_new_session=True,bufsize=0)
        os.set_blocking(self.process.stdin.fileno(),False)
        self.reader=threading.Thread(target=self._read,name='codine-sdk-reader',daemon=True)
        self.errors=threading.Thread(target=self._stderr,name='codine-sdk-stderr',daemon=True)
        self.reader.start();self.errors.start()
        deadline=time.monotonic()+startup_timeout
        try:
            while self.ready is None:
                self._drain()
                if self.failure:raise CodineError(self.failure)
                if time.monotonic()>=deadline:raise TimeoutError('Codine RPC startup timed out.')
                time.sleep(.01)
        except BaseException:
            self.close(timeout=1);raise

    def _fail(self, message):
        with self.lock:
            self.failure=self.failure or message
            # Publish failure to waiters only after EOF has requested shutdown.
            # Never leave a disconnected client driving an unowned turn.
            try:self.process.stdin.close()
            except OSError:pass
            for item in self.pending.values():item['event'].set()

    def _stderr(self):
        while True:
            data=self.process.stderr.read(4096)
            if not data:return
            # Drain continuously; retain no raw stderr in public errors (may contain secrets).
            with self.lock:self.stderr[:]=b'[stderr output omitted]' if data else b''

    def _read(self):
        try:
            while True:
                raw=self.process.stdout.readline(FRAME_LIMIT+1)
                if not raw:break
                if len(raw)>FRAME_LIMIT or not raw.endswith(b'\n'):raise ValueError('Invalid RPC response frame size.')
                value=json.loads(raw,object_pairs_hook=unique_object,
                    parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite RPC response')))
                if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=API_VERSION:
                    raise ValueError('Unsupported RPC response.')
                if 'event' in value:
                    if (set(value)!={'version','sequence','event','request_id','data'} or
                            type(value['sequence']) is not int or value['sequence']<=self.event_sequence or
                            not isinstance(value['event'],str) or not 1<=len(value['event'])<=80 or
                            (value['request_id'] is not None and not isinstance(value['request_id'],str))):
                        raise ValueError('Invalid RPC event envelope.')
                    self.event_sequence=value['sequence']
                    if value['event']=='ready':
                        if self.ready is not None or not isinstance(value['data'],dict) or type(value['data'].get('protocol')) is not int or value['data'].get('protocol')!=API_VERSION:
                            raise ValueError('Invalid or repeated RPC ready event.')
                        self.ready=value['data']
                    try:self.notifications.put_nowait(value)
                    except queue.Full:raise ValueError('Client event backlog exceeded 512 messages.') from None
                elif 'id' in value:
                    if (not isinstance(value['id'],str) or ('result' in value)==('error' in value) or
                            set(value)!={'version','id', 'error' if 'error' in value else 'result'} or
                            ('error' in value and (not isinstance(value['error'],dict) or
                              set(value['error'])!={'message'} or not isinstance(value['error']['message'],str)))):
                        raise ValueError('Invalid RPC response envelope.')
                    with self.lock:
                        if value['id'] not in self.sent_ids or value['id'] in self.response_ids:
                            raise ValueError('Unknown or duplicate RPC response ID.')
                        self.response_ids.add(value['id'])
                        item=self.pending.get(value['id'])
                        if item is not None:item['response']=value;item['event'].set()
                else:raise ValueError('Unrecognized RPC message.')
        except (OSError,ValueError,TypeError,RecursionError):
            self._fail('Codine RPC transport failed; inspect the saved session before retrying actions.')
        finally:
            self._fail('Codine process disconnected; action outcomes may require session recovery.')

    def _send(self, request, timeout=10):
        raw=(json.dumps(request,ensure_ascii=True,allow_nan=False)+'\n').encode()
        if len(raw)>FRAME_LIMIT:raise ValueError('RPC request exceeds 64 KiB.')
        deadline=time.monotonic()+timeout
        if not self.write_lock.acquire(timeout=timeout):raise TimeoutError('RPC writer is busy.')
        try:
            view=memoryview(raw)
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdin,selectors.EVENT_WRITE)
                while view:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError('RPC write timed out; delivery may be incomplete.')
                    if selector.select(min(.1,remaining)):
                        try:count=os.write(self.process.stdin.fileno(),view)
                        except BlockingIOError:continue
                        view=view[count:]
        except (OSError,ValueError,TimeoutError):
            self._fail('RPC write failed; delivery may be incomplete. Inspect session before retrying.')
            raise
        finally:self.write_lock.release()

    def _callback(self, callback, argument):
        previous=getattr(self.callback_state,'active',False)
        self.callback_state.active=True
        try:return callback(argument)
        finally:self.callback_state.active=previous

    def _drain(self):
        with self.callback_lock:
            while True:
                try:event=self.notifications.get_nowait()
                except queue.Empty:return
                if self.on_event is not None:
                    try:self._callback(self.on_event,event)
                    except Exception:
                        try:self.cancel()
                        except (OSError,RuntimeError,TimeoutError):pass
                        raise CodineError('Event callback failed; cancellation requested. Inspect session before retrying.') from None
                if event['event']=='approval':
                    # An absent or failing approval callback denies; it never grants implicitly.
                    allow=False
                    if self.approve is not None:
                        try:allow=self._callback(self.approve,event['data']['description']) is True
                        except Exception:allow=False
                    self.request('approval',{'token':event['data']['token'],'allow':allow},timeout=10,_events=False)

    def request(self, method, params=None, *, timeout=180, _events=True):
        if self.closed:raise CodineError('Client is closed.')
        if _events and getattr(self.callback_state,'active',False):
            raise CodineError('Callbacks may call status/cancel; schedule turns or commands after the callback returns.')
        ident=uuid.uuid4().hex
        item={'event':threading.Event(),'response':None}
        with self.lock:
            if self.failure:raise CodineError(self.failure)
            if len(self.pending)>=32:raise CodineError('Too many concurrent RPC requests.')
            if len(self.sent_ids)>=4096:raise CodineError('RPC request limit reached; reconnect with the saved session.')
            self.pending[ident]=item
            self.sent_ids.add(ident)
        try:
            self._send({'version':API_VERSION,'id':ident,'method':method,'params':{} if params is None else params})
            deadline=time.monotonic()+timeout
            while not item['event'].wait(.01):
                if _events:self._drain()
                if time.monotonic()>=deadline:
                    raise TimeoutError('RPC request timed out; it may still be running. Cancel or inspect status before retrying.')
            if _events:self._drain()
            response=item['response']
            if response is None:raise CodineError(self.failure or 'RPC disconnected.')
            if 'error' in response:raise CodineError(response['error']['message'])
            return response['result']
        finally:
            with self.lock:self.pending.pop(ident,None)

    def run(self, text, *, timeout=180):return self.request('run',{'text':text},timeout=timeout)
    def command(self, text, *, timeout=180):return self.request('command',{'text':text},timeout=timeout)
    def status(self):return self.request('status',timeout=10,_events=False)
    def cancel(self):return self.request('cancel',timeout=10,_events=False)

    def close(self, timeout=5):
        if self.closed:return
        try:
            if self.process.poll() is None and not self.failure:
                try:self.request('close',timeout=min(timeout,2),_events=False)
                except (OSError,RuntimeError,TimeoutError):pass
            try:self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:os.killpg(self.process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                self.process.wait(timeout=2)
        finally:
            self.closed=True
            for stream in (self.process.stdin,self.process.stdout,self.process.stderr):
                try:stream.close()
                except OSError:pass
            self.reader.join(timeout=1);self.errors.join(timeout=1)

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
