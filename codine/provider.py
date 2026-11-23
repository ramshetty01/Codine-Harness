"""NVIDIA transport: reconstruct complete calls before the engine can act."""
import json
from openai import OpenAI
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


class NvidiaProvider:
    def __init__(self, config):
        self.config = config
        self.client = None

    def close(self):
        if self.client:
            self.client.close()

    def complete(self, messages, schemas, model, emit):
        if not self.config.api_key:
            raise ValueError('Set NVIDIA_API_KEY in .env. Offline commands work without a key.')
        if self.client is None:
            self.client = OpenAI(api_key=self.config.api_key, base_url=BASE_URL, timeout=120.0, max_retries=0)
        with self.client.chat.completions.create(
            model=model, messages=messages, tools=schemas,
            temperature=1, top_p=0.95, max_tokens=self.config.max_tokens,
            parallel_tool_calls=True, seed=42,
            extra_body={'chat_template_kwargs': {'enable_thinking': True}},
            stream=True, stream_options={'include_usage': True},
        ) as stream:
            return collect_stream(stream, emit, self.config.show_reasoning)


class OpenRouterProvider:
    """Complete messages preserve opaque reasoning details without fragment guessing."""
    def __init__(self, config):
        self.config = config
        self.client = None

    def close(self):
        if self.client:
            self.client.close()

    def complete(self, messages, schemas, model, emit):
        if not self.config.api_key:
            raise ValueError('Set OPENROUTER_API_KEY in .env before a live request.')
        if self.client is None:
            self.client = OpenAI(api_key=self.config.api_key, base_url='https://openrouter.ai/api/v1', timeout=120.0, max_retries=0)
        response = self.client.chat.completions.create(
            model=model, messages=messages, tools=schemas,
            max_tokens=self.config.max_tokens, stream=False,
            extra_body={'provider': {'require_parameters': True}},
        )
        if not response.choices:
            raise RuntimeError('OpenRouter returned no choices.')
        choice = response.choices[0]
        reply = choice.message.model_dump(exclude_none=True)
        calls = reply.get('tool_calls', [])
        if calls and choice.finish_reason != 'tool_calls':
            raise RuntimeError('OpenRouter tool batch was truncated; no tools executed.')
        if choice.finish_reason not in ('stop', 'length', 'tool_calls') or (choice.finish_reason == 'tool_calls' and not calls):
            raise RuntimeError('OpenRouter returned an incomplete/unsupported completion.')
        if self.config.show_reasoning and reply.get('reasoning'):
            emit('reasoning', reply['reasoning'])
        if reply.get('content'):
            emit('text', reply['content'])
        return reply, choice.finish_reason, response.usage
