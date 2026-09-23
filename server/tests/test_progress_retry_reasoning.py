import asyncio
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import openai

from babeldoc.progress_monitor import ProgressMonitor
from babeldoc.translator.request_budget import ParagraphRequestBudget, TranslationBudgetExhausted
from babeldoc.translator.request_budget import submit_translation
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
from babeldoc.format.pdf.document_il.midend.il_translator import PageTranslateTracker, PbarContext
from pdf2zh_next.translator.reasoning_mode import apply_reasoning_mode, reasoning_family
from observability import TaskMetricsCollector
import test_openai_protocol as protocol_tests
from test_openai_protocol import chat
import test_partial_batch as partial_tests
from test_partial_batch import MemoryCache, translated
from test_cache_namespace import settings, NoopRateLimiter
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
from test_pdf2zh_next_service import make_settings_payload
from pdf2zh_next_service import create_runtime_settings, validate_service_config


class ProgressTests(unittest.TestCase):
    def test_full_document_counts_only_eligible_references_once(self):
        events = []
        pm = ProgressMonitor([("Translate Paragraphs", 1)], progress_change_callback=lambda **e: events.append(e), report_interval=0)
        refs = [SimpleNamespace(debug_id=f"r{i}", unicode="Reference source", layout_label="reference_content") for i in range(95)]
        invalid = SimpleNamespace(debug_id=None, unicode="Reference source", layout_label="reference_content")
        body = [SimpleNamespace(debug_id=f"p{i}", unicode="Body text", layout_label="text") for i in range(332)]
        docs = SimpleNamespace(page=[SimpleNamespace(pdf_font=[], pdf_xobject=[], pdf_paragraph=body + refs + [invalid])])
        collector = TaskMetricsCollector(task_id="test", provider="test", model="test")
        translator = ILTranslatorLLMOnly.__new__(ILTranslatorLLMOnly)
        translator.il_translator = Mock()
        translator.translate_engine = SimpleNamespace(metrics_collector=collector)
        translator.translation_config = SimpleNamespace(
            raise_if_cancelled=lambda: None, skip_references=True, min_text_length=3, pool_max_workers=1,
            progress_monitor=pm, shared_context_cross_split_part=SimpleNamespace(first_paragraph=True, recent_title_paragraph=None),
            get_working_file_path=lambda name: name, save_detailed_tracking=False)
        translator.total_count = translator.ok_count = translator.fallback_count = 0
        translator.calc_token_count = len
        translator.process_cross_page_paragraph = lambda *args: None
        translator.process_cross_column_paragraph = lambda *args: None
        seen = []
        def translate(batch, stage, *args, **kwargs):
            for paragraph in batch.paragraphs:
                seen.append(id(paragraph))
                stage.advance(1)
        translator.translate_paragraph = translate
        with patch("babeldoc.format.pdf.document_il.midend.il_translator_llm_only.is_cid_paragraph", return_value=False), patch("babeldoc.format.pdf.document_il.midend.il_translator_llm_only.is_placeholder_only_paragraph", return_value=False):
            translator.translate(docs)
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(set(seen), {id(p) for p in body})
        self.assertEqual(events[-1]["stage_current"], 427)
        self.assertEqual(events[-1]["stage_total"], 427)
        self.assertTrue(any(e.get("stage_current") == 416 for e in events))
        self.assertEqual(collector.snapshot()["referencesSkipped"], 95)
        self.assertFalse(translator._should_translate_paragraph(refs[0], set(), True))

    def test_reference_paragraphs_never_reenter_page_batches(self):
        events = []
        pm = ProgressMonitor([("translate", 1)], progress_change_callback=lambda **e: events.append(e), report_interval=0)
        refs = [SimpleNamespace(debug_id=f"r{i}", unicode="Reference source", layout_label="text") for i in range(95)]
        body = [SimpleNamespace(debug_id=f"p{i}", unicode="Body text", layout_label="text") for i in range(332)]
        page = SimpleNamespace(pdf_font=[], pdf_xobject=[], pdf_paragraph=body + refs)
        translator = ILTranslatorLLMOnly.__new__(ILTranslatorLLMOnly)
        translator.reference_skip_ids = {id(p) for p in refs}
        translator.translation_config = SimpleNamespace(raise_if_cancelled=lambda: None, min_text_length=3,
            shared_context_cross_split_part=SimpleNamespace(first_paragraph=None, recent_title_paragraph=None))
        translator.calc_token_count = len
        translator.mid = 0
        submitted = []
        translator.submit_translation = lambda executor, fn, batch, *args, **kwargs: submitted.extend(batch.paragraphs)
        stage = pm.stage_start("translate", 427)
        stage.advance(95)
        with patch("babeldoc.format.pdf.document_il.midend.il_translator_llm_only.is_cid_paragraph", return_value=False), patch("babeldoc.format.pdf.document_il.midend.il_translator_llm_only.is_placeholder_only_paragraph", return_value=False):
            translator.process_page(page, Mock(), stage, PageTranslateTracker(), Mock(), set())
        self.assertEqual([id(p) for p in submitted], [id(p) for p in body])
        stage.advance(321)
        self.assertEqual(stage.current, 416)
        self.assertAlmostEqual(events[-1]["stage_progress"], 416 / 427 * 100)
        stage.advance(11)
        self.assertEqual(stage.current, 427)

    def test_overflow_is_logged_but_published_progress_is_bounded(self):
        events = []
        pm = ProgressMonitor([("translate", 1)], progress_change_callback=lambda **e: events.append(e), report_interval=0)
        stage = pm.stage_start("translate", 427)
        with self.assertLogs("babeldoc.progress_monitor", level="WARNING"):
            stage.advance(511)
        self.assertEqual(stage.current, 511)
        self.assertEqual(events[-1]["stage_progress"], 100)
        self.assertEqual(events[-1]["overall_progress"], 100)

    def test_error_and_cancellation_do_not_finish_stage(self):
        for error in (ValueError(), asyncio.CancelledError()):
            events = []
            pm = ProgressMonitor([("translate", 1)], progress_change_callback=lambda **e: events.append(e), report_interval=0)
            with self.assertRaises(type(error)):
                with pm.stage_start("translate", 10) as stage:
                    stage.advance(2)
                    raise error
            self.assertEqual(stage.current, 2)
            self.assertNotIn("progress_end", [e["type"] for e in events])
        progress = Mock()
        with self.assertRaises(asyncio.CancelledError):
            with PbarContext(progress):
                raise asyncio.CancelledError()
        progress.advance.assert_not_called()


