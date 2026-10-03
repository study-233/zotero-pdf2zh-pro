"""Cancel individual Codex selections without cancelling another reader's turn."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
from text_translation import TextTranslationError, TextTranslationService
from translation_memory import TranslationMemory


class TextCancellationTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.service = TextTranslationService(timeout=2)
        self.addCleanup(self.service.close)
        self.memory = TranslationMemory(Path(self.directory) / 'memory.sqlite3')
        self.client = Mock()
        self.client.check_ready.return_value = {'reasoningEffort': 'medium'}
        self.client.translate.return_value = SimpleNamespace(text='样本', usage={})
        self.enterContext(patch('pdf2zh_next.translator.base_translator.TranslationCache'))
        self.enterContext(patch('pdf2zh_next.translator.translator_impl.codex.get_codex_client', return_value=self.client))
        self.data = {'text': 'sample', 'target': 'zh-CN', 'service': 'codex',
                     'llm_api': {'model': 'gpt-6-luna'}, 'requestId': 'request_1234567890abcdef'}

    def test_active_http_cancellation_interrupts_callback_and_never_saves_result(self):
        started, release = threading.Event(), threading.Event()

        def generate(_prompt, **options):
            started.set()
            self.assertTrue(release.wait(1))
            options['check_cancelled']()
            return SimpleNamespace(text='must not save', usage={})

        self.client.translate.side_effect = generate
        with ThreadPoolExecutor(1) as caller:
            future = caller.submit(self.service.translate, self.data, self.memory)
            try:
                self.assertTrue(started.wait(1))
                with patch.object(server, 'TEXT_TRANSLATOR', self.service):
                    response = server.create_app().test_client().post('/cancel-text', json={'requestId': self.data['requestId']})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json['cancelled'])
            finally:
                release.set()
            with self.assertRaisesRegex(TextTranslationError, 'selection_cancelled'):
                future.result(timeout=1)
        self.assertFalse(self.service._request_events)
        self.assertFalse(self.service.cache)
        read_only = {key: value for key, value in self.data.items() if key != 'requestId'}
        self.assertEqual(self.service.translate({**read_only, 'allowGenerate': False}, self.memory)['status'], 'miss')
        self.assertEqual(self.client.translate.call_count, 1)

    def test_unique_ids_do_not_coalesce_and_cancel_does_not_affect_other_request(self):
        started = [threading.Event(), threading.Event()]
        release = threading.Event()
        order = []
        order_lock = threading.Lock()

        def generate(_prompt, **options):
            with order_lock:
                index = len(order)
                order.append(index)
            started[index].set()
            self.assertTrue(release.wait(1))
            options['check_cancelled']()
            return SimpleNamespace(text='第二个请求', usage={})

        self.client.translate.side_effect = generate
        with ThreadPoolExecutor(2) as caller:
            first = caller.submit(self.service.translate, self.data, self.memory)
            self.assertTrue(started[0].wait(1))
            second = caller.submit(self.service.translate, {**self.data, 'requestId': 'request_abcdef1234567890'}, self.memory)
            try:
                self.assertTrue(started[1].wait(1))
                self.service.cancel(self.data['requestId'])
            finally:
                release.set()
            with self.assertRaisesRegex(TextTranslationError, 'selection_cancelled'):
                first.result(timeout=1)
            self.assertEqual(second.result(timeout=1)['translation'], '第二个请求')
        self.assertEqual(len(order), 2)
        self.assertFalse(self.service._request_events)
        self.assertEqual(len(self.service.cache), 1)

    def test_early_cancellation_prevents_generation_and_expires(self):
        with patch('text_translation.time.monotonic', return_value=100):
            self.service.cancel(self.data['requestId'])
            with self.assertRaisesRegex(TextTranslationError, 'selection_cancelled'):
                self.service.translate(self.data, self.memory)
        self.client.translate.assert_not_called()
        with patch('text_translation.time.monotonic', return_value=161):
            self.assertEqual(self.service.translate(self.data, self.memory)['translation'], '样本')
        self.client.translate.assert_called_once()

    def test_cancel_tombstones_are_bounded(self):
        for index in range(150):
            self.service.cancel(f'request_{index:020d}')
        self.assertEqual(len(self.service._request_history), 128)
        self.assertNotIn('request_00000000000000000000', self.service._request_history)
        self.assertIn('request_00000000000000000149', self.service._request_history)

    def test_duplicate_active_and_completed_ids_are_rejected(self):
        started, release = threading.Event(), threading.Event()

        def generate(_prompt, **options):
            started.set()
            self.assertTrue(release.wait(1))
            return SimpleNamespace(text='样本', usage={})

        self.client.translate.side_effect = generate
        with ThreadPoolExecutor(1) as caller:
            first = caller.submit(self.service.translate, self.data, self.memory)
            try:
                self.assertTrue(started.wait(1))
                with self.assertRaisesRegex(TextTranslationError, 'duplicate_request_id'):
                    self.service.translate(self.data, self.memory)
            finally:
                release.set()
            first.result(timeout=1)
        with self.assertRaisesRegex(TextTranslationError, 'duplicate_request_id'):
            self.service.translate(self.data, self.memory)
        self.assertFalse(self.service.cancel(self.data['requestId'])['cancelled'])
        self.client.translate.assert_called_once()

    def test_outer_timeout_sets_cancel_event_before_registry_cleanup(self):
        self.service.timeout = .03
        release = threading.Event()
        events = []

        def generate(_prompt, **options):
            events.append(self.service._request_events[self.data['requestId']])
            release.wait(1)
            options['check_cancelled']()
            return SimpleNamespace(text='late answer', usage={})

        self.client.translate.side_effect = generate
        try:
            with self.assertRaisesRegex(TextTranslationError, 'provider_timeout'):
                self.service.translate(self.data, self.memory)
            self.assertTrue(events[0].is_set())
            self.assertFalse(self.service._request_events)
        finally:
            release.set()
            self.service.executor.shutdown(wait=True)
        self.assertFalse(self.service.cache)

    def test_invalid_ids_and_non_codex_ids_are_rejected_without_provider_calls(self):
        with patch.object(server, 'TEXT_TRANSLATOR', self.service):
            client = server.create_app().test_client()
            for value in (None, '', 'short', 'a' * 129, 'x' * 16 + '/', ['bad']):
                response = client.post('/cancel-text', json={'requestId': value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json['code'], 'invalid_request_id')
                with self.assertRaisesRegex(TextTranslationError, 'invalid_request_id'):
                    self.service.translate({**self.data, 'requestId': value}, self.memory)
            for fields in ({'service': 'openai'}, {'selectionProvider': 'bing'}, {'allowGenerate': False}):
                with self.assertRaisesRegex(TextTranslationError, 'invalid_request_id'):
                    self.service.translate({**self.data, **fields}, self.memory)
        self.client.translate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
