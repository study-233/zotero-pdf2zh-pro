import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import server
from text_translation import TextTranslationService, TextTranslationError, SelectionCancellation
from translation_memory import TranslationMemory
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator


def frames(stream):
    return [(s.split('\n')[0][7:], json.loads(s.split('data: ', 1)[1])) for s in stream if not s.startswith(':')]


class SelectionStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = TextTranslationService(timeout=1)
        self.addCleanup(self.service.executor.shutdown)
        self.memory = TranslationMemory(Path(self.temp.name) / 'translation-memory.sqlite3')
        self.data = dict(text='A selected sentence.', mode='translate', selectionProvider='profile', service='openai', requestId='stream-request-0001')

    def test_incremental_output_then_final_and_cached_done_only(self):
        def generate(*args, cancel_event):
            cancel_event.emit('start', {'model': 'test', 'provider': 'openai'})
            cancel_event.emit('delta', {'text': '学'})
            cancel_event.emit('delta', {'text': '习'})
            return {'translation': '学习', 'model': 'test', 'provider': 'openai'}
        with patch.object(self.service, '_generate', side_effect=generate) as upstream:
            result = frames(self.service.stream(self.data, self.memory))
            self.assertEqual([k for k, _ in result], ['start', 'delta', 'delta', 'done'])
            self.assertEqual([v['seq'] for _, v in result], [1, 2, 3, 4])
            self.assertTrue(all(v['requestId'] == self.data['requestId'] for _, v in result))
            self.assertTrue(result[-1][1]['saved'])
            cached = frames(self.service.stream({**self.data, 'requestId': 'stream-request-0002'}, self.memory))
            self.assertEqual([k for k, _ in cached], ['start', 'done'])
            self.assertTrue(cached[-1][1]['cached'])
            upstream.assert_called_once()

    def test_partial_failure_is_not_cached_and_requires_explicit_new_request(self):
        def generate(*args, cancel_event):
            cancel_event.emit('delta', {'text': 'partial'})
            raise TextTranslationError('incomplete_output', 502)
        with patch.object(self.service, '_generate', side_effect=generate) as upstream:
            result = frames(self.service.stream(self.data, self.memory))
            self.assertEqual([k for k, _ in result], ['start', 'delta', 'error'])
            self.assertFalse(self.service.cache)
            upstream.assert_called_once()

    def test_early_cancel_and_disconnect_do_not_save(self):
        self.service.cancel(self.data['requestId'])
        with patch.object(self.service, '_generate') as upstream:
            result = frames(self.service.stream(self.data, self.memory))
            self.assertEqual(result[-1][1]['code'], 'selection_cancelled')
            upstream.assert_not_called()
        cancelled = threading.Event()
        def generate(*args, cancel_event):
            cancel_event.emit('delta', {'text': 'partial'})
            cancel_event.wait(2)
            cancelled.set()
            self.service._check_request(cancel_event)
        with patch.object(self.service, '_generate', side_effect=generate):
            stream = self.service.stream({**self.data, 'requestId': 'stream-request-0003'}, self.memory)
            next(stream)
            stream.close()
            self.assertTrue(cancelled.wait(2))
            self.assertFalse(self.service.cache)

    def test_flask_negotiates_sse_and_rejects_structured_stream(self):
        with patch.object(server, 'TEXT_TRANSLATOR', self.service), patch.object(server, 'TRANSLATES_DIR', Path(self.temp.name)), patch.object(self.service, '_generate', return_value={'translation': 'result'}):
            client = server.create_app().test_client()
            self.assertTrue(client.post('/selection-capabilities').json['selectionStream'])
            response = client.post('/translate-text/stream', json=self.data)
            self.assertEqual(response.mimetype, 'text/event-stream')
            self.assertIn('event: done', response.text)
            self.assertEqual(client.post('/translate-text/stream', json={**self.data, 'mode': 'dictionary'}).status_code, 400)


class FakeStream:
    def __init__(self, events): self.events, self.closed = events, False
    def __iter__(self): return iter(self.events)
    def close(self): self.closed = True


class OpenAISelectionStreamTests(unittest.TestCase):
    def translator(self, protocol, events):
        stream = FakeStream(events)
        translator = OpenAITranslator.__new__(OpenAITranslator)
        translator.resolved_protocol = protocol
        translator.model = 'test'
        translator._options = lambda _: {'temperature': 0}
        translator.check_cancelled = lambda: None
        translator._record_usage = Mock()
        translator.client = NS(responses=NS(create=Mock(return_value=stream)), chat=NS(completions=NS(create=Mock(return_value=stream))))
        return translator, stream

    def test_chat_filters_reasoning_and_rejects_missing_finish_or_length(self):
        def event(text=None, finish=None): return NS(choices=[NS(index=0, delta=NS(content=text, reasoning_content='secret'), finish_reason=finish)])
        translator, stream = self.translator('chat_completions', [event('<thi'), event('nk>secret</thi'), event('nk>学'), event('习'), event(finish='stop')])
        deltas = []
        self.assertEqual(translator.selection_stream('prompt', deltas.append, SelectionCancellation()), '学习')
        self.assertEqual(''.join(deltas), '学习')
        self.assertTrue(stream.closed)
        for events in ([event('partial')], [event('partial'), event(finish='length')]):
            translator, stream = self.translator('chat_completions', events)
            with self.assertRaisesRegex(TextTranslationError, 'incomplete_output'):
                translator.selection_stream('prompt', lambda _: None, SelectionCancellation())
            self.assertTrue(stream.closed)

    def test_responses_outputs_only_text_and_uses_authoritative_completion(self):
        response = NS(status='completed', output=[NS(type='message', role='assistant', content=[NS(type='output_text', text='最终译文')])])
        translator, stream = self.translator('responses', [NS(type='response.reasoning_summary_text.delta', delta='secret'), NS(type='response.output_text.delta', delta='译文'), NS(type='response.completed', response=response)])
        deltas = []
        self.assertEqual(translator.selection_stream('prompt', deltas.append, SelectionCancellation()), '最终译文')
        self.assertEqual(deltas, ['译文'])
        self.assertTrue(stream.closed)

    def test_cancellation_closes_an_established_stream(self):
        cancellation, closed = SelectionCancellation(), threading.Event()
        cancellation.close_with(closed.set)
        cancellation.set()
        self.assertTrue(closed.wait(1))
        # A cancel that arrives before the connection is established still closes it.
        closed.clear()
        cancellation.close_with(closed.set)
        self.assertTrue(closed.wait(1))