class BudgetTests(unittest.TestCase):
    def test_atomic_shared_budget_and_backoff_survive_fallback(self):
        owner = ParagraphRequestBudget()
        paragraphs = [object(), object()]
        batch = owner.request(paragraphs)
        batch.reserve()
        batch.reserve_wait(20)
        single = owner.request([paragraphs[0]])
        self.assertFalse(single.available(11))
        single.reserve_wait(10)
        single.reserve()
        single.reserve()
        with self.assertRaises(TranslationBudgetExhausted):
            owner.request([paragraphs[0]]).reserve()
        other = owner.request([paragraphs[1]])
        with ThreadPoolExecutor(8) as pool:
            futures = [pool.submit(owner.request([paragraphs[1]]).reserve) for _ in range(8)]
        self.assertEqual(sum(f.exception() is None for f in futures), 2)
        self.assertFalse(other.available())

    def test_timeout_batch_splits_once_and_singles_share_remaining_budget(self):
        self.run_batch_failure("timeout", expected_requests=7, failed=0)

    def test_invalid_batch_splits_without_repeating_batch(self):
        self.run_batch_failure("invalid", expected_requests=7, failed=0)

    def test_provider_failure_never_multiplies_into_single_requests(self):
        self.run_batch_failure("server", expected_requests=2, failed=6)
        self.run_batch_failure("auth", expected_requests=1, failed=6)
        self.run_batch_failure("rate", expected_requests=1, failed=6)

    def test_invalid_single_retries_cannot_reset_the_paragraph_allowance(self):
        self.run_batch_failure("all_invalid", expected_requests=13, failed=6)

    def run_batch_failure(self, kind, expected_requests, failed):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            paragraphs, recovery, _, batch, run = partial_tests.PartialBatchTests().fixture(stack, directory, lambda text: text)
            helper = protocol_tests.ProtocolTests()
            stack.callback(helper.doCleanups)
            def respond(request):
                text = json.loads(request.content)["messages"][0]["content"]
                if text.startswith("["):
                    if kind == "timeout":
                        raise httpx.ReadTimeout("timeout", request=request)
                    if kind in {"invalid", "all_invalid"}:
                        return httpx.Response(200, json=chat("not json"))
                    code = {"server": 503, "auth": 401, "rate": 429}[kind]
                    return httpx.Response(code, headers={"retry-after": "31"} if kind == "rate" else {}, json={"error": {"message": "failure"}})
                return httpx.Response(200, json=chat(text if kind == "all_invalid" else translated(text)))
            engine = helper.translator(respond, protocol="chat_completions")
            engine.cache = MemoryCache()
            engine._wait_cancel = lambda seconds: None
            engine.metrics_collector = TaskMetricsCollector(task_id="test", provider="test", model="test")
            batch.translate_engine = batch.il_translator.translate_engine = engine
            run()
            self.assertEqual(len(helper.requests), expected_requests)
            self.assertEqual(recovery.snapshot()[0]["failed"], failed)
            self.assertEqual(engine.metrics_collector.snapshot()["activity"]["fallbackPending"], 0)
            self.assertTrue(all(count <= 3 for count in batch.il_translator._request_budget.attempts.values()))

    def test_single_timeout_only_attempts_twice_and_cancel_interrupts_wait(self):
        helper = protocol_tests.ProtocolTests()
        self.addCleanup(helper.doCleanups)
        def respond(request):
            raise httpx.ReadTimeout("timeout", request=request)
        engine = helper.translator(respond)
        engine._wait_cancel = lambda seconds: None
        budget = ParagraphRequestBudget().request([object()])
        with self.assertRaises(openai.APITimeoutError):
            engine.llm_translate("input", True, {"batch_size": 1, "request_budget": budget})
        self.assertEqual(len(helper.requests), 2)
        engine.check_cancelled = Mock(side_effect=asyncio.CancelledError)
        from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
        with self.assertRaises(asyncio.CancelledError):
            OpenAITranslator._wait_cancel(engine, 30)


