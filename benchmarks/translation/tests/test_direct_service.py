import asyncio
import tempfile
import unittest
from unittest.mock import Mock, patch

from benchmarks.translation.direct_service import DirectClient, install
from benchmarks.translation.common import rows


class DirectServiceTests(unittest.TestCase):
    def test_selection_cancel_preserves_product_terminal_error(self):
        from text_translation import TextTranslationError
        with tempfile.TemporaryDirectory() as directory:
            client = DirectClient('/unused', directory)
            client.ready.add('test')
            state = {'cancelled': False}
            def check():
                if state['cancelled']:
                    raise TextTranslationError('selection_cancelled', 409)
            def result(*args, **kwargs):
                state['cancelled'] = True
                return {'status':'cancelled','translation':'部分',
                    'usage':dict.fromkeys(('input','output','cached_input','reasoning'))}
            provider = Mock()
            provider.generate.side_effect = result
            with patch.object(client, 'provider', return_value=provider), self.assertRaises(TextTranslationError) as caught:
                client.translate('source', model='test', reasoning_effort='low', timeout=10, check_cancelled=check)
            self.assertEqual(caught.exception.code, 'selection_cancelled')

    def test_entry_refuses_normal_server_environment(self):
        with patch.dict('os.environ', {}, clear=True), self.assertRaises(RuntimeError):
            install()

    def test_product_and_transport_share_call_id_and_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            client = DirectClient('/unused', directory)
            client.ready.add('test')
            provider, used = Mock(), {}
            provider.generate.return_value = {'status': 'ok', 'translation': '译文',
                'usage': {'input': 100, 'output': 10, 'cached_input': 40, 'reasoning': 0, 'availability': 'complete'}}
            with patch.object(client, 'provider', return_value=provider):
                result = client.translate('source', model='test', reasoning_effort='low', timeout=10,
                                          call_id='product-id', on_usage=used.update)
            from pathlib import Path
            ledger = rows(next(Path(directory).glob('*.jsonl')))
            self.assertEqual([r['call_id'] for r in ledger], ['product-id', 'product-id'])
            self.assertEqual(used, result.usage)
            self.assertEqual(used['prompt_tokens'], 100)
            provider.close.assert_called_once()

    def test_cancelled_transport_is_not_returned_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            client = DirectClient('/unused', directory)
            client.ready.add('test')
            provider = Mock()
            provider.generate.return_value = {'status': 'cancelled', 'translation': '部分',
                'usage': {'input': None, 'output': None, 'cached_input': None, 'reasoning': None}}
            with patch.object(client, 'provider', return_value=provider), self.assertRaises(asyncio.CancelledError):
                client.translate('source', model='test', reasoning_effort='low', timeout=10)
