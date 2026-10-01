import codecs
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from src.main.agent.agent import Agent
from src.main.api.openai_compatible import OpenAICompatibleProvider
from src.main.api.usage import UsageTracker
from src.main.config import load_config
from src.main.encoding import configure_terminal_encoding, decode_output, decode_text, encode_text
from src.main.tools.base import ToolError


class WindowsEncodingTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.agent = Agent(provider=OpenAICompatibleProvider(api_key='test', base_url='https://example.invalid/v1', usage_tracker=UsageTracker()), workspace=self.root, system_prompt='test',
                           model='test', auto_approve=True)
        self.addCleanup(self.agent.provider.session.close)

    def test_output_encodings_and_bom(self):
        for encoding in ('utf-8', 'utf-8-sig', 'gbk', 'utf-16', 'utf-32'):
            with self.subTest(encoding=encoding):
                self.assertEqual(decode_output('中文输出\r\n'.encode(encoding)), '中文输出\n')
        self.assertEqual(decode_output(b'bad\xff'), 'bad\ufffd')

    def test_bom_round_trip_preserves_endianness(self):
        for marker, encoding in ((codecs.BOM_UTF16_BE, 'utf-16-be'),
                                 (codecs.BOM_UTF16_LE, 'utf-16-le'),
                                 (codecs.BOM_UTF32_BE, 'utf-32-be'),
                                 (codecs.BOM_UTF32_LE, 'utf-32-le')):
            data = marker + '中文\r\n'.encode(encoding)
            text, detected = decode_text(data)
            self.assertEqual(encode_text(text, detected), data)

    def test_commands_decode_stdout_and_stderr_separately(self):
        completed = subprocess.CompletedProcess(['tool'], 0, '中文🙂'.encode(), '警告'.encode('gbk'))
        with patch('src.main.tools.command.subprocess.run', return_value=completed) as run:
            result = self.agent.tool_handlers['run_command']('tool')
        self.assertIn('中文🙂\n警告', result)
        self.assertNotIn('text', run.call_args.kwargs)

    def test_read_and_replace_preserve_encoding_bom_and_crlf(self):
        for encoding in ('utf-8', 'utf-8-sig', 'gbk', 'utf-16'):
            with self.subTest(encoding=encoding):
                target = self.root / '中文.txt'
                target.write_bytes('第一行\r\n第二行\r\n'.encode(encoding))
                self.assertIn('1: 第一行', self.agent.tool_handlers['read_file']('中文.txt'))
                self.agent.tool_handlers['replace_in_file']('中文.txt', '第一行\n第二行', '你好\n世界')
                self.assertEqual(target.read_bytes(), '你好\r\n世界\r\n'.encode(encoding))
                self.agent.tool_handlers['write_file']('中文.txt', '新的\n内容\n')
                self.assertEqual(target.read_bytes(), '新的\r\n内容\r\n'.encode(encoding))

    def test_unencodable_edit_does_not_truncate_gbk_file(self):
        target = self.root / 'old.txt'
        original = '原文'.encode('gbk')
        target.write_bytes(original)
        self.agent.tool_handlers['read_file']('old.txt')
        with self.assertRaises(ToolError):
            self.agent.tool_handlers['write_file']('old.txt', '🙂')
        self.assertEqual(target.read_bytes(), original)

    def test_new_file_is_utf8(self):
        self.agent.tool_handlers['write_file']('new.txt', '中文🙂\n')
        self.assertEqual((self.root / 'new.txt').read_bytes(), '中文🙂\n'.encode())

    def test_search_windows_mixed_encodings(self):
        for encoding in ('utf-8', 'utf-8-sig', 'gbk'):
            (self.root / (encoding + '.txt')).write_bytes('中文匹配'.encode(encoding))
        with patch('src.main.tools.search.sys.platform', 'win32'):
            result = self.agent.tool_handlers['search_files']('中文匹配')
        self.assertEqual(result.count('中文匹配'), 3)

    def test_rg_utf8_output_under_gbk_locale(self):
        completed = subprocess.CompletedProcess(['rg'], 0, '文件.txt:1:中文🙂'.encode(), b'')
        with patch('src.main.tools.search.sys.platform', 'linux'), \
                patch('src.main.tools.search.subprocess.run', return_value=completed), \
                patch('src.main.encoding.locale.getpreferredencoding', return_value='gbk'):
            self.assertEqual(self.agent.tool_handlers['search_files']('中文'), '文件.txt:1:中文🙂')

    def test_config_with_utf8_bom_or_gbk(self):
        config = '[API_MANAGER]\nBASE_URL="https://example.invalid/v1"\nAPI_KEY="test"\nMODEL="中文"\n'
        target = self.root / 'config.toml'
        for encoding in ('utf-8-sig', 'gbk'):
            target.write_bytes(config.encode(encoding))
            self.assertEqual(load_config(target).api.model, '中文')

    def test_windows_redirected_output_is_utf8(self):
        buffer = BytesIO()
        stream = TextIOWrapper(buffer, encoding='gbk')
        self.addCleanup(stream.close)
        with patch('src.main.encoding.sys.platform', 'win32'), \
                patch('src.main.encoding.sys.stdout', stream), \
                patch('src.main.encoding.sys.stderr', StringIO()):
            configure_terminal_encoding()
            stream.write('中文🙂')
            stream.flush()
        self.assertEqual(buffer.getvalue(), '中文🙂'.encode())

    def test_windows_console_keeps_encoding_and_handles_emoji(self):
        buffer = BytesIO()
        stream = TextIOWrapper(buffer, encoding='gbk')
        self.addCleanup(stream.close)
        with patch('src.main.encoding.sys.platform', 'win32'), \
                patch('src.main.encoding.sys.stdout', stream), \
                patch('src.main.encoding.sys.stderr', StringIO()), \
                patch.object(stream, 'isatty', return_value=True):
            configure_terminal_encoding()
            self.assertEqual(stream.encoding, 'gbk')
            stream.write('中文🙂')
            stream.flush()
        self.assertEqual(buffer.getvalue().decode('gbk'), '中文\\U0001f642')