class ReasoningTests(unittest.TestCase):
    def test_effective_options_isolate_translation_and_review_caches(self):
        engines = []
        for mode in (None, "default", "off"):
            config = settings(model="deepseek-v4-flash")
            if mode is not None:
                config.translate_engine_settings.openai_reasoning_mode = mode
            with patch("pdf2zh_next.translator.translator_impl.openai.openai.OpenAI"):
                engines.append(OpenAITranslator(config, NoopRateLimiter()))
        self.assertEqual(engines[0].cache.translate_engine_params, engines[1].cache.translate_engine_params)
        self.assertNotEqual(engines[1].cache.translate_engine_params, engines[2].cache.translate_engine_params)
        key = {"source": "isolated source", "output": "isolated output"}
        engines[1]._set_review_cache(key, "approved")
        self.assertEqual(engines[0]._get_review_cache(key), "approved")
        self.assertIsNone(engines[2]._get_review_cache(key))

    def test_runtime_passes_mode_and_live_probe_reports_observed_reasoning(self):
        payload = make_settings_payload(service="openaicompatible", llm_api={
            "apiKey": "test", "apiUrl": "https://relay.invalid/v1", "model": "deepseek-v4-flash", "reasoningMode": "off"})
        self.assertEqual(create_runtime_settings(payload).translate_engine_settings.openai_reasoning_mode, "off")
        for tokens, expected in ((2, "可能未生效"), (None, "无法确认"), (0, "为 0")):
            translator = SimpleNamespace(model="deepseek-v4-flash", reasoning_mode="off", resolved_protocol="chat_completions",
                _pending_initialization_metrics=[{"usage": {"reasoning_tokens": tokens}}])
            with patch("pdf2zh_next_service.get_translator", return_value=translator):
                result = validate_service_config({**payload, "live_test": True}, "test")
            self.assertIn(expected, result.live_test["reasoningMessage"])

    def test_known_models_and_protocols(self):
        for model in ("gpt-5.1", "gpt-5.2", "gpt-5.4", "gpt-5.5", "gpt-5.2-2025-12-11"):
            self.assertEqual(apply_reasoning_mode({}, "off", model, "chat_completions"), {"reasoning_effort": "none"})
            self.assertEqual(apply_reasoning_mode({}, "off", model, "responses"), {"reasoning": {"effort": "none"}})
        self.assertEqual(apply_reasoning_mode({}, "off", "deepseek-v4-flash", "chat_completions"), {"thinking": {"type": "disabled"}})
        for model in ("gpt-5", "gpt-5.2-pro", "gpt-5.4-codex", "gpt-6-astra", "custom"):
            self.assertIsNone(reasoning_family(model))
            with self.assertRaises(ValueError):
                apply_reasoning_mode({}, "off", model, "responses")

    def test_default_preserves_options_and_conflicts_are_rejected(self):
        for mode in (None, [], {}, "unsupported"):
            with self.assertRaises(ValueError):
                apply_reasoning_mode({}, mode, "gpt-5.4", "responses")
        options = {"reasoning_effort": "high"}
        self.assertIs(apply_reasoning_mode(options, "default", "custom", "responses"), options)
        for options in ({"reasoning_effort": "high"}, {"thinking": {"type": "enabled"}}, {"reasoning": {"effort": "low"}}):
            with self.assertRaisesRegex(ValueError, "冲突"):
                apply_reasoning_mode(options, "off", "gpt-5.4", "responses")


