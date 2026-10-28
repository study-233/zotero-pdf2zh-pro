"""Core model use must not require the PDF engine or mutate historical identities."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pdf2zh_pro.config import create_model_settings


class CoreBoundaryTests(unittest.TestCase):
    def test_constructor_does_not_send_a_health_probe(self):
        from pdf2zh_pro.translator import create_translator
        from pdf2zh_pro.translator.openai import OpenAITranslator
        settings = create_model_settings({'service':'openai','llm_api':{'apiKey':'test-key'}})
        with patch.object(OpenAITranslator, 'health_check') as probe:
            translator = create_translator(settings)
            try:
                probe.assert_not_called()
            finally:
                translator.client.close()

    def test_selection_and_model_configuration_do_not_import_pdf_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            code = '''
import sys
import pdf2zh_pro
from pdf2zh_pro.config import create_model_settings
import text_translation
settings = create_model_settings({'service':'openai','llm_api':{'apiKey':'test-key'}})
assert settings.translate_engine_settings.openai_api_key == 'test-key'
assert 'pdf2zh_pro.pdf' not in sys.modules
assert not any(name == 'babeldoc' or name.startswith('babeldoc.') for name in sys.modules)
assert not any(name == 'pdf2zh_next' or name.startswith('pdf2zh_next.') for name in sys.modules)
'''
            result = subprocess.run([sys.executable, '-c', code],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                env={**os.environ, 'PDF2ZH_TRANSLATION_CACHE_DIR': directory}, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_siliconflow_terms_share_normalized_main_protocol_and_options(self):
        settings = create_model_settings({'service':'siliconflow', 'llm_api':{
            'apiKey':'test-key','apiUrl':'https://relay.invalid/v1','apiProtocol':'responses',
            'requestOptions':{'max_output_tokens':200},'reasoningMode':'off',
        }})
        self.assertIs(settings.term_extraction_engine_settings, settings.translate_engine_settings)
        engine = settings.translate_engine_settings
        self.assertEqual(engine.openai_api_protocol, 'responses')
        self.assertEqual(engine.openai_reasoning_mode, 'off')
        self.assertEqual(json.loads(engine.openai_request_options)['max_output_tokens'], 200)
