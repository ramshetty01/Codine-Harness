"""Provider transports: reconstruct complete calls before the engine can act."""
import json
import copy
import re
import hashlib
from dataclasses import dataclass, field
from openai import OpenAI, DefaultHttpxClient
import httpx2 as httpx
from .connection_retry import create_stream
from .config import BASE_URL


def collect_stream(stream, emit=None, show_reasoning=False):
    emit = emit or (lambda kind, text: None)
    content, reasoning, calls = [], [], {}
    finish, usage = None, None
    size = 0
    for chunk in stream:
        if getattr(chunk, 'usage', None):
            usage = chunk.usage
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        finish = choice.finish_reason or finish
        delta = choice.delta
        thinking = getattr(delta, 'reasoning_content', None)
        if thinking:
            reasoning.append(thinking)
            size += len(thinking)
            if show_reasoning:
                emit('reasoning', thinking)
        if delta.content:
            content.append(delta.content)
            size += len(delta.content)
            emit('text', delta.content)
        for fragment in delta.tool_calls or []:
            call = calls.setdefault(fragment.index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
            if fragment.id:
                call['id'] += fragment.id
            if fragment.function:
                name, args = fragment.function.name or '', fragment.function.arguments or ''
                call['function']['name'] += name
                call['function']['arguments'] += args
                size += len(name) + len(args)
        if size > 2_000_000 or len(calls) > 32:
            raise RuntimeError('Response exceeds the local output limit; no requested tools executed.')
    if finish is None:
        raise RuntimeError('Stream ended without a completion marker; no requested tools executed.')
    if calls and finish != 'tool_calls':
        raise RuntimeError('Incomplete or truncated tool request; no requested tools executed.')
    reply = {'role': 'assistant', 'content': ''.join(content) or None}
    if reasoning:
        reply['reasoning_content'] = ''.join(reasoning)
    if calls:
        ordered = [calls[index] for index in sorted(calls)]
        ids = set()
        for call in ordered:
            if not call['id'] or call['id'] in ids or not call['function']['name']:
                raise RuntimeError('Incomplete or duplicate tool call ID.')
            ids.add(call['id'])
            try:
                if not isinstance(json.loads(call['function']['arguments']), dict):
                    raise ValueError()
            except ValueError as error:
                raise RuntimeError('Invalid tool arguments; no requested tools executed.') from error
        reply['tool_calls'] = ordered
    elif finish == 'tool_calls':
        raise RuntimeError('Provider reported tool calls without any calls.')
    return reply, finish, usage


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _reasoning_detail(details, incoming):
    """Merge adjacent text deltas; opaque/encrypted blocks keep their exact sequence."""
    if not isinstance(incoming, dict) or not isinstance(incoming.get('type'), str):
        raise RuntimeError('Malformed streamed reasoning detail; no tools executed.')
    detail = copy.deepcopy(incoming)
    kind = detail['type']
    text_key = {'reasoning.text': 'text', 'reasoning.summary': 'summary'}.get(kind)
    if text_key and detail.get(text_key) is not None and not isinstance(detail[text_key], str):
        raise RuntimeError('Malformed reasoning text; no tools executed.')
    previous = details[-1] if details else None
    # Signed text blocks are complete. IDs/format/custom metadata may not be
    # overwritten or silently combined with a different block's metadata.
    merge = bool(text_key and previous and previous.get('type') == kind and not previous.get('signature'))
    if merge:
        for key, value in detail.items():
            if key not in (text_key, 'signature') and value is not None and previous.get(key) not in (None, value):
                merge = False
                break
    if merge:
        if text_key in detail:
            previous[text_key] = (previous.get(text_key) or '') + (detail.get(text_key) or '')
        for key, value in detail.items():
            if key != text_key and (key not in previous or previous[key] is None):
                previous[key] = value
    else:
        details.append(detail)


def collect_openrouter_stream(stream, emit=None, show_reasoning=False):
    """Validate one Chat Completions stream before exposing any executable calls."""
    emit = emit or (lambda *args: None)
    parts = {'content': [], 'reasoning': [], 'reasoning_content': [], 'refusal': []}
    details, calls = [], {}
    finish, usage, response_id = None, None, None
    size = 0
    for count, chunk in enumerate(stream, 1):
        if count > 100000 or _field(chunk, 'error') is not None:
            raise RuntimeError('OpenRouter stream failed or exceeded its frame limit; no tools executed.')
        ident = _field(chunk, 'id')
        if ident is not None:
            if not isinstance(ident, str) or (response_id is not None and ident != response_id):
                raise RuntimeError('OpenRouter stream changed response identity; no tools executed.')
            response_id = ident
        choices = _field(chunk, 'choices')
        if not isinstance(choices, list) or len(choices) > 1:
            raise RuntimeError('Malformed or multiple streamed choices; no tools executed.')
        current_usage = _field(chunk, 'usage')
        if not choices:
            if finish is not None and current_usage is not None:
                usage = current_usage
            continue
        choice = choices[0]
        if type(_field(choice, 'index')) is not int or _field(choice, 'index') != 0:
            raise RuntimeError('Unexpected streamed choice index; no tools executed.')
        delta = _field(choice, 'delta')
        if delta is None or _field(delta, 'role') not in (None, 'assistant'):
            raise RuntimeError('Malformed assistant delta; no tools executed.')
        current_finish = _field(choice, 'finish_reason')
        if current_finish not in (None, 'stop', 'length', 'tool_calls'):
            raise RuntimeError('OpenRouter stream ended with an unsupported/error reason; no tools executed.')
        if finish is not None and current_finish not in (None, finish):
            raise RuntimeError('Conflicting stream completion markers; no tools executed.')
        reasoning_details = _field(delta, 'reasoning_details')
        fragments = _field(delta, 'tool_calls')
        if reasoning_details is not None and not isinstance(reasoning_details, list):
            raise RuntimeError('Malformed reasoning details array; no tools executed.')
        if fragments is not None and not isinstance(fragments, list):
            raise RuntimeError('Malformed tool deltas; no tools executed.')
        values = {}
        for key in parts:
            value = _field(delta, key)
            if value is not None and not isinstance(value, str):
                raise RuntimeError('Malformed streamed text; no tools executed.')
            values[key] = value or ''
        if finish is not None and (any(values.values()) or reasoning_details or fragments):
            raise RuntimeError('Content arrived after stream completion; no tools executed.')
        for key, value in values.items():
            if value:
                parts[key].append(value)
                size += len(value.encode('utf-8'))
        for detail in reasoning_details or []:
            try:
                size += len(json.dumps(detail, ensure_ascii=False, allow_nan=False).encode())
            except (TypeError, ValueError):
                raise RuntimeError('Non-JSON reasoning detail; no tools executed.') from None
            _reasoning_detail(details, detail)
        for fragment in fragments or []:
            index = _field(fragment, 'index')
            if type(index) is not int or not 0 <= index < 32 or _field(fragment, 'type') not in (None, 'function'):
                raise RuntimeError('Invalid streamed tool index/type; no tools executed.')
            call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
            call_id = _field(fragment, 'id')
            if call_id is not None:
                if not isinstance(call_id, str) or not call_id or (call['id'] and call['id'] != call_id):
                    raise RuntimeError('Conflicting streamed tool identity; no tools executed.')
                call['id'] = call_id
                size += len(call_id.encode())
            function = _field(fragment, 'function')
            if function is not None:
                for key in ('name', 'arguments'):
                    value = _field(function, key)
                    if value is not None:
                        if not isinstance(value, str):
                            raise RuntimeError('Malformed tool function delta; no tools executed.')
                        call['function'][key] += value
                        size += len(value.encode())
        if size > 2_000_000 or len(details) > 4096:
            raise RuntimeError('Streamed output exceeds local limits; no tools executed.')
        # Emit only presentation text. Tool calls stay private until final validation.
        if values['content']:
            emit('text', values['content'])
        if values['refusal']:
            emit('text', values['refusal'])
        if show_reasoning:
            thinking = values['reasoning'] or values['reasoning_content']
            if thinking:
                emit('reasoning', thinking)
            elif reasoning_details:
                for detail in reasoning_details:
                    text_key = {'reasoning.text': 'text', 'reasoning.summary': 'summary'}.get(detail.get('type'))
                    text = detail.get(text_key) if text_key else None
                    if isinstance(text, str) and text:
                        emit('reasoning', text)
        if current_finish is not None:
            finish = current_finish
        if finish is not None and current_usage is not None:
            # Final accounting is a snapshot, not an increment. Never add chunks
            # or substitute preliminary usage when final usage/cost is absent.
            usage = current_usage
    if finish is None:
        raise RuntimeError('OpenRouter stream ended without a completion marker; no tools executed.')
    if calls and finish != 'tool_calls':
        raise RuntimeError('OpenRouter tool batch was truncated; no tools executed.')
    reply = {'role': 'assistant', 'content': ''.join(parts['content']) or None}
    for key in ('reasoning', 'reasoning_content', 'refusal'):
        if parts[key]:
            reply[key] = ''.join(parts[key])
    if details:
        reply['reasoning_details'] = details
    if calls:
        if sorted(calls) != list(range(len(calls))):
            raise RuntimeError('Streamed tool indices are incomplete; no tools executed.')
        ids = set()
        for call in calls.values():
            if not call['id'] or call['id'] in ids or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', call['function']['name']):
                raise RuntimeError('Missing/duplicate tool identity or invalid name; no tools executed.')
            ids.add(call['id'])
            try:
                def reject_constant(value):
                    raise ValueError('Nonfinite JSON constant')
                if not isinstance(json.loads(call['function']['arguments'], parse_constant=reject_constant), dict):
                    raise ValueError('Arguments must be an object')
            except (ValueError, RecursionError):
                raise RuntimeError('Invalid complete tool arguments; no tools executed.') from None
        reply['tool_calls'] = [calls[index] for index in sorted(calls)]
    elif finish == 'tool_calls':
        raise RuntimeError('OpenRouter finished with no tool calls; no tools executed.')
    return reply, finish, usage



def _request_identity(messages, schemas, model):
    encoded = json.dumps({'messages': messages, 'schemas': schemas, 'model': model},
                         sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class PreparedCompletion:
    """One immutable request snapshot; no sticky plan shared between calls."""
    identity: str
    request_json: str = field(repr=False)
    note: str
    send: object = field(repr=False, compare=False)

    def bind(self, messages, schemas, model):
        if _request_identity(messages, schemas, model) != self.identity:
            raise ValueError('Prepared request does not match its model, messages and tool schemas.')
        # Only serialized immutable content is captured. Later caller mutations
        # cannot replace the request after it has crossed the accounting boundary.
        request_json, send = self.request_json, self.send
        def invoke(emit):
            request = json.loads(request_json)
            return send(request['model'], request['messages'], request['kwargs'], emit)
        return invoke


def _prepare_completion(provider, messages, schemas, model):
    if not provider.config.api_key:
        raise ValueError('Configure the selected provider API key before a live request.')
    identity = _request_identity(messages, schemas, model)
    plan = provider.preflight(messages, schemas, model)
    # Defensive check: custom preflight adapters must not change request inputs.
    if _request_identity(messages, schemas, model) != identity:
        raise ValueError('Preflight changed request inputs; no request sent.')
    request_json = json.dumps({'model': model, 'messages': messages, 'kwargs': plan.kwargs},
                              allow_nan=False)
    return PreparedCompletion(identity, request_json, plan.note, provider._send_prepared)

class NvidiaProvider:
    def __init__(self, config, capability_declarations=None):
        self.capability_declarations = capability_declarations
        self.config = config
        self.client = None
        self._owned_client = None

    def close(self):
        if self.client:
            self.client.close()

    def preflight(self, messages, schemas, model):
        from .capabilities import declarations, plan_request
        records = (self.capability_declarations or declarations)(self.config.project)
        plan = plan_request(self.config, model, schemas, declared=records.get('nvidia', {}).get(model), provider='nvidia')
        self.last_capabilities = plan
        return plan

    def prepare_completion(self, messages, schemas, model):
        return _prepare_completion(self, messages, schemas, model)

    def complete(self, messages, schemas, model, emit):
        prepared = self.prepare_completion(messages, schemas, model)
        return prepared.bind(messages, schemas, model)(emit)

    def _send_prepared(self, model, messages, kwargs, emit):
        if self.client is None:
            self.client = OpenAI(api_key=self.config.api_key, base_url=BASE_URL, timeout=httpx.Timeout(120.0, connect=5.0, write=30.0, pool=5.0), max_retries=0,
                                 http_client=DefaultHttpxClient(follow_redirects=False))
            self._owned_client = self.client
        with create_stream(self.client, {'model': model, 'messages': messages, **kwargs}, emit,
                           owned=self._owned_client is self.client) as stream:
            return collect_stream(stream, emit, self.config.show_reasoning)


class OpenRouterProvider:
    """Stream presentation text while reconstructing validated assistant/tool messages."""
    def __init__(self, config, capability_record=None, capability_declarations=None):
        self.capability_record = capability_record
        self.capability_declarations = capability_declarations
        self.config = config
        self.client = None
        self._owned_client = None

    def close(self):
        if self.client:
            self.client.close()

    def preflight(self, messages, schemas, model):
        from .capabilities import cached_record, declarations, plan_request
        read_record = self.capability_record or cached_record
        read_declarations = self.capability_declarations or declarations
        records = read_declarations(self.config.project)
        plan = plan_request(self.config, model, schemas, record=read_record(model), declared=records.get('openrouter', {}).get(model))
        self.last_capabilities = plan
        return plan

    def prepare_completion(self, messages, schemas, model):
        return _prepare_completion(self, messages, schemas, model)

    def complete(self, messages, schemas, model, emit):
        prepared = self.prepare_completion(messages, schemas, model)
        return prepared.bind(messages, schemas, model)(emit)

    def _send_prepared(self, model, messages, kwargs, emit):
        if self.client is None:
            self.client = OpenAI(api_key=self.config.api_key, base_url='https://openrouter.ai/api/v1', timeout=httpx.Timeout(120.0, connect=5.0, write=30.0, pool=5.0), max_retries=0,
                                 http_client=DefaultHttpxClient(follow_redirects=False))
            self._owned_client = self.client
        with create_stream(self.client, {'model': model, 'messages': messages, **kwargs}, emit,
                           owned=self._owned_client is self.client) as stream:
            return collect_openrouter_stream(stream, emit, self.config.show_reasoning)
