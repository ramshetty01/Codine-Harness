"""Versioned snapshots with a durable pending-action journal and file locks."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .config import Redactor


def validate_title(title):
    if not isinstance(title, str) or len(title) > 120 or any(not c.isprintable() for c in title):
        raise ValueError("Session title must be printable text of at most 120 characters.")


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_messages(messages):
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict) or messages[0].get("role") != "system":
        raise ValueError("Session must begin with system instructions.")
    pending = set()
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError("Invalid session message.")
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"} or (role == "system" and index != 0):
            raise ValueError("Invalid message role/order.")
        if pending and role != "tool":
            raise ValueError("Tool requests are missing matching results.")
        if role == "tool":
            ident = message.get("tool_call_id")
            if ident not in pending:
                raise ValueError("Orphan or duplicate tool result.")
            if not isinstance(message.get("content"), str):
                raise ValueError("Tool result must contain text.")
            pending.remove(ident)
        elif role in {"system", "user"} and not isinstance(message.get("content"), str):
            raise ValueError("Expected text message content.")
        elif role == "assistant":
            if message.get("content") is not None and not isinstance(message["content"], str):
                raise ValueError("Invalid assistant content.")
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list):
                raise ValueError("Invalid tool batch.")
            for call in calls:
                ident = call.get("id")
                if not isinstance(ident, str) or not ident or ident in pending:
                    raise ValueError("Invalid or duplicate tool call ID.")
                function = call.get("function", {})
                if not isinstance(function.get("name"), str) or not isinstance(function.get("arguments"), str):
                    raise ValueError("Invalid tool function.")
                if not isinstance(json.loads(function["arguments"]), dict):
                    raise ValueError("Tool arguments must be an object.")
                pending.add(ident)
    if pending:
        raise ValueError("Session ends with incomplete tool calls.")


@dataclass
class Session:
    id: str
    project: str
    model: str
    messages: list
    provider: str = "nvidia"
    billing: dict = field(default_factory=dict)
    execution: dict = field(default_factory=dict)
    plan: list = field(default_factory=list)
    plan_revision: int = 0
    title: str = ""
    queue_receipts: list = field(default_factory=list)
    last_run: dict = field(default_factory=dict)
    git_baseline: dict = field(default_factory=dict)
    migration: dict = field(default_factory=dict)
    version: int = 1
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    pending: dict | None = None
    context_start: int = 1
    context_summary: dict = field(default_factory=dict)
    summary_end: int = 1
    summary_user: int | None = None
    usage_details: dict = field(default_factory=dict)
    usage: dict = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0})

    @classmethod
    def create(cls, project, model, instructions, provider="nvidia"):
        ident = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        return cls(ident, str(Path(project).resolve()), model, [{"role": "system", "content": instructions}], provider=provider)

    def repair_pending(self):
        if not self.pending:
            return False
        if not isinstance(self.pending, dict):
            raise ValueError("Malformed pending journal.")
        reply = self.pending["reply"]
        results = self.pending.get("results", {})
        if not isinstance(reply, dict) or not reply.get("tool_calls") or not isinstance(results, dict):
            raise ValueError("Malformed pending journal.")
        ids = {call["id"] for call in reply["tool_calls"]}
        if set(results) - ids or any(not isinstance(value, str) for value in results.values()):
            raise ValueError("Malformed pending tool results.")
        recovered = [reply]
        for call in reply["tool_calls"]:
            recovered.append({"role": "tool", "tool_call_id": call["id"], "content": results.get(call["id"],
                '{"error":"Interrupted action: outcome unknown. Inspect project state before retrying; this action was NOT replayed."}')})
        validate_messages(self.messages + recovered)
        self.messages.extend(recovered)
        self.pending = None
        return True


class SessionStore:
    def __init__(self, project, redactor=None):
        self.project = Path(project).resolve()
        internal = self.project / ".codine"
        if internal.is_symlink():
            raise ValueError("Session directory cannot be a symbolic link.")
        self.directory = internal / "sessions"
        if self.directory.is_symlink():
            raise ValueError("Session directory cannot be a symbolic link.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(internal, 0o700)
        os.chmod(self.directory, 0o700)
        self.redactor = redactor or Redactor()
        self.lock = None
        self.active = None

    def path(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", ident):
            raise ValueError("Invalid session ID.")
        path = self.directory / f"{ident}.json"
        if path.is_symlink():
            raise ValueError("Session file cannot be a symbolic link.")
        return path

    def _open_lock(self, ident):
        self.path(ident)
        path = self.directory / f"{ident}.lock"
        if path.is_symlink():
            raise ValueError("Session lock cannot be a symbolic link.")
        handle = open(path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise ValueError("This session is already open in another Codine process.") from None
        return handle

    def acquire(self, ident):
        if self.active == ident:
            return
        handle = self._open_lock(ident)
        self.close()
        self.lock, self.active = handle, ident

    def close(self):
        if self.lock:
            self.lock.close()
        self.lock = None
        self.active = None

    def save(self, session):
        from .usage import validate as validate_usage
        validate_usage(session.usage_details)
        from .task_plan import validate_plan
        validate_plan(session.plan)
        validate_title(session.title)
        from .migration import validate_migration
        validate_migration(session.migration)
        from .inbox import validate_receipts
        validate_receipts(session.queue_receipts)
        from .run_state import validate_run
        validate_run(session.last_run)
        from .git_awareness import validate_baseline
        validate_baseline(session.git_baseline)
        if type(session.plan_revision) is not int or session.plan_revision < 0:
            raise ValueError("Invalid plan revision.")
        validate_messages(session.messages)
        if session.project != str(self.project):
            raise ValueError("Session workspace mismatch.")
        self.acquire(session.id)
        session.updated = now()
        payload = self.redactor.data(asdict(session))
        fd, temporary = tempfile.mkstemp(prefix=".save-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path(session.id))
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, ident):
        # Lock BEFORE reading, so a concurrent writer cannot make our snapshot stale.
        handle = self._open_lock(ident) if self.active != ident else None
        try:
            session = self._read_session(ident)
        except BaseException:
            if handle:
                handle.close()
            raise
        if handle:
            self.close()
            self.lock, self.active = handle, ident
        self.save(session)
        return session

    def _read_session(self, ident, *, expected_project=None, repair_pending=True, payload=None):
        path = self.path(ident)
        if payload is None and path.stat().st_size > 20_000_000:
            raise ValueError("Session is too large to load (20 MB limit).")
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if payload is None else payload
            session = Session(**data)
            if type(session.version) is not int or session.version != 1 or session.id != ident or session.project != (expected_project or str(self.project)):
                raise ValueError("Unsupported session version, ID, or workspace.")
            if session.provider not in ('nvidia', 'openrouter'):
                raise ValueError('Unknown saved provider.')
            if not isinstance(session.billing, dict):
                raise ValueError("Session billing must be an object.")
            if session.billing:
                from .budget import Budget
                Budget(session.billing)
            from .task_plan import validate_plan
            validate_plan(session.plan)
            validate_title(session.title)
            from .migration import validate_migration
            validate_migration(session.migration)
            from .inbox import validate_receipts
            validate_receipts(session.queue_receipts)
            from .run_state import validate_run
            validate_run(session.last_run)
            from .git_awareness import validate_baseline
            validate_baseline(session.git_baseline)
            if type(session.plan_revision) is not int or session.plan_revision < 0:
                raise ValueError("Invalid plan revision.")
            validate_messages(session.messages)
            if type(session.context_start) is not int or not 1 <= session.context_start <= len(session.messages):
                raise ValueError("Invalid context boundary.")
            if session.context_start < len(session.messages) and session.messages[session.context_start].get('role') != 'user':
                raise ValueError("Context must begin at a user-turn boundary.")
            if type(session.summary_end) is not int or not 1 <= session.summary_end <= len(session.messages):
                raise ValueError('Invalid summary boundary.')
            if not isinstance(session.context_summary, dict):
                raise ValueError('Invalid context summary.')
            if session.context_summary:
                from .context import complete_boundaries, parse_summary
                if session.summary_end not in complete_boundaries(session.messages, 1):
                    raise ValueError('Summary must end after a complete assistant/tool group.')
                if set(session.context_summary) != {'summary', 'evidence'} or not isinstance(session.context_summary['evidence'], list):
                    raise ValueError('Invalid summary metadata.')
                parse_summary({'content': json.dumps(session.context_summary['summary'])}, 'stop')
            elif session.summary_end != 1 or session.summary_user is not None:
                raise ValueError('Summary boundary has no summary.')
            if session.summary_user is not None and (type(session.summary_user) is not int or
                    not 1 <= session.summary_user < session.summary_end or
                    session.messages[session.summary_user]['role'] != 'user'):
                raise ValueError('Invalid summary user anchor.')
            if not isinstance(session.model, str) or not session.model:
                raise ValueError("Invalid saved model.")
            if not isinstance(session.usage, dict) or any(type(session.usage.get(key)) is not int or session.usage[key] < 0 for key in ('input', 'output', 'total')):
                raise ValueError("Invalid usage counters.")
            from .usage import validate as validate_usage
            validate_usage(session.usage_details)
            if repair_pending:
                session.repair_pending()
        except (TypeError, KeyError, AttributeError, json.JSONDecodeError) as error:
            raise ValueError("Malformed session; original file left unchanged.") from error
        return session

    def list(self):
        result = []
        for path in sorted(self.directory.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True):
            if path.is_symlink() or path.stat().st_size > 20_000_000:
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    result.append({'id': path.stem, 'title': data.get('title', '') if isinstance(data.get('title', ''), str) else '', 'updated': data.get('updated', ''), 'model': data.get('model', ''), 'messages': len(data.get('messages', []))})
            except (OSError, ValueError, TypeError):
                continue
        return result
