import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from text_translation import TextTranslationService, TextTranslationError
from translation_memory import TranslationMemory


class TextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = TranslationMemory(Path(self.temp.name) / 'translation-memory.sqlite3')
        self.service = TextTranslationService(timeout=1)
        self.addCleanup(self.service.executor.shutdown)
        self.data = {'documentFingerprint': 'paper', 'text': 'physical devices', 'target': 'zh-CN', 'mode': 'translate'}

    def test_health_exposes_selection_protocol_capabilities(self):
        with patch.object(server, 'build_workspace_health', return_value={'writable': True}), \
             patch.object(server, 'build_task_stats', return_value={}), \
             patch.object(server, 'TASK_MANAGER', SimpleNamespace(_queue_blocked=False)):
            capabilities = server.create_app().test_client().get('/health').json['capabilities']
        self.assertTrue(capabilities['exactSelectionTranslation'])
        self.assertTrue(capabilities['bingSelectionTranslation'])

    def test_profile_exact_policy_never_returns_containing_paragraph(self):
        self.memory.upsert('paper', [{'page': 1, 'paragraphId': 'p', 'source': 'The distributions align with expectations.',
                                     'translation': '这些分布符合预期。', 'status': 'succeeded'}])
        data = {**self.data, 'text': 'distributions', 'memoryPolicy': 'exact', 'selectionProvider': 'profile'}
        with patch.object(self.service, '_generate', return_value={'translation': '分布', 'provider': 'openai'}) as generate:
            result = self.service.translate(data, self.memory)
            self.assertEqual(result['translation'], '分布')
            self.assertNotEqual(result['provider'], 'translation-memory')
            self.service.translate(data, self.memory)
            generate.assert_called_once()
            exact = self.service.translate({**data, 'text': 'The distributions align with expectations.'}, self.memory)
            self.assertEqual(exact['provider'], 'translation-memory')
            generate.assert_called_once()

    def test_memory_then_glossary_avoid_provider_even_with_invalid_config(self):
        entry = {'page': 1, 'paragraphId': 'p', 'source': 'Using physical devices.', 'translation': '使用物理设备。', 'status': 'succeeded'}
        self.memory.upsert('paper', [entry])
        with patch.object(self.service, '_generate') as generate:
            self.assertEqual(self.service.translate(self.data, self.memory)['provider'], 'translation-memory')
            for mode in ('lookup', 'translate'):
                data = {**self.data, 'mode': mode, 'documentFingerprint': 'new', 'glossaryEntries': [{'source': 'physical devices', 'target': '物理设备'}]}
                self.assertEqual(self.service.translate(data, self.memory)['provider'], 'glossary')
            generate.assert_not_called()

    def test_cache_key_includes_context_model_and_action_but_reuses_across_documents(self):
        answer = {'status': 'ok', 'translation': '设备', 'cached': False}
        with patch.object(self.service, '_generate', return_value=answer) as generate:
            self.assertFalse(self.service.translate(self.data, self.memory)['cached'])
            self.assertTrue(self.service.translate(self.data, self.memory)['cached'])
            for changes in ({'context': 'changed'}, {'llm_api': {'model': 'other'}}, {'page': 2},
                            {'documentFingerprint': 'other'}, {'mode': 'explain'}):
                self.service.translate({**self.data, **changes}, self.memory)
            self.assertEqual(generate.call_count, 4)

    def test_explanation_uses_real_memory_context(self):
        self.memory.upsert('paper', [{'page': 1, 'paragraphId': 'p', 'source': 'Using physical devices.', 'translation': '使用物理设备。', 'status': 'succeeded'}])
        with patch.object(self.service, '_generate', return_value={'explanation': '说明'}) as generate:
            self.service.translate({**self.data, 'mode': 'explain', 'context': 'wrong context'}, self.memory)
            self.assertIn('Using physical devices.', generate.call_args.args[2])
            self.assertIn('使用物理设备。', generate.call_args.args[2])

    def test_provider_success_prompt_empty_output_and_sanitized_failure(self):
        data = {**self.data, 'service': 'openaicompatible', 'llm_api': {'apiKey': 'test', 'apiUrl': 'https://example.invalid/v1', 'model': 'test-model'}}
        translator = SimpleNamespace(resolved_protocol='chat_completions', model='test-model', llm_translate=Mock(return_value='物理设备'), client=Mock())
        with patch('text_translation.OpenAITranslator', return_value=translator):
            result = self.service.translate({**data, 'context': 'paper context'}, self.memory)
            self.assertEqual(result['translation'], '物理设备')
            self.assertIn('paper context', translator.llm_translate.call_args.args[0])
            translator.client.close.assert_called_once()
            translator.llm_translate.return_value = ''
            with self.assertRaisesRegex(TextTranslationError, 'empty_output'):
                self.service.translate({**data, 'text': 'new word'}, self.memory)
            translator.llm_translate.side_effect = RuntimeError('secret-key paper text')
            with self.assertRaisesRegex(TextTranslationError, '^provider_error$'):
                self.service.translate({**data, 'text': 'another word'}, self.memory)

    def test_timeout_coalesces_identical_requests_and_bounds_concurrency(self):
        self.service.timeout = .02
        release = threading.Event()
        self.addCleanup(release.set)
        def slow(*args):
            release.wait(2)
            return {'translation': 'answer', 'cached': False}
        with patch.object(self.service, '_generate', side_effect=slow) as generate:
            for text in ('word', 'word', 'other'):
                with self.assertRaisesRegex(TextTranslationError, 'provider_timeout'):
                    self.service.translate({**self.data, 'text': text}, self.memory)
            with self.assertRaisesRegex(TextTranslationError, 'selection_busy'):
                self.service.translate({**self.data, 'text': 'third'}, self.memory)
            self.assertEqual(generate.call_count, 2)
            release.set()

    def test_http_success_and_errors(self):
        with patch.object(server, 'TEXT_TRANSLATOR', self.service), patch.object(server, 'TRANSLATES_DIR', Path(self.temp.name)):
            client = server.create_app().test_client()
            for data, code in (({}, 'empty_text'), ([], 'invalid_request'), ({'text': ''}, 'empty_text'),
                               ({**self.data, 'page': True}, 'invalid_page'), ({**self.data, 'mode': 'invalid'}, 'invalid_mode')):
                response = client.post('/translate-text', json=data)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json['code'], code)
            with patch.object(self.service, '_generate', side_effect=TextTranslationError('provider_timeout', 504)):
                response = client.post('/translate-text', json=self.data)
                self.assertEqual(response.status_code, 504)
            with patch.object(self.service, '_generate', return_value={'status': 'ok', 'translation': '成功'}):
                self.assertEqual(client.post('/translate-text', json=self.data).json['translation'], '成功')

    def test_invalid_provider_config_does_not_start_model(self):
        with patch('text_translation.OpenAITranslator') as provider:
            with self.assertRaisesRegex(TextTranslationError, 'invalid_config'):
                self.service.translate({**self.data, 'service': 'not-a-provider'}, self.memory)
            provider.assert_not_called()

class FreeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = TranslationMemory(Path(self.temp.name) / 'memory.sqlite3')
        self.service = TextTranslationService()
        self.addCleanup(self.service.executor.shutdown)
        self.data = {'text': 'Selected sentence.', 'source': 'en', 'target': 'zh-CN',
                     'selectionProvider': 'bing', 'memoryPolicy': 'exact', 'documentFingerprint': 'paper'}

    def page(self):
        return Mock(status_code=200, text=(
            'IG:"page-wide-wrong-id"; {"ig":"translator-session"}; '
            '<div data-iid="translator.1"></div><div data-iid="translator.2"></div>'
            'var params_AbusePreventionHelper = [123,"session-token",3600000];'))

    def response(self, text='选句译文'):
        return Mock(status_code=200, json=Mock(return_value=[{'translations': [{'text': text, 'to': 'zh-Hans'}]}]))

    def test_exact_policy_retranslates_containment_and_legacy_stays_unchanged(self):
        self.memory.upsert('paper', [{'page': 1, 'paragraphId': 'p', 'source': 'Before. Selected sentence. After.',
                                    'translation': '前文。选句译文。后文。', 'status': 'succeeded'}])
        with patch.object(self.service, '_generate', return_value={'translation': '选句译文', 'provider': 'bing'}) as generate:
            self.assertEqual(self.service.translate(self.data, self.memory)['translation'], '选句译文')
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(self.service.translate({**self.data, 'text': 'Before. Selected sentence. After.'}, self.memory)['provider'], 'translation-memory')
            legacy = {k: v for k, v in self.data.items() if k not in ('memoryPolicy', 'selectionProvider')}
            self.assertEqual(self.service.translate(legacy, self.memory)['provider'], 'translation-memory')
            self.assertEqual(generate.call_count, 1)

    def test_free_request_sends_only_selection_and_languages_never_model(self):
        with patch('text_translation.requests.Session') as factory, patch('text_translation.OpenAITranslator') as model:
            session = factory.return_value.__enter__.return_value
            session.get.return_value = self.page()
            session.post.return_value = self.response('一句 & 译文')
            result = self.service.translate({**self.data, 'context': 'private context', 'llm_api': {'apiKey': 'secret'}}, self.memory)
            self.assertEqual(result['translation'], '一句 & 译文')
            self.assertEqual(result['provider'], 'bing')
            self.assertEqual(session.get.call_args.args[0], 'https://www.bing.com/translator')
            self.assertEqual(session.post.call_args.args[0], 'https://www.bing.com/ttranslatev3')
            self.assertEqual(session.post.call_args.kwargs['params'], {'IG': 'translator-session', 'IID': 'translator.2'})
            self.assertEqual(session.post.call_args.kwargs['data'], {
                'text': 'Selected sentence.', 'fromLang': 'en', 'to': 'zh-Hans', 'key': 123, 'token': 'session-token'})
            self.assertNotIn('secret', str(session.mock_calls))
            self.assertNotIn('private context', str(session.mock_calls))
            model.assert_not_called()
            self.assertTrue(self.service.translate({**self.data, 'context': 'other context'}, self.memory)['cached'])
            self.assertEqual(session.get.call_count, 1)
            self.assertEqual(session.post.call_count, 1)

    def test_bing_language_mapping_and_auto_detection(self):
        with patch('text_translation.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.get.return_value = self.page()
            session.post.return_value = self.response()
            for source, target, expected in [('auto', 'zh_TW', ('auto-detect', 'zh-Hant')),
                                             ('zh-CN', 'en', ('zh-Hans', 'en')),
                                             ('en', 'ja', ('en', 'ja'))]:
                self.service.free_last_request = 0
                self.service.translate({**self.data, 'source': source, 'target': target}, self.memory)
                body = session.post.call_args.kwargs['data']
                self.assertEqual((body['fromLang'], body['to']), expected)

    def test_cache_isolated_by_language_and_provider_not_model_credentials(self):
        with patch.object(self.service, '_generate', return_value={'translation': 'ok', 'cached': False}) as generate:
            self.service.translate(self.data, self.memory)
            self.service.translate({**self.data, 'llm_api': {'apiKey': 'secret'}, 'page': 9, 'context': 'other'}, self.memory)
            self.assertEqual(generate.call_count, 1)
            self.service.translate({**self.data, 'target': 'ja'}, self.memory)
            self.service.translate({**self.data, 'selectionProvider': 'profile'}, self.memory)
            self.assertEqual(generate.call_count, 3)

    def test_utf8_chunks_preserve_all_punctuation_whitespace_and_characters(self):
        from text_translation import split_selection_text
        for text in ['word ' * 600, '🙂汉字' * 600, 'a' * 1500, 'Dr. A: Why? Yes!\n下一句。继续。' * 60]:
            chunks = split_selection_text(text)
            self.assertEqual(''.join(chunks), text)
            self.assertTrue(all(0 < len(chunk.encode('utf-8')) <= 1000 for chunk in chunks))
        self.assertEqual(split_selection_text('Hello, world!'), ['Hello, world!'])

    def test_failures_are_sanitized_not_cached_and_never_fall_back(self):
        import requests
        failures = [(Mock(status_code=429), 'provider_quota'), (Mock(status_code=403), 'provider_error'),
            (Mock(status_code=401), 'provider_error'), (self.response(''), 'empty_output'),
            (Mock(status_code=200, json=Mock(return_value={'statusCode': 429})), 'provider_quota'),
            (Mock(status_code=200, json=Mock(return_value={'ShowCaptcha': True})), 'provider_error'),
            (Mock(status_code=200, json=Mock(return_value=[])), 'provider_error'),
            (Mock(status_code=200, json=Mock(return_value=[{'translations': []}])), 'provider_error'),
            (Mock(status_code=200, json=Mock(side_effect=ValueError('private'))), 'provider_error'),
            (requests.Timeout('secret URL'), 'provider_timeout')]
        with patch('text_translation.requests.Session') as factory, patch('text_translation.OpenAITranslator') as model:
            session = factory.return_value.__enter__.return_value
            session.get.return_value = self.page()
            for response, code in failures:
                self.service.free_last_request = 0
                session.post.side_effect = response if isinstance(response, Exception) else None
                session.post.return_value = response
                with self.assertRaisesRegex(TextTranslationError, '^' + code + '$'):
                    self.service.translate(self.data, self.memory)
                self.assertEqual(len(self.service.cache), 0)
            model.assert_not_called()

    def test_bing_session_errors_do_not_send_text(self):
        import requests
        with patch('text_translation.requests.Session') as factory, patch('text_translation.OpenAITranslator') as model:
            session = factory.return_value.__enter__.return_value
            for page, code in [(Mock(status_code=429), 'provider_quota'),
                               (Mock(status_code=403), 'provider_error'),
                               (Mock(status_code=200, text='<html>Changed page</html>'), 'provider_error'),
                               (requests.Timeout('private'), 'provider_timeout')]:
                session.get.side_effect = page if isinstance(page, Exception) else None
                session.get.return_value = page
                with self.assertRaisesRegex(TextTranslationError, '^' + code + '$'):
                    self.service.translate(self.data, self.memory)
                self.assertFalse(self.service.cache)
            session.post.assert_not_called()
            model.assert_not_called()

    def test_rate_limit_deadline_and_no_partial_success(self):
        now = [100.0]
        starts, chunks = [], []
        def post(*args, **kwargs):
            starts.append(now[0])
            chunks.append(kwargs['data']['text'])
            return self.response('分片')
        with patch('text_translation.requests.Session') as factory, \
             patch('text_translation.time.monotonic', side_effect=lambda: now[0]), \
             patch('text_translation.time.sleep', side_effect=lambda delay: now.__setitem__(0, now[0] + delay)):
            session = factory.return_value.__enter__.return_value
            session.get.return_value = self.page()
            session.post.side_effect = post
            result = self.service._generate_bing(self.data, 'x' * 2500, 'zh-CN')
            self.assertEqual(starts, [100, 101, 102])
            self.assertEqual(''.join(chunks), 'x' * 2500)
            self.assertEqual(result['translation'], '分片 分片 分片')
            self.service.timeout = 1.5
            self.service.free_last_request = 0
            with self.assertRaisesRegex(TextTranslationError, 'provider_timeout'):
                self.service._generate_bing(self.data, 'x' * 3500, 'zh-CN')

    def test_failed_later_chunk_does_not_cache_partial_translation(self):
        with patch('text_translation.requests.Session') as factory, patch('text_translation.time.sleep'):
            session = factory.return_value.__enter__.return_value
            session.get.return_value = self.page()
            session.post.side_effect = [self.response('第一部分'), Mock(status_code=429)]
            with self.assertRaisesRegex(TextTranslationError, 'provider_quota'):
                self.service.translate({**self.data, 'text': 'x' * 1500}, self.memory)
            self.assertFalse(self.service.cache)
            self.assertEqual(session.post.call_count, 2)

    def test_invalid_provider_policy_and_mode_rejected_without_requests(self):
        with patch('text_translation.requests.Session') as session:
            for field, value in [('selectionProvider', 'other'), ('selectionProvider', 'mymemory'),
                                 ('memoryPolicy', 'other'), ('mode', 'explain')]:
                with self.assertRaises(TextTranslationError):
                    self.service.translate({**self.data, field: value}, self.memory)
            session.assert_not_called()
