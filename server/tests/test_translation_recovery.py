import json
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import openai
from tenacity import wait_none

from babeldoc.format.pdf.document_il import Document, Page, PdfParagraph, PdfStyle, PdfParagraphComposition, PdfCharacter
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, ParagraphTranslateTracker
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly, BatchParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import PageTranslateTracker
from babeldoc.format.pdf.translation_recovery import TranslationRecovery, safe_error
from babeldoc.translator.validation import InvalidTranslation, validate_batch, validate_text
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator, _retry_wait
from pdf2zh_next_service import TranslationResult, TranslationOutputFile
from task_manager import TaskManager, TaskRecord
import test_openai_protocol as protocol_fixtures
chat = protocol_fixtures.chat


SOURCE = "This is a complete English paragraph about the experiment."
TARGET = "这是一个关于实验的完整中文段落。"


class ValidationTests(unittest.TestCase):
    def test_bad_batch_shapes_and_tokens(self):
        cases = ['[{"id":0,"output":"译文",}]', '[]',
                 '[{"id":0,"output":"译文"},{"id":0,"output":"译文"}]',
                 '[{"id":-1,"output":"译文"}]', '[{"id":true,"output":"译文"}]',
                 '[{"id":0,"input":"译文"}]', '[{"id":0,"output":""}]']
        for value in cases:
            with self.subTest(value=value), self.assertRaises(InvalidTranslation):
                validate_batch(value, [SOURCE], "zh-CN")
        with self.assertRaises(InvalidTranslation):
            validate_text("Result {v1}", "结果 {v2}", "zh-CN")
        with self.assertRaises(InvalidTranslation):
            validate_text(SOURCE, SOURCE, "zh-CN")
        validate_text("GazeToHand", "GazeToHand", "zh-CN")
        validate_text("Y. Liu and T. Mikkelsen", "Y. Liu and T. Mikkelsen", "zh-CN")
        self.assertEqual(validate_batch(json.dumps([{"id":0,"output":TARGET}]), [SOURCE], "zh-CN"), {0: TARGET})


class RetryAndCacheTests(unittest.TestCase):
    translator = protocol_fixtures.ProtocolTests.translator
    def test_five_attempts_all_limited(self):
        translator = self.translator(lambda r: httpx.Response(503, json={"error":"temporary"}), protocol="chat_completions")
        translator.rate_limiter = Mock()
        call = OpenAITranslator.do_translate.retry_with(wait=wait_none())
        with self.assertRaises(openai.InternalServerError):
            call(translator, SOURCE)
        self.assertEqual(len(self.requests), 5)
        self.assertEqual(translator.rate_limiter.wait.call_count, 5)

    def test_success_after_four_errors(self):
        count = []
        def handler(r):
            count.append(r)
            return httpx.Response(503, json={"error":"temporary"}) if len(count) < 5 else httpx.Response(200, json=chat(TARGET))
        translator = self.translator(handler, protocol="chat_completions")
        self.assertEqual(OpenAITranslator.do_translate.retry_with(wait=wait_none())(translator, SOURCE), TARGET)
        self.assertEqual(len(count), 5)

    def test_404_verification_and_permanent_errors(self):
        for message, verified, expected in [("temporary", True, 5), ("temporary", False, 1), ("model_not_found", True, 1), ("route not found", True, 1)]:
            t = self.translator(lambda r: httpx.Response(404, json={"error":message}), protocol="chat_completions")
            if verified:
                t._verified_protocols.add("chat_completions")
            with self.assertRaises(openai.NotFoundError):
                OpenAITranslator.do_translate.retry_with(wait=wait_none())(t, SOURCE)
            self.assertEqual(len(self.requests), expected)

    def test_retry_after_and_cancellation(self):
        error = openai.RateLimitError("busy", response=httpx.Response(429, headers={"retry-after":"60"}, request=httpx.Request("POST", "https://test.invalid")), body=None)
        state = SimpleNamespace(attempt_number=1, outcome=SimpleNamespace(exception=lambda: error))
        self.assertGreaterEqual(_retry_wait(state), 60)
        t = self.translator(lambda r: httpx.Response(503, json={"error":"temporary"}), protocol="chat_completions")
        checks = [0]
        def cancel():
            checks[0] += 1
            if checks[0] >= 3:
                raise InterruptedError("cancelled")
        t.check_cancelled = cancel
        with self.assertRaises(InterruptedError):
            t.do_translate(SOURCE)
        self.assertEqual(len(self.requests), 1)

    def test_bad_cache_is_evicted_then_only_valid_result_saved(self):
        t = self.translator(lambda r: httpx.Response(200, json=chat(TARGET)), protocol="chat_completions")
        t.cache = Mock()
        t.cache.get.return_value = SOURCE
        params = {"validate_output": lambda value: validate_text(SOURCE, value, "zh-CN")}
        self.assertEqual(t.llm_translate(SOURCE, rate_limit_params=params), TARGET)
        t.cache.delete.assert_called_once_with(SOURCE)
        t.cache.set.assert_called_once_with(SOURCE, TARGET)
        t.cache.get.return_value = TARGET
        t.llm_translate(SOURCE, rate_limit_params=params)
        self.assertEqual(len(self.requests), 1)

    def test_invalid_generation_retried_once_never_cached(self):
        t = self.translator(lambda r: httpx.Response(200, json=chat(SOURCE)), protocol="chat_completions")
        t.cache = Mock(); t.cache.get.return_value = None
        with self.assertRaises(InvalidTranslation):
            t.llm_translate(SOURCE, rate_limit_params={"validate_output": lambda value: validate_text(SOURCE, value, "zh-CN")})
        self.assertEqual(len(self.requests), 2)
        t.cache.set.assert_not_called()

    def test_shared_request_concurrency(self):
        lock = threading.Lock(); active = [0, 0]
        def handler(r):
            with lock:
                active[0] += 1; active[1] = max(active)
            threading.Event().wait(0.02)
            with lock:
                active[0] -= 1
            return httpx.Response(200, json=chat(TARGET))
        t = self.translator(handler, protocol="chat_completions")
        t.configure_execution(2, lambda: None)
        with ThreadPoolExecutor(8) as pool:
            list(pool.map(lambda _: t.do_translate(SOURCE), range(8)))
        self.assertEqual(active[1], 2)