class ActivityTests(unittest.TestCase):
    def test_cancelled_queued_fallback_releases_activity_counts(self):
        collector = TaskMetricsCollector(task_id="t", provider="p", model="m")
        gate = threading.Event()
        with ThreadPoolExecutor(1) as executor:
            blocker = executor.submit(gate.wait, 2)
            future = submit_translation(executor, lambda: None, collector=collector, fallback=True)
            self.assertTrue(future.cancel())
            gate.set()
            blocker.result()
        self.assertEqual(collector.snapshot()["activity"]["queued"], 0)
        self.assertEqual(collector.snapshot()["activity"]["fallbackPending"], 0)

    def test_heartbeat_ages_active_requests_without_advancing_progress(self):
        now = [0.0]
        collector = TaskMetricsCollector(task_id="t", provider="p", model="m", clock=lambda: now[0])
        started = collector.request_started()
        collector.activity_changed("queued", 2)
        collector.update_progress({"type": "progress_update", "stage": "Translate Paragraphs", "stage_current": 0, "overall_progress": 0})
        now[0] = 10
        collector.update_progress({"type": "progress_update", "stage": "Translate Paragraphs", "stage_current": 1, "overall_progress": 10})
        now[0] = 45
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["activity"]["oldestRequestSeconds"], 45)
        self.assertEqual(snapshot["activity"]["lastParagraphCompletedAgoSeconds"], 35)
        self.assertEqual(snapshot["activity"]["queued"], 2)
        self.assertIsNone(snapshot["throughput"]["etaSeconds"])
        collector.request_finished(started, succeeded=True)
        self.assertIsNone(collector.snapshot()["activity"]["oldestRequestSeconds"])
