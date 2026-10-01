import json
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selection_cache import SelectionCache
from text_translation import TextTranslationService, TextTranslationError
from translation_memory import TranslationMemory


class SelectionCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.memory = TranslationMemory(Path(self.temp.name) / 'memory.sqlite3')
        self.service = self.service_instance()
        self.data = {'text': 'bank', 'source': 'en', 'target': 'zh-CN', 'selectionProvider': 'profile',
                     'memoryPolicy': 'exact', 'mode': 'dictionary', 'documentFingerprint': 'paper',
                     'llm_api': {'model': 'one', 'apiUrl': 'https://example.invalid/v1', 'apiKey': 'PRIVATE_KEY'}}
        self.entry = {'headword': 'bank', 'senses': [{'chinese': '银行；河岸', 'pos': 'n.', 'examples': []}], 'aiGenerated': True}

    def service_instance(self):
        service = TextTranslationService(timeout=3)
        self.addCleanup(service.executor.shutdown)
        return service

    def answer(self, text='银行', **extra):
        return {'status': 'ok', 'translation': text, 'provider': 'test', 'model': 'one', 'cached': False, **extra}

    def test_dictionary_persists_across_restart_papers_and_models_without_context_or_keys(self):
        with patch.object(self.service, '_generate', return_value=self.answer(entry=self.entry)) as generate:
            first = self.service.translate({**self.data, 'context': 'PRIVATE_CONTEXT'}, self.memory)
            self.assertTrue(first['saved'])
            self.assertEqual(generate.call_args.args[2], '')
        other = self.service_instance()
        with patch.object(other, '_generate') as generate:
            result = other.translate({**self.data, 'documentFingerprint': 'other', 'llm_api': {'model': 'two'}, 'allowGenerate': False}, self.memory)
            self.assertEqual(result['entry'], self.entry)
            self.assertEqual(result['model'], 'one')
            self.assertTrue(result['cached'])
            generate.assert_not_called()
        raw = (Path(self.temp.name) / 'selection-cache.sqlite3').read_bytes()
        for private in (b'PRIVATE_KEY', b'PRIVATE_CONTEXT', b'example.invalid'):
            self.assertNotIn(private, raw)

    def test_refresh_bypasses_memory_glossary_and_replaces_cache_only_on_success(self):
        data = {**self.data, 'mode': 'translate'}
        self.memory.upsert('paper', [{'source': 'bank', 'translation': '旧全文译文', 'page': 1, 'paragraphId': 'p', 'status': 'succeeded'}])
        with patch.object(self.service, '_generate', return_value=self.answer('新译文')) as generate:
            self.service.translate({**data, 'cachePolicy': 'refresh', 'glossaryEntries': [{'source': 'bank', 'target': '术语'}]}, self.memory)
            self.assertEqual(self.service.translate(data, self.memory)['translation'], '新译文')
            generate.assert_called_once()
        with patch.object(self.service, '_generate', side_effect=TextTranslationError('provider_error')):
            with self.assertRaises(TextTranslationError):
                self.service.translate({**data, 'cachePolicy': 'refresh'}, self.memory)
        self.assertEqual(self.service.translate(data, self.memory)['translation'], '新译文')
        self.assertEqual(self.memory.lookup('paper', 'bank')['translation'], '旧全文译文')

    def test_context_cache_isolates_papers_models_endpoints_options_and_context(self):
        data = {**self.data, 'mode': 'context', 'context': 'The river bank is steep.'}
        with patch.object(self.service, '_generate', return_value=self.answer()) as generate:
            self.service.translate(data, self.memory)
            self.assertTrue(self.service.translate(data, self.memory)['cached'])
            for change in ({'documentFingerprint': 'second'}, {'context': 'The bank lends money.'}, {'target': 'ja'},
                           *({'llm_api': {**data['llm_api'], **change}} for change in (
                               {'model': 'two'}, {'apiUrl': 'https://other.invalid'}, {'requestOptions': {'temperature': .8}},
                               {'extraData': {'openai_temperature': .8}}, {'reasoningMode': 'off'}))):
                self.service.translate({**data, **change}, self.memory)
            self.assertEqual(generate.call_count, 9)
            self.service.translate({**data, 'llm_api': {**data['llm_api'], 'apiKey': 'NEW_KEY'}}, self.memory)
            self.assertEqual(generate.call_count, 9)

    def test_read_only_miss_no_model_and_case_sensitive_dictionary_keys(self):
        with patch.object(self.service, '_generate') as generate:
            self.assertEqual(self.service.translate({**self.data, 'allowGenerate': False}, self.memory)['status'], 'miss')
            generate.assert_not_called()
        self.assertNotEqual(self.service._key(self.data, ''), self.service._key({**self.data, 'text': 'Bank'}, ''))
        self.assertEqual(self.service._key(self.data, ''), self.service._key({**self.data, 'text': ' bank  '}, ''))

    def test_empty_context_and_invalid_modes_never_generate(self):
        with patch.object(self.service, '_generate') as generate:
            for changes in ({'mode': 'context'}, {'mode': 'context', 'context': 'bank'},
                            {'cachePolicy': 'bad'}, {'allowGenerate': 'false'}, {'mode': 'dictionary', 'text': 'a b c d'}):
                with self.assertRaises(TextTranslationError):
                    self.service.translate({**self.data, **changes}, self.memory)
            generate.assert_not_called()

    def test_database_failure_returns_unsaved_result_and_keeps_old_disk_result(self):
        with patch.object(self.service, '_generate', return_value=self.answer(entry=self.entry)):
            self.service.translate(self.data, self.memory)
        with patch('selection_cache.SelectionCache.put', side_effect=OSError('private path')), \
             patch.object(self.service, '_generate', return_value=self.answer('新词条', entry=self.entry)):
            result = self.service.translate({**self.data, 'cachePolicy': 'refresh'}, self.memory)
        self.assertFalse(result['saved'])
        self.assertEqual(result['translation'], '新词条')
        other = self.service_instance()
        self.assertEqual(other.translate({**self.data, 'allowGenerate': False}, self.memory)['translation'], '银行')

    def test_newer_refresh_wins_even_when_older_request_finishes_later(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = [0]
        def generate(*args):
            calls[0] += 1
            if calls[0] == 1:
                entered.set()
                release.wait(2)
                return self.answer('旧结果', entry=self.entry)
            return self.answer('新结果', entry=self.entry)
        with patch.object(self.service, '_generate', side_effect=generate), ThreadPoolExecutor(2) as pool:
            old = pool.submit(self.service.translate, self.data, self.memory)
            self.assertTrue(entered.wait(1))
            new = self.service.translate({**self.data, 'cachePolicy': 'refresh'}, self.memory)
            self.assertTrue(new['saved'])
            release.set()
            old.result(2)
        other = self.service_instance()
        self.assertEqual(other.translate({**self.data, 'allowGenerate': False}, self.memory)['translation'], '新结果')

    def test_clear_keeps_dictionary_and_prevents_inflight_translation_repopulation(self):
        with patch.object(self.service, '_generate', return_value=self.answer(entry=self.entry)):
            self.service.translate(self.data, self.memory)
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        data = {**self.data, 'mode': 'translate'}
        def generate(*args):
            entered.set(); release.wait(2)
            return self.answer('过期译文')
        with patch.object(self.service, '_generate', side_effect=generate), ThreadPoolExecutor(1) as pool:
            old = pool.submit(self.service.translate, data, self.memory)
            self.assertTrue(entered.wait(1))
            self.service.clear_cache(self.memory)
            release.set(); old.result(2)
        self.assertEqual(self.service.translate({**data, 'allowGenerate': False}, self.memory)['status'], 'miss')
        self.assertTrue(self.service.translate({**self.data, 'allowGenerate': False}, self.memory)['cached'])

    def test_lru_does_not_evict_dictionary(self):
        cache = SelectionCache(Path(self.temp.name) / 'lru.sqlite3')
        cache.MAX_ROWS = 2
        cache.put('dictionary', 'dictionary', {'translation': '词条'})
        cache.put('a', 'translate', {'translation': 'a'})
        cache.put('b', 'context', {'translation': 'b'})
        cache.get('a')
        cache.put('c', 'translate', {'translation': 'c'})
        self.assertIsNone(cache.get('b'))
        self.assertIsNotNone(cache.get('a'))
        self.assertIsNotNone(cache.get('dictionary'))

    def test_model_dictionary_json_validation_and_context_prompt(self):
        translator = SimpleNamespace(resolved_protocol='chat_completions', model='one',
            llm_translate=Mock(return_value=json.dumps({**self.entry, 'phonetic': 'invented'})), client=Mock())
        with patch('text_translation.OpenAITranslator', return_value=translator):
            result = self.service.translate(self.data, self.memory)
            self.assertNotIn('phonetic', result['entry'])
            prompt = translator.llm_translate.call_args.args[0]
            self.assertIn('1-6', prompt)
            self.assertIn('24 Chinese characters', prompt)
            self.assertIn('independent of any paper', prompt)
            translator.llm_translate.return_value = '{"senses": [{"chinese": ""}]}'
            with self.assertRaisesRegex(TextTranslationError, 'invalid_output'):
                self.service.translate({**self.data, 'cachePolicy': 'refresh'}, self.memory)
            self.assertTrue(self.service.translate(self.data, self.memory)['cached'])
            translator.llm_translate.return_value = json.dumps({'pos': 'n.', 'meaning': '河岸', 'explanation': '前文描述了河流。'})
            result = self.service.translate({**self.data, 'mode': 'context', 'context': 'The river bank is steep.'}, self.memory)
            self.assertIn('river bank', translator.llm_translate.call_args.args[0])
            self.assertEqual(result['translation'], 'n. 河岸\n前文描述了河流。')
            self.assertEqual(result['contextMeaning']['meaning'], '河岸')
            for invalid in ['raw answer', '{}', '{"meaning": "河岸", "explanation": 4}', '{"meaning": "河岸", "explanation": "说明", "pos": null}']:
                translator.llm_translate.return_value = invalid
                with self.assertRaisesRegex(TextTranslationError, 'invalid_output'):
                    self.service.translate({**self.data, 'mode': 'context', 'context': 'The river bank is steep.', 'cachePolicy': 'refresh'}, self.memory)
            cached = self.service.translate({**self.data, 'mode': 'context', 'context': 'The river bank is steep.'}, self.memory)
            self.assertEqual(cached['contextMeaning'], result['contextMeaning'])
            self.assertTrue(cached['cached'])

    def test_capabilities_and_clear_http(self):
        import server
        with patch.object(server, 'TEXT_TRANSLATOR', self.service), patch.object(server, 'TRANSLATES_DIR', Path(self.temp.name)):
            client = server.create_app().test_client()
            self.assertTrue(client.post('/selection-capabilities', json={}).json['selectionLearning'])
            self.assertEqual(client.post('/selection-cache/clear', json={}).json['status'], 'ok')


class ContextFormatTests(unittest.TestCase):
    def test_context_fields_are_bounded_and_sentence_pos_is_optional(self):
        from selection_cache import context_meaning
        self.assertEqual(context_meaning({'meaning': ' 句子译法 ', 'explanation': '一句说明。'}), {'meaning': '句子译法', 'explanation': '一句说明。'})
        for invalid in [None, [], {}, {'meaning': '译法', 'explanation': ' '}, {'meaning': '译法', 'explanation': 'x' * 4001}, {'meaning': '译法', 'explanation': '说明', 'pos': 'x' * 81}]:
            with self.assertRaises(ValueError):
                context_meaning(invalid)

    def test_only_context_cache_changes_version_and_dictionary_keys_stay_compatible(self):
        import hashlib
        for mode in ['dictionary', 'translate', 'context']:
            data = {'text': 'bank', 'mode': mode, 'selectionProvider': 'bing'}
            identity = {'schema': 1, 'promptVersion': 1, 'mode': mode, 'text': 'bank', 'source': 'en', 'target': 'zh-cn'}
            if mode != 'dictionary':
                identity['provider'] = 'bing'
            old = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            current = TextTranslationService._key(data, '')
            if mode == 'context':
                self.assertNotEqual(old, current)
            else:
                self.assertEqual(old, current)
