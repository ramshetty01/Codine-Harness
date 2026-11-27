"""In-process bounded job supervision; never persist commands or credentials."""
from dataclasses import dataclass, field
import threading
import time
import uuid

from .command_output import OutputRedactor
from .config import Redactor
from .terminal import safe_text


@dataclass
class Job:
    id: str
    description: str
    status: str = 'queued'
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    exit_code: int | None = None
    logs: str = ''
    truncated: bool = False
    cleanup_uncertain: bool = False
    output_withheld: bool = False
    timeout_seconds: int = 300
    expired: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class JobSupervisor:
    def __init__(self, redactor=None, *, max_active=4, max_retained=16, log_limit=16000, default_lifetime=300, max_lifetime=3600):
        if not 1 <= max_active <= max_retained <= 64 or not 1024 <= log_limit <= 65536:
            raise ValueError('Invalid job count or log limits.')
        if not 1 <= default_lifetime <= max_lifetime <= 3600:
            raise ValueError('Job lifetimes must be bounded by 3600 seconds.')
        self.default_lifetime, self.max_lifetime = default_lifetime, max_lifetime
        self.redactor = redactor or Redactor()
        self.max_active, self.max_retained, self.log_limit = max_active, max_retained, log_limit
        self.jobs = {}
        self.lock = threading.RLock()
        self.closed = False

    def _clean(self, value):
        return safe_text(self.redactor.text(str(value)))

    def _append(self, job, text):
        with self.lock:
            remaining = self.log_limit - len(job.logs)
            job.logs += text[:remaining]
            if len(text) > remaining:
                job.truncated = True

    def start(self, description, run, *, timeout=None):
        """run(cancel_event, on_output) must already be approved and bounded."""
        timeout = self.default_lifetime if timeout is None else timeout
        if type(timeout) is not int or not 1 <= timeout <= self.max_lifetime:
            raise ValueError('Invalid bounded job lifetime.')
        with self.lock:
            if self.closed:
                raise ValueError('Job supervisor is shutting down.')
            active = sum(job.thread is not None and job.thread.is_alive() for job in self.jobs.values())
            if active >= self.max_active:
                raise ValueError(f'At most {self.max_active} background jobs can run at once.')
            while len(self.jobs) >= self.max_retained:
                old = next((key for key, job in self.jobs.items() if not job.thread.is_alive() and not job.cleanup_uncertain), None)
                if old is None:
                    raise ValueError('Job history is full.')
                del self.jobs[old]
            job = Job(uuid.uuid4().hex[:12], self._clean(description)[:1000], timeout_seconds=timeout)
            self.jobs[job.id] = job
            job.thread = threading.Thread(target=self._worker, args=(job, run), name='codine-job-'+job.id, daemon=True)
            job.thread.start()
            return self.status(job.id)

    def _worker(self, job, run):
        streaming = OutputRedactor()
        def output(event):
            event = streaming.feed(event, self.redactor)
            if event['text']:
                self._append(job, '['+event['stream']+'] '+self._clean(event['text']))
        def expire():
            with self.lock:
                job.expired = True
                job.cancel.set()
        timer = threading.Timer(job.timeout_seconds, expire)
        timer.daemon = True
        with self.lock:
            job.status = 'running'
        timer.start()
        try:
            result = run(job.cancel, output)
            with self.lock:
                # Never retain the result's output, arguments, environment or credentials.
                status = result.get('status', 'failed')
                if status == 'cancelled' and job.expired:
                    status = 'timeout'
                job.status = status if status in {'completed','timeout','output_limit','cancelled','declined','denied'} else 'failed'
                job.output_withheld = result.get('output_withheld') is True
                job.exit_code = result.get('exit_code') if type(result.get('exit_code')) is int else None
        except BaseException as error:
            # Exception text may contain credential-bearing runner output.
            with self.lock:
                job.status = 'failed'
                job.cleanup_uncertain = True
            self._append(job, '\nJob failed ('+type(error).__name__+'); inspect execution settings.\n')
        finally:
            timer.cancel()
            with self.lock:
                job.finished = time.monotonic()

    def status(self, ident=None):
        with self.lock:
            if ident is None:
                return [self._summary(job) for job in self.jobs.values()]
            if ident not in self.jobs:
                raise ValueError('Unknown or expired job ID; jobs exist only in this process.')
            job = self.jobs[ident]
            return {**self._summary(job), 'logs': job.logs, 'logs_truncated': job.truncated}

    def _summary(self, job):
        return {'id':job.id,'description':job.description,'status':job.status,'exit_code':job.exit_code,
                'seconds':round((job.finished or time.monotonic())-job.started,2),
                'cancel_requested':job.cancel.is_set(), 'cleanup_uncertain':job.cleanup_uncertain, 'output_withheld':job.output_withheld, 'timeout_seconds':job.timeout_seconds}

    def cancel_job(self, ident):
        with self.lock:
            if ident not in self.jobs:
                raise ValueError('Unknown or expired job ID; jobs exist only in this process.')
            job=self.jobs[ident]
            if job.thread.is_alive():
                job.cancel.set()
            return self._summary(job)

    def cancel_all(self, timeout=35):
        """Stop jobs before restart; retain the supervisor if exec later fails."""
        with self.lock:
            jobs=list(self.jobs.values())
            for job in jobs:
                if job.thread.is_alive():
                    job.cancel.set()
        deadline=time.monotonic()+timeout
        for job in jobs:
            job.thread.join(max(0,deadline-time.monotonic()))
        uncertain=[job.id for job in jobs if job.thread.is_alive() or job.cleanup_uncertain]
        if uncertain:
            raise RuntimeError('Could not confirm job cleanup: '+', '.join(uncertain))

    def close(self, timeout=35):
        """Cancel every job and wait for transport cleanup before exit."""
        with self.lock:
            self.closed=True
        self.cancel_all(timeout)
