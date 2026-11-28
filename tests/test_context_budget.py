from contextlib import nullcontext
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from codine.agent import Agent
from codine.config import Config, Redactor
from codine.context import ContextLimitError, estimate_tokens, project_context
from codine.provider import OpenRouterProvider
from codine.sessions import Session, validate_messages


class ContextBudgetTests(unittest.TestCase):
    def session(self):
        return Session.create(Path.cwd(), 'small', 'system')

    def test_utf8_schemas_guidance_and_reasoning_count(self):
        session = self.session()
        session.messages += [{'role': 'user', 'content': '你好'},
                             {'role': 'assistant', 'content': 'ok', 'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque'}]}]
        schemas = [{'description': 'tool schema'}]
        messages, _ = project_context(session, schemas, 10000, 'instructions')
        estimate = estimate_tokens(messages, schemas)
        self.assertGreater(estimate, estimate_tokens(session.messages, []))
        self.assertGreater(estimate_tokens([{'content': '你好'}], []), estimate_tokens([{'content': 'ab'}], []))
        project_context(session, schemas, 10000, 'instructions', token_budget=estimate + 100, output_tokens=100)
        with self.assertRaises(ContextLimitError):
            project_context(session, schemas, 10000, 'instructions', token_budget=estimate + 99, output_tokens=100)

    def test_trim_whole_tool_turn_without_changing_archive(self):
        session = self.session()
        session.messages += [
            {'role': 'user', 'content': 'old' * 1000},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'a', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
            {'role': 'tool', 'tool_call_id': 'a', 'content': 'result'},
            {'role': 'user', 'content': 'new'},
            {'role': 'assistant', 'content': 'answer', 'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'opaque'}]},
        ]
        before = copy.deepcopy(session.messages)
        messages, start = project_context(session, [], 10000, token_budget=1000, output_tokens=100)
        self.assertEqual(start, 4)
        validate_messages(messages)
        self.assertEqual(messages[-1]['reasoning_details'], before[-1]['reasoning_details'])
        self.assertEqual(session.messages, before)
        with self.assertRaises(ContextLimitError):
            project_context(session, [], 10, token_budget=100000, output_tokens=100)

    def test_model_limits_follow_active_model_and_cap_output(self):
        config = Config(Path.cwd(), max_tokens=500, context_tokens=2000,
                        model_limits={'small': {'context_tokens': 1000, 'output_tokens': 200}})
        self.assertEqual(config.request_limits('small'), (1000, 200))
        self.assertEqual(config.request_limits('unknown'), (2000, 500))
        config.model_limits['bad'] = {'context_tokens': 500}
        with self.assertRaises(ValueError):
            config.request_limits('bad')
        provider = OpenRouterProvider(config)
        config.api_key = 'offline'
        provider.client = Mock()
        provider.client.chat.completions.create.return_value = nullcontext([{'choices':[{'index':0,'delta':{'content':'ok'},'finish_reason':'stop'}]}])
        provider.complete([], [], 'small', lambda *args: None)
        self.assertEqual(provider.client.chat.completions.create.call_args.kwargs['max_tokens'], 200)

    def test_agent_rejects_before_provider_or_billing_reservation(self):
        config = Config(Path.cwd(), provider='openrouter', api_key='offline', context_tokens=500, max_tokens=100)
        session = self.session()
        provider, registry, store = Mock(), Mock(), Mock()
        registry.schemas.return_value = []
        agent = Agent(config, provider, registry, store, session, lambda *args: None, Redactor())
        with patch('codine.agent.load_instructions', return_value=('', [])):
            with self.assertRaises(ContextLimitError):
                agent.run_turn('x' * 1000)
        provider.complete.assert_not_called()
        self.assertFalse(session.billing.get('pending'))

    def test_env_limits_reject_invalid_shapes(self):
        for limits in ('[]', '{"x": {"context_tokens": true}}', '{"x": {"typo": 100}}'):
            with patch.dict(os.environ, {'CODINE_MODEL_LIMITS': limits}), patch('codine.settings.read_env', return_value={}):
                with self.assertRaises(ValueError):
                    Config.load(Path.cwd(), provider='nvidia')
        with patch.dict(os.environ, {'CODINE_MODEL_LIMITS': json.dumps({'small': {'context_tokens': 8192, 'output_tokens': 512}})}), patch('codine.settings.read_env', return_value={}):
            self.assertEqual(Config.load(Path.cwd(), provider='nvidia').request_limits('small'), (8192, 512))
