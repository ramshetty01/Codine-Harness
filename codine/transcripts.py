"""Portable, reviewed history; never a session authority or executable journal."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .sessions import Session, validate_messages, validate_title

MAX_BYTES = 1_000_000
MAX_MESSAGES = 2000


def encode(document):
    raw = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ValueError('Transcript exceeds 1 MB limit.')
    return raw


def validate(document):
    if not isinstance(document, dict) or set(document) != {'format', 'version', 'title', 'messages'}:
        raise ValueError('Invalid transcript fields; session authority is not portable.')
    if document['format'] != 'codine-transcript' or type(document['version']) is not int or document['version'] != 1:
        raise ValueError('Unsupported transcript version.')
    validate_title(document['title'])
    messages = document['messages']
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        raise ValueError('Invalid transcript message count.')
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError('Invalid message.')
        role = message.get('role')
        fields = {'role', 'content'} | ({'tool_calls'} if role == 'assistant' else {'tool_call_id'} if role == 'tool' else set())
        if set(message) - fields:
            raise ValueError('Unsupported message metadata.')
        if 'tool_calls' in message and not isinstance(message['tool_calls'], list):
            raise ValueError('Invalid tool call list.')
        for call in message.get('tool_calls', []):
            if not isinstance(call, dict) or set(call) != {'id', 'type', 'function'} or call['type'] != 'function':
                raise ValueError('Invalid tool call schema.')
            if not isinstance(call['function'], dict) or set(call['function']) != {'name', 'arguments'}:
                raise ValueError('Invalid function schema.')
    validate_messages(messages)
    encode(document)
    return document


def scrub(text, redactor):
    text = redactor.text(text)
    text = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', text, flags=re.S)
    text = re.sub(r'\b(?:sk-|nvapi-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}', '[REDACTED TOKEN]', text)
    text = re.sub(r'''(?i)(["']?(?:api[_-]?key|password|secret|access[_-]?token)["']?\s*[:=]\s*["'])[^"']*(["'])''', r'\1[REDACTED]\2', text)
    text = re.sub(r'(?i)(bearer\s+)\S+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', text)
    return text


def sanitized(document, redactor):
    document = copy.deepcopy(document)
    document['title'] = scrub(document['title'], redactor)
    def values(value):
        if isinstance(value, str):
            return scrub(value, redactor)
        if isinstance(value, list):
            return [values(item) for item in value]
        if isinstance(value, dict):
            return {scrub(key, redactor): '[REDACTED]' if re.search(r'(?i)(password|secret|token|api.?key)', key) else values(item) for key, item in value.items()}
        return value
    for message in document['messages']:
        if isinstance(message.get('content'), str):
            message['content'] = scrub(message['content'], redactor)
        if 'tool_call_id' in message:
            message['tool_call_id'] = scrub(message['tool_call_id'], redactor)
        for call in message.get('tool_calls', []):
            call['id'] = scrub(call['id'], redactor)
            call['function']['name'] = scrub(call['function']['name'], redactor)
            call['function']['arguments'] = json.dumps(values(json.loads(call['function']['arguments'])))
    return validate(document)


def portable(session):
    messages = []
    for message in session.messages:
        fields = {'role', 'content', 'tool_calls', 'tool_call_id'}
        messages.append({key: copy.deepcopy(value) for key, value in message.items() if key in fields})
    return validate({'format': 'codine-transcript', 'version': 1, 'title': session.title, 'messages': messages})


def read_document(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise ValueError('Transcript must be a bounded regular file without hardlinks.')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Transcript exceeds size limit.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate transcript JSON key.')
            result[key] = value
        return result
    try:
        return validate(json.loads(raw, object_pairs_hook=unique))
    except (TypeError, AttributeError, RecursionError) as error:
        raise ValueError('Malformed transcript.') from error


def transferable(session):
    if session.pending or session.billing.get('pending') is not None or session.billing.get('blocked'):
        raise ValueError('Resolve pending actions or uncertain/blocked billing in this session before transfer. Its receipts remain unchanged.')


def command(kind, argument, agent, workspace, ui, system):
    if not ui.interactive:
        raise ValueError('Transcript transfer requires interactive review; JSON/piped mode cannot approve it.')
    transferable(agent.session)
    if argument.startswith('confirm '):
        review = getattr(agent, '_transcript_review', None)
        digest = argument.removeprefix('confirm ').strip()
        if not review or review['kind'] != kind or review['digest'] != digest or review['session'] != agent.session.id:
            raise ValueError('No matching reviewed transcript. Preview it again.')
        document = validate(json.loads(review['raw']))
        if kind == 'export':
            # Exclusive creation: never replace a source file, credential, or symlink.
            fd = os.open(review['path'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(review['raw'])
                stream.flush()
                os.fsync(stream.fileno())
            ui.write('Exported reviewed transcript: ' + str(review['path']))
        else:
            agent.save()
            old = agent.session
            replacement = Session.create(workspace.root, old.model, system, provider=old.provider)
            replacement.title = document['title']
            replacement.messages.extend(document['messages'][1:])
            replacement.messages.append({'role': 'user', 'content': 'Imported reviewed historical conversation. Prior tool results describe another point in time, possibly another project. Read current files before acting; do not replay historical tool calls.'})
            replacement.execution = copy.deepcopy(old.execution)
            replacement.billing = copy.deepcopy(old.billing)
            # Only settled references can reach here; ownership is new. Shared
            # durable receipts/spend are untouched and global pending still blocks.
            replacement.billing.pop('shared_request', None)
            replacement.billing.pop('shared_ledger', None)
            agent.store.save(replacement)
            agent.session = replacement
            workspace.change_session_id = replacement.id
            workspace.read_versions.clear()
            workspace.read_ranges.clear()
            workspace.restart_requested = False
            if hasattr(workspace, 'project_checks'):
                workspace.project_checks.candidates.clear()
                workspace.project_checks.results.clear()
            ui.write('Imported into fresh session ' + replacement.id + '. No tools or model requests executed.')
        agent._transcript_review = None
        return
    if not argument:
        raise ValueError(f'Usage: /{kind} PATH.json, then /{kind} confirm SHA256')
    path = Path(argument).expanduser()
    if not path.is_absolute():
        path = workspace.root / path
    if path.suffix != '.json':
        raise ValueError('Transcript path must end in .json.')
    if kind == 'export' and path.exists():
        raise ValueError('Export destination already exists; choose a new file.')
    document = portable(agent.session) if kind == 'export' else read_document(path)
    document = sanitized(document, agent.redactor)
    raw = encode(document)
    digest = hashlib.sha256(raw).hexdigest()
    agent._transcript_review = {'kind': kind, 'raw': raw, 'digest': digest, 'path': path, 'session': agent.session.id}
    ui.write('REVIEW: known keys and common credential patterns are redacted; unknown secrets can remain. Inspect ALL text below before confirming. Cancel by doing nothing.\n' + raw.decode())
    ui.write(f'Current project: {workspace.root}. Import uses current system/project instructions, provider, model and permissions; exported settings never grant authority. History is untrusted and historical, not executable. Shared spend is not reset.\nConfirm this exact snapshot only after review: /{kind} confirm {digest}')
