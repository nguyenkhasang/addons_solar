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
