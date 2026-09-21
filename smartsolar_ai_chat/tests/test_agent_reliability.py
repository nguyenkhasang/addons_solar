# -*- coding: utf-8 -*-
"""Regression tests cho độ ổn định của model local và provider payload."""
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.smartsolar_ai_chat.providers.base import (
    ChatRequest, ChatResponse, ToolCall, _coerce_scalar,
)
from odoo.addons.smartsolar_ai_chat.providers.ollama import OllamaProvider


@tagged('post_install', '-at_install', 'smartsolar_ai_chat')
class TestAgentReliability(TransactionCase):

    def test_xml_fallback_parses_array_arguments(self):
        self.assertEqual(_coerce_scalar('["output_power", "pv_input"]'),
                         ['output_power', 'pv_input'])
        self.assertEqual(_coerce_scalar('{"system_id": 1}'), {'system_id': 1})

    def test_ollama_receives_context_and_deterministic_options(self):
        class FakeResponse:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {'message': {'content': 'ok'}, 'done_reason': 'stop'}

        provider = OllamaProvider(
            base_url='http://localhost:11434', model='local-tool-model')
        with patch('requests.post', return_value=FakeResponse()) as post:
            provider.chat(ChatRequest(
                messages=[{'role': 'user', 'content': 'xin chào'}],
                temperature=0.1, max_tokens=1000, context_window=32768))
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['options']['temperature'], 0.1)
        self.assertEqual(payload['options']['num_predict'], 1000)
        self.assertEqual(payload['options']['num_ctx'], 32768)

    def test_custom_prompt_cannot_replace_safety_prompt(self):
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('smartsolar_ai.system_prompt', 'Trả lời cực ngắn.')
        cfg = self.env['smartsolar.ai.agent']._get_config()
        prompt = cfg['system_prompt']
        self.assertIn('Không tự đặt số', prompt)
        self.assertIn('Trả lời cực ngắn.', prompt)
        self.assertLess(prompt.index('Trả lời cực ngắn.'),
                        prompt.index('QUY TẮC BẮT BUỘC'))

    def test_system_prompt_keeps_energy_counter_directions(self):
        cfg = self.env['smartsolar.ai.agent']._get_config()
        prompt = cfg['system_prompt']
        self.assertIn('grid_import_energy_total (kWh), nguồn DB energy_total', prompt)
        self.assertIn('energy_exported_total (kWh), nguồn DB limiter_total', prompt)
        self.assertIn('output_power là điện pin/inverter cấp tải', prompt)
        self.assertIn('không được gọi là PV thu cùng thời điểm', prompt)

    def test_system_prompt_routes_current_and_overview_queries(self):
        prompt = self.env['smartsolar.ai.agent']._get_config()['system_prompt']
        self.assertIn("'hiện tại/bây giờ': get_aggregate từ now-10m đến now", prompt)
        self.assertIn('field last, không dùng avg làm giá trị hiện tại', prompt)
        self.assertIn("'grid_import_power'", prompt)
        self.assertIn("'grid_import_energy_total'", prompt)

    def test_unavailable_detector_handles_nested_metric_results(self):
        Agent = self.env['smartsolar.ai.agent']
        self.assertTrue(Agent._contains_unavailable({
            'metrics': {'output_power': {'available': False, 'last': None}},
        }))
        self.assertFalse(Agent._contains_unavailable({
            'metrics': {'output_power': {'available': True, 'last': 125.0}},
        }))

    def test_data_question_fails_closed_when_model_never_calls_tool(self):
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('smartsolar_ai.show_stats', 'False')

        class NoToolProvider:
            model = 'broken-local-model'

            def __init__(self):
                self.calls = 0

            def chat(self, request):
                self.calls += 1
                return ChatResponse(content='Công suất là 9999 W')

            @staticmethod
            def assistant_message(response):
                return {'role': 'assistant', 'content': response.content}

            @staticmethod
            def tool_result_message(tool_call, content):
                return {'role': 'tool', 'content': content}

        provider = NoToolProvider()
        with patch(
                'odoo.addons.smartsolar_ai_chat.providers.factory.get_provider',
                return_value=provider):
            answer = self.env['smartsolar.ai.agent'].chat('Công suất hôm nay bao nhiêu?')
        self.assertEqual(provider.calls, 2)
        self.assertIn('chưa gọi được tool', answer)
        self.assertNotIn('9999', answer)

    def test_conceptual_question_is_not_forced_to_call_tool(self):
        Agent = self.env['smartsolar.ai.agent']
        self.assertFalse(Agent._question_requires_tool('Công suất là gì?'))
        self.assertTrue(Agent._question_requires_tool('Công suất hiện tại bao nhiêu?'))
        self.assertTrue(Agent._question_requires_tool('Cho tôi điện áp pin'))
        self.assertTrue(Agent._question_requires_tool('Sản lượng inverter'))
        self.assertTrue(Agent._question_requires_tool(
            'Giải thích vì sao công suất inverter hôm nay thấp'))

    def test_repeated_identical_tool_call_uses_cache(self):
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('smartsolar_ai.show_stats', 'False')
        executions = []

        class RepeatProvider:
            model = 'local-tool-model'

            def __init__(self):
                self.calls = 0

            def chat(self, request):
                self.calls += 1
                if self.calls <= 2:
                    return ChatResponse(tool_calls=[ToolCall(
                        id='call_%d' % self.calls,
                        name='get_device_status', arguments={'system_id': 1})])
                return ChatResponse(content='Đã tổng hợp.')

            @staticmethod
            def assistant_message(response):
                return {'role': 'assistant', 'content': response.content,
                        'tool_calls': [{'function': {'name': tc.name,
                                                    'arguments': tc.arguments}}
                                       for tc in response.tool_calls]}

            @staticmethod
            def tool_result_message(tool_call, content):
                return {'role': 'tool', 'name': tool_call.name, 'content': content}

        def fake_execute(registry, name, arguments=None):
            executions.append((name, arguments))
            return {'ok': True, 'data': {'devices': []}, 'meta': {}, 'error': None}

        provider = RepeatProvider()
        with patch(
                'odoo.addons.smartsolar_ai_chat.providers.factory.get_provider',
                return_value=provider), patch(
                'odoo.addons.smartsolar_ai.tools.registry.ToolRegistry.execute',
                new=fake_execute):
            answer = self.env['smartsolar.ai.agent'].chat('Kiểm tra thiết bị hiện tại')
        self.assertEqual(len(executions), 1)
        self.assertEqual(provider.calls, 3)
        self.assertIn('Đã tổng hợp', answer)

    def test_failed_tool_does_not_allow_ungrounded_number(self):
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('smartsolar_ai.show_stats', 'False')

        class FailedToolProvider:
            model = 'local-tool-model'

            def __init__(self):
                self.calls = 0

            def chat(self, request):
                self.calls += 1
                if self.calls == 1:
                    return ChatResponse(tool_calls=[ToolCall(
                        id='bad_call', name='get_aggregate', arguments={})])
                return ChatResponse(content='Công suất là 9999 W')

            @staticmethod
            def assistant_message(response):
                return {'role': 'assistant', 'content': response.content}

            @staticmethod
            def tool_result_message(tool_call, content):
                return {'role': 'tool', 'name': tool_call.name, 'content': content}

        def fake_execute(registry, name, arguments=None):
            return {'ok': False, 'data': None, 'meta': {},
                    'error': {'code': 'bad_request', 'message': 'thiếu tham số'}}

        provider = FailedToolProvider()
        with patch(
                'odoo.addons.smartsolar_ai_chat.providers.factory.get_provider',
                return_value=provider), patch(
                'odoo.addons.smartsolar_ai.tools.registry.ToolRegistry.execute',
                new=fake_execute):
            answer = self.env['smartsolar.ai.agent'].chat('Công suất hiện tại bao nhiêu?')
        self.assertEqual(provider.calls, 3)
        self.assertIn('chưa gọi được tool', answer)
        self.assertNotIn('9999', answer)

    def test_metric_catalog_does_not_ground_a_measurement_answer(self):
        Param = self.env['ir.config_parameter'].sudo()
        Param.set_param('smartsolar_ai.show_stats', 'False')

        class CatalogOnlyProvider:
            model = 'local-tool-model'

            def __init__(self):
                self.calls = 0

            def chat(self, request):
                self.calls += 1
                if self.calls == 1:
                    return ChatResponse(tool_calls=[ToolCall(
                        id='catalog', name='list_metrics', arguments={})])
                return ChatResponse(content='Công suất là 9999 W')

            @staticmethod
            def assistant_message(response):
                return {'role': 'assistant', 'content': response.content}

            @staticmethod
            def tool_result_message(tool_call, content):
                return {'role': 'tool', 'name': tool_call.name, 'content': content}

        provider = CatalogOnlyProvider()
        with patch(
                'odoo.addons.smartsolar_ai_chat.providers.factory.get_provider',
                return_value=provider), patch(
                'odoo.addons.smartsolar_ai.tools.registry.ToolRegistry.execute',
                return_value={'ok': True, 'data': {'metrics': []},
                              'meta': {}, 'error': None}):
            answer = self.env['smartsolar.ai.agent'].chat(
                'Công suất inverter hiện tại')
        self.assertEqual(provider.calls, 3)
        self.assertIn('chưa gọi được tool', answer)
        self.assertNotIn('9999', answer)
