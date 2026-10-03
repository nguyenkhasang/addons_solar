# -*- coding: utf-8 -*-
import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch

from odoo.tests.case import TestCase

from ..providers.base import ChatRequest, ProviderError
from ..providers.codex_cli import CodexCLIProvider
from ..providers.factory import get_provider


class TestCodexProvider(TestCase):
    test_tags = {'standard', 'post_install', 'smartsolar_ai_chat'}
    test_module = 'smartsolar_ai_chat'
    tools = [{'type': 'function', 'function': {
        'name': 'get_aggregate', 'description': 'Read Solar data',
        'parameters': {'type': 'object', 'properties': {'system_id': {'type': 'integer'}}},
    }}]

    def test_cli_round_trip_preserves_tool_history_and_usage(self):
        provider = CodexCLIProvider(binary='/opt/codex', model='test-model')
        commands = []

        def spawn(command, **kwargs):
            commands.append(command)
            self.assertEqual(kwargs['cwd'], command[command.index('-C') + 1])
            self.assertNotIn('DB_PASSWORD', kwargs['env'])
            self.assertNotIn('OPENAI_API_KEY', kwargs['env'])
            self.assertNotIn('shell', kwargs)
            process = MagicMock()
            process.returncode = 0
            process.__enter__.return_value = process

            def communicate(prompt, timeout):
                self.assertEqual(timeout, 120)
                self.assertIn('Công suất hiện tại?', prompt)
                self.assertIn('parameters', prompt)
                self.assertNotIn('Công suất hiện tại?', ' '.join(command))
                Path(command[command.index('-o') + 1]).write_text(json.dumps({
                    'content': '', 'tool_calls': [{
                        'name': 'get_aggregate', 'arguments': '{"system_id": 7}',
                    }],
                }))
                return json.dumps({'type': 'turn.completed', 'usage': {
                    'input_tokens': 40, 'output_tokens': 10,
                }}), ''

            process.communicate.side_effect = communicate
            return process

        with patch('shutil.which', return_value='/opt/codex'), patch(
                'subprocess.Popen', side_effect=spawn), patch.dict(
                'os.environ', {'DB_PASSWORD': 'private', 'OPENAI_API_KEY': 'private'}):
            response = provider.chat(ChatRequest(messages=[{
                'role': 'user', 'content': 'Công suất hiện tại?',
            }], tools=self.tools))
        self.assertEqual(response.tool_calls[0].arguments, {'system_id': 7})
        self.assertEqual(response.usage['prompt_tokens'], 40)
        message = provider.assistant_message(response)
        self.assertEqual(json.loads(message['tool_calls'][0]['function']['arguments']),
                         {'system_id': 7})
        result = provider.tool_result_message(response.tool_calls[0], '{"ok":true}')
        self.assertEqual(result['tool_call_id'], response.tool_calls[0].id)
        command = commands[0]
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--ephemeral', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('web_search="disabled"', command)
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertFalse(Path(command[command.index('-C') + 1]).exists())

    def test_invalid_or_unrequested_tools_fail_closed(self):
        for raw, tools in [
            ('not JSON', {'get_aggregate': {}}),
            ('{"content": "ok", "tool_calls": [{"name": "shell", "arguments": "{}"}]}',
             {'get_aggregate': {}}),
            ('{"content": "ok", "tool_calls": [{"name": "get_aggregate", "arguments": "[]"}]}',
             {'get_aggregate': {}}),
            ('{"content": "ok", "tool_calls": [{"name": "get_aggregate", "arguments": "{}"}]}', {}),
            ('{"content": 7, "tool_calls": []}', {}),
        ]:
            with self.subTest(raw=raw), self.assertRaises(ProviderError):
                CodexCLIProvider._parse_result(raw, tools)

    def test_missing_cli_reports_actionable_error(self):
        with patch('shutil.which', return_value=None), self.assertRaisesRegex(
                ProviderError, 'codex login'):
            CodexCLIProvider().chat(ChatRequest(messages=[]))

    def test_timeout_kills_process_group(self):
        process = MagicMock()
        process.pid = 4321
        process.__enter__.return_value = process
        process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 1), ('', '')]
        with patch('shutil.which', return_value='/opt/codex'), patch(
                'subprocess.Popen', return_value=process), patch('os.killpg') as kill:
            with self.assertRaisesRegex(ProviderError, 'quá thời gian'):
                CodexCLIProvider(timeout=1).chat(ChatRequest(messages=[]))
        kill.assert_called_once()
        self.assertEqual(kill.call_args.args[0], process.pid)

    def test_cli_errors_do_not_echo_private_diagnostics(self):
        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = 1
        process.communicate.return_value = ('', 'secret token and conversation')
        with patch('shutil.which', return_value='/opt/codex'), patch(
                'subprocess.Popen', return_value=process):
            with self.assertRaises(ProviderError) as caught:
                CodexCLIProvider().chat(ChatRequest(messages=[]))
        self.assertNotIn('secret', str(caught.exception))
        self.assertIn('codex login status', str(caught.exception))

    def test_codex_does_not_inherit_ollama_model(self):
        values = {'smartsolar_ai.provider': 'codex', 'smartsolar_ai.model': 'gemma4:12b'}
        env = MagicMock()
        env.__getitem__.return_value.sudo.return_value.get_param.side_effect = (
            lambda key, default=None: values.get(key, default))
        provider = get_provider(env)
        self.assertIsInstance(provider, CodexCLIProvider)
        self.assertIsNone(provider.model)
        values['smartsolar_ai.codex_model'] = 'chosen-codex-model'
        self.assertEqual(get_provider(env).model, 'chosen-codex-model')

    def test_vision_data_is_attached_without_base64_in_prompt(self):
        import tempfile
        provider = CodexCLIProvider()
        message = provider.build_image_message('Đọc biểu đồ', [{'mime': 'image/png', 'b64': 'YWJj'}])
        with tempfile.TemporaryDirectory() as tmp:
            messages, images = provider._prepare_messages([message], Path(tmp))
            self.assertEqual(Path(images[0]).read_bytes(), b'abc')
            self.assertNotIn('YWJj', json.dumps(messages))

    def test_compact_tool_specs_preserve_constraints_and_do_not_mutate(self):
        tools = {'read': {'name': 'read', 'description': 'Read measurements',
                         'parameters': {'type': 'object', 'required': ['metrics', 'start'],
                                        'properties': {
                                            'metrics': {'type': 'array', 'minItems': 1, 'maxItems': 10,
                                                        'description': 'Repeated metric prose',
                                                        'items': {'type': 'string', 'enum': ['power', 'energy']}},
                                            'start': {'type': 'string', 'description': 'Repeated time prose'},
                                            'threshold': {'type': 'number', 'minimum': 0,
                                                          'description': 'Deviation in standard deviations'},
                                        }}}}
        before = json.dumps(tools)
        compact = CodexCLIProvider._compact_tools(tools)[0]
        self.assertEqual(json.dumps(tools), before)
        parameters = compact['parameters']
        self.assertEqual(parameters['required'], ['metrics', 'start'])
        self.assertEqual(parameters['properties']['metrics']['maxItems'], 10)
        self.assertEqual(parameters['properties']['metrics']['items']['enum'], ['power', 'energy'])
        self.assertNotIn('description', parameters['properties']['start'])
        self.assertEqual(parameters['properties']['threshold']['minimum'], 0)
        self.assertIn('description', parameters['properties']['threshold'])

    def test_compact_tool_result_preserves_evidence_and_warnings(self):
        envelope = {'ok': True, 'data': {'value': None, 'available': False,
                                       'coverage_pct': 65.74, 'range': ['a', 'b'],
                                       'quality': {'warnings': [{'code': 'constant_counter'}]}},
                    'meta': {'tool': 'read', 'generated_at': 'now',
                             'electrical_terminology': 'Repeated prose',
                             'instruction': 'Unavailable is not zero', 'cached': True},
                    'error': None}
        messages = [{'role': 'tool', 'content': json.dumps(envelope), 'tool_call_id': 'test'}]
        compact = CodexCLIProvider._compact_messages(messages)
        data = compact[0]['content']
        self.assertEqual(data['data'], envelope['data'])
        self.assertEqual(data['meta'], {'instruction': 'Unavailable is not zero', 'cached': True})
        self.assertEqual(compact[0]['tool_call_id'], 'test')
        self.assertIn('Repeated prose', messages[0]['content'])

    def test_cli_uses_local_planner_instructions_and_reports_cached_usage(self):
        provider = CodexCLIProvider(binary='/opt/codex', model='test-model')
        def spawn(command, **kwargs):
            config = next(value for value in command if value.startswith('model_instructions_file='))
            path = Path(json.loads(config.split('=', 1)[1]))
            instructions = path.read_text()
            self.assertIn('Không chạy lệnh', instructions)
            self.assertIn('tool_calls', instructions)
            self.assertEqual(path.parent, Path(kwargs['cwd']))
            process = MagicMock()
            process.returncode = 0
            process.__enter__.return_value = process
            def communicate(prompt, timeout):
                Path(command[command.index('-o') + 1]).write_text(
                    json.dumps({'content': 'ok', 'tool_calls': []}))
                self.assertEqual(json.loads(prompt)['tools'], [])
                return json.dumps({'type': 'turn.completed', 'usage': {
                    'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 5}}), ''
            process.communicate.side_effect = communicate
            return process
        with patch('shutil.which', return_value='/opt/codex'), patch('subprocess.Popen', side_effect=spawn):
            response = provider.chat(ChatRequest(messages=[{'role': 'user', 'content': 'hello'}],
                                                  tools=self.tools, tool_choice='none'))
        self.assertEqual(response.usage['prompt_tokens'], 100)
        self.assertEqual(response.usage['cached_prompt_tokens'], 60)
        self.assertEqual(response.usage['llm_calls'], 1)

    def test_required_tool_choice_schema_does_not_block_final_answers(self):
        required = CodexCLIProvider._schema({'read': {}}, require_tool=True)
        self.assertEqual(required['properties']['tool_calls']['minItems'], 1)
        automatic = CodexCLIProvider._schema({'read': {}})
        self.assertNotIn('minItems', automatic['properties']['tool_calls'])
        final = CodexCLIProvider._schema({}, require_tool=True)
        self.assertEqual(final['properties']['tool_calls']['maxItems'], 0)

    def test_compact_series_table_preserves_every_timestamp_and_value(self):
        data = {'series': {'points': [{'t': 'a', 'v': 0}, {'t': 'b', 'v': None},
                                     {'t': 'c', 'v': 2319}], 'truncated': True,
                           'original_count': 778, 'unit': 'W'}}
        compact = CodexCLIProvider._compact_series(data)
        self.assertEqual(compact['series']['point_columns'], ['t', 'v'])
        self.assertEqual(compact['series']['points'], [['a', 0], ['b', None], ['c', 2319]])
        self.assertTrue(compact['series']['truncated'])
        self.assertEqual(compact['series']['original_count'], 778)
        self.assertEqual(data['series']['points'][2]['v'], 2319)

    def test_cli_tool_json_is_not_double_encoded(self):
        envelope = {'ok': True, 'data': {'value': 6.8, 'available': True,
                                       'quality': {'warnings': [{'code': 'constant_counter'}]}}}
        original = [{'role': 'tool', 'tool_call_id': 'id', 'content': json.dumps(envelope)}]
        wire = json.dumps({'messages': CodexCLIProvider._compact_messages(original)},
                          ensure_ascii=False, separators=(',', ':'))
        recovered = json.loads(wire)['messages'][0]
        self.assertEqual(recovered['content'], envelope)
        self.assertEqual(recovered['tool_call_id'], 'id')
        self.assertIsInstance(original[0]['content'], str)
        double_encoded = json.dumps({'messages': original}, separators=(',', ':'))
        self.assertLess(len(wire), len(double_encoded))


    def test_compare_schema_deduplicates_actual_period_names(self):
        from odoo.addons.smartsolar_ai.tools.registry import ToolRegistry
        tool = next(t for t in ToolRegistry(None).specs()
                    if t['name'] == 'compare_periods')
        compact = CodexCLIProvider._compact_tools({'compare_periods': tool})[0]
        for field in ['a_start', 'a_end', 'b_start', 'b_end']:
            self.assertEqual(compact['parameters']['properties'][field]['type'], 'string')
            self.assertNotIn('description', compact['parameters']['properties'][field])
            self.assertIn(field, compact['parameters']['required'])
        self.assertEqual(tool['parameters']['required'], compact['parameters']['required'])
