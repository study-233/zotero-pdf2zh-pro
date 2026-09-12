import asyncio
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_partial_batch as partial
import test_quality_review as review_fixtures
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator


CLOCK = "babeldoc.format.pdf.document_il.midend.il_translator.perf_counter"


class EngineMetricsTests(unittest.TestCase):
    def test_batch_and_single_repair_report_actual_unit_counts(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            _, _, engine, _, run = partial.PartialBatchTests().fixture(
                stack, directory, partial.PartialBatchTests.partial_response,
            )
            params_seen = []
            original_request = engine.do_llm_translate
            def request(text, rate_limit_params=None):
                params_seen.append(dict(rate_limit_params or {}))
                return original_request(text, rate_limit_params)
            engine.do_llm_translate = request
            run()
            self.assertEqual([params["metric_kind"] for params in params_seen], ["translation", "translation"])
            self.assertEqual([params["batch_size"] for params in params_seen], [6, 1])
            self.assertEqual(params_seen[0]["paragraph_token_count"], 0)

    def test_review_requests_count_only_submitted_units_and_record_elapsed_stage(self):
        engine = review_fixtures.ReviewTranslator(review_fixtures.passed)
        engine.metrics_collector = Mock()
        translator = ILTranslator.__new__(ILTranslator)
        translator.translate_engine = engine
        translator.translation_config = SimpleNamespace(semantic_review=True, raise_if_cancelled=lambda: None)
        translator.begin_translation_completion()
        for index in range(5):
            translator.quality_review.register_candidate(
                object(), review_fixtures.SOURCE + f" Unit {index}.", review_fixtures.TARGET,
                Mock(), risk_hints=("continuation",), order_key=index,
            )
        params_seen = []
        original_request = engine.do_llm_translate
        def request(text, rate_limit_params=None):
            params_seen.append(dict(rate_limit_params or {}))
            return original_request(text, rate_limit_params)
        engine.do_llm_translate = request
        with patch(CLOCK, side_effect=[10.0, 12.5]):
            translator.finish_translation_completion()
        self.assertEqual([params["metric_kind"] for params in params_seen], ["review", "review"])
        self.assertEqual([params["batch_size"] for params in params_seen], [4, 1])
        engine.metrics_collector.record_stage.assert_called_once_with("Quality Review", 2.5)

    def test_review_timing_covers_failure_and_cancellation_without_double_counting(self):
        for error in (None, RuntimeError("review failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                translator = ILTranslator.__new__(ILTranslator)
                collector = Mock()
                translator.translate_engine = SimpleNamespace(metrics_collector=collector)
                translator.translation_config = SimpleNamespace(raise_if_cancelled=lambda: None)
                translator.begin_translation_completion()
                translator.quality_review = SimpleNamespace(run=Mock(side_effect=error))
                with patch(CLOCK, side_effect=[100.0, 103.0]):
                    if error is None:
                        translator.finish_translation_completion()
                        translator.finish_translation_completion()
                    else:
                        with self.assertRaises(type(error)):
                            translator.finish_translation_completion()
                collector.record_stage.assert_called_once_with("Quality Review", 3.0)

    def test_timing_sink_failure_does_not_replace_review_cancellation(self):
        translator = ILTranslator.__new__(ILTranslator)
        collector = Mock()
        collector.record_stage.side_effect = RuntimeError("metrics sink unavailable")
        translator.translate_engine = SimpleNamespace(metrics_collector=collector)
        translator.translation_config = SimpleNamespace(raise_if_cancelled=lambda: None)
        translator.begin_translation_completion()
        translator.quality_review = SimpleNamespace(run=Mock(side_effect=asyncio.CancelledError))
        with patch(CLOCK, side_effect=[1.0, 2.0]), self.assertRaises(asyncio.CancelledError):
            translator.finish_translation_completion()

    def test_disabled_review_does_not_add_review_stage(self):
        translator = ILTranslator.__new__(ILTranslator)
        collector = Mock()
        translator.translate_engine = SimpleNamespace(metrics_collector=collector)
        translator.translation_config = SimpleNamespace(raise_if_cancelled=lambda: None)
        translator.finish_translation_completion()
        collector.record_stage.assert_not_called()