class RecoveryTests(unittest.TestCase):
    @contextmanager
    def recovery_directory(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            self.recovery_stack = stack
            yield directory

    def fixtures(self, directory):
        p = PdfParagraph(debug_id="one", unicode=SOURCE, pdf_style=PdfStyle(), layout_label="text", pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=PdfCharacter(char_unicode="T"))])
        docs = Document(page=[Page(page_number=0, pdf_paragraph=[p])])
        cfg = SimpleNamespace(skip_references=False, min_text_length=3, disable_same_text_fallback=False,
                              raise_if_cancelled=lambda: None, add_formula_placehold_hint=False)
        recovery = TranslationRecovery(Path(directory)/"recovery.sqlite3", "source-and-config")
        self.recovery_stack.callback(recovery.close)
        cfg.recovery = recovery
        recovery.register(docs, cfg)
        translator = ILTranslator.__new__(ILTranslator)
        translator.translation_config = cfg
        translator.translate_engine = SimpleNamespace(lang_out="zh-CN", llm_translate=Mock(return_value=TARGET))
        translator.use_as_fallback = False
        translator.support_llm_translate = True
        translator._prepare_paragraph = lambda p, *args: (p.unicode, ILTranslator.TranslateInput(p.unicode, [], p.pdf_style))
        translator.generate_prompt_for_llm = lambda text, *args: text
        return p, docs, cfg, recovery, translator

    def test_failure_is_recorded_success_restores_without_api(self):
        with self.recovery_directory() as tmp:
            p, docs, cfg, recovery, translator = self.fixtures(tmp)
            translator.translate_engine.llm_translate.side_effect = RuntimeError("secret should not be persisted")
            translator.translate_paragraph(p, docs.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            recovery.finish()
            self.assertEqual(recovery.snapshot()[0]["failed"], 1)
            self.assertEqual(p.unicode, SOURCE)
            self.assertNotIn("secret should", json.dumps(recovery.entries))
            translator.translate_engine.llm_translate.side_effect = None
            translator.translate_paragraph(p, docs.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            recovery.finish()
            self.assertEqual(recovery.snapshot()[0]["succeeded"], 1)
            p2, docs2, cfg2, recovered, t2 = self.fixtures(tmp)
            t2.translate_paragraph(p2, docs2.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            recovered.finish()
            t2.translate_engine.llm_translate.assert_not_called()
            self.assertEqual(p2.unicode, TARGET)
            self.assertEqual(recovered.snapshot()[0]["failed"], 0)
            other = TranslationRecovery(recovered.path, "different-config")
            self.recovery_stack.callback(other.close)
            self.assertEqual(other.entries, {})

    def test_batch_fallback_keeps_source_string_and_recovers(self):
        with self.recovery_directory() as tmp:
            p, docs, cfg, recovery, single = self.fixtures(tmp)
            batch = ILTranslatorLLMOnly.__new__(ILTranslatorLLMOnly)
            batch.translation_config = cfg; batch.il_translator = single
            batch.translate_engine = SimpleNamespace(lang_out="zh-CN", llm_translate=Mock(side_effect=InvalidTranslation("bad_json")))
            batch._build_llm_prompt = lambda **kwargs: kwargs["json_input_str"]
            batch.calc_token_count = lambda text: len(text)
            batch.total_count = batch.fallback_count = batch.ok_count = 0
            def submit(fn, *args, **kwargs):
                self.assertIsInstance(args[0].unicode, str)
                kwargs.pop("priority", None)
                return fn(*args, **kwargs)
            batch.translate_paragraph(BatchParagraph([p], docs.page, PageTranslateTracker()), Mock(), {}, {}, executor=SimpleNamespace(submit=submit))
            recovery.finish()
            self.assertEqual(recovery.snapshot()[0]["succeeded"], 1)
            self.assertEqual(recovery.snapshot()[0]["failed"], 0)

    def test_unprocessed_paragraph_is_not_silent_success(self):
        with self.recovery_directory() as tmp:
            _, _, _, recovery, _ = self.fixtures(tmp)
            recovery.finish()
            self.assertEqual(recovery.snapshot()[0]["failed"], 1)

    def test_repair_only_requests_the_failed_paragraph(self):
        with self.recovery_directory() as tmp:
            def pair():
                p, docs, cfg, recovery, translator = self.fixtures(tmp)
                second = PdfParagraph(debug_id="two", unicode=SOURCE + " A second sentence.",
                    pdf_style=PdfStyle(), pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=PdfCharacter(char_unicode="T"))])
                docs.page[0].pdf_paragraph.append(second)
                recovery.register(docs, cfg)
                return p, second, docs, recovery, translator
            first, second, docs, recovery, translator = pair()
            translator.translate_paragraph(first, docs.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            recovery.fail(second, RuntimeError("temporary"))
            recovery.finish()
            first, second, docs, recovered, translator = pair()
            for paragraph in [first, second]:
                translator.translate_paragraph(paragraph, docs.page[0], Mock(), ParagraphTranslateTracker(), {}, {})
            recovered.finish()
            translator.translate_engine.llm_translate.assert_called_once()
            self.assertIn("second sentence", translator.translate_engine.llm_translate.call_args.args[0])
            self.assertEqual(recovered.snapshot()[0]["succeeded"], 2)

    def test_incomplete_repair_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/"paper.pdf"; source.write_bytes(b"pdf")
            old = root/"old.pdf"; old.write_bytes(b"old")
            manager = TaskManager(root/"tasks.json")
            manager._tasks["task"] = TaskRecord("task", "paper.pdf", "openai", ["dual"],
                {"input_path":str(source), "output_dir":str(root/"output")}, root, status="completed")
            result = TranslationResult({"dual":TranslationOutputFile("dual", old, old.name)},
                {"total":2,"succeeded":1,"failed":1,"skipped":0,"pending":0}, [{"page":1,"paragraphId":"one"}])
            async def translate(*args, **kwargs): return result
            with patch("task_manager.translate_pdf_with_callbacks", side_effect=translate):
                manager._run_task("task")
            self.assertEqual(manager.get_task("task")["status"], "incomplete")
            self.assertLess(manager.get_task("task")["overallProgress"], 100)
            self.assertIsNone(manager.get_result_file("task")[1])
            restored = TaskManager(root/"tasks.json")
            self.assertEqual(restored.get_task("task")["failedParagraphs"], result.failed_paragraphs)
            with patch("task_manager.threading.Thread"):
                snap = restored.repair_task("task")
            self.assertEqual(snap["status"], "queued")
            self.assertTrue(old.exists())
            self.assertEqual(restored._tasks["task"].request_payload["qps"], 2)
            self.assertEqual(restored._tasks["task"].request_payload["pool_size"], 4)
            self.assertEqual(TaskManager(root/"tasks.json").get_task("task")["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
