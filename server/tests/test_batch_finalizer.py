import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, nullcontext
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import test_partial_batch as partial
from babeldoc.format.pdf.document_il import Document, Page
from babeldoc.format.pdf.document_il.midend.batch_finalizer import BatchFinalizer, ParagraphOutput
from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, PageTranslateTracker
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import BatchParagraph
from babeldoc.translator.validation import inspect_batch


class FinalizerTests(unittest.TestCase):
    def fixture(self):
        sources = ["An English scientific paragraph. {v1}", "Another scientific paragraph. {v2}"]
        ps = [object(), object()]
        engine = partial.FixtureTranslator(lambda _: self.fail("finalizing must never request translation"))
        params = {"validate_output": lambda value: inspect_batch(value, sources, "zh-CN"),
                  "cache_output_if": lambda value: not inspect_batch(value, sources, "zh-CN").invalid_outputs}
        finalizer = BatchFinalizer(engine, "batch", ps, sources, params)
        return engine, finalizer, ps, sources

    def test_real_future_is_nonblocking_and_seal_commits_once(self):
        engine, finalizer, _, sources = self.fixture()
        future = Future()
        finalizer.record(0, ParagraphOutput(sources[0], partial.translated(sources[0])))
        finalizer.add_future(1, future)
        finalizer.seal()
        self.assertFalse(finalizer.finalize(None, lambda: None))
        self.assertEqual(engine.cache.values, {})
        future.set_result(ParagraphOutput(sources[1], partial.translated(sources[1])))
        self.assertTrue(finalizer.finalize(None, lambda: None))
        self.assertFalse(finalizer.finalize(None, lambda: None))
        self.assertEqual(len(engine.cache.values), 1)
        with self.assertRaisesRegex(RuntimeError, "sealed"):
            finalizer.add_future(1, Future())

    def test_failed_cancelled_or_wrong_source_future_does_not_commit(self):
        for failure in ("exception", "cancelled", "wrong_source"):
            with self.subTest(failure=failure):
                engine, finalizer, _, sources = self.fixture()
                future = Future()
                finalizer.record(0, ParagraphOutput(sources[0], partial.translated(sources[0])))
                finalizer.add_future(1, future)
                finalizer.seal()
                if failure == "exception":
                    future.set_exception(ValueError("failed"))
                elif failure == "cancelled":
                    future.cancel()
                else:
                    future.set_result(ParagraphOutput("unrelated", "无关译文"))
                self.assertFalse(finalizer.finalize(None, lambda: None))
                self.assertEqual(engine.cache.values, {})

    def test_immutable_results_and_review_override_prevent_old_translation_commit(self):
        engine, finalizer, ps, sources = self.fixture()
        results = [ParagraphOutput(source, partial.translated(source)) for source in sources]
        with self.assertRaises(FrozenInstanceError):
            results[0].output = "changed"
        for i, result in enumerate(results):
            finalizer.record(i, result)
        finalizer.seal()
        corrected = ["已修正的完整译文。{v1}", "另一段的完整译文。{v2}"]
        review = SimpleNamespace(final_output=lambda p: corrected[ps.index(p)])
        self.assertTrue(finalizer.finalize(review, lambda: None))
        self.assertEqual([row["output"] for row in json.loads(engine.cache.values["batch"])], corrected)

    def test_confirmed_failure_evicts_prior_cache_and_cancellation_prevents_writes(self):
        engine, finalizer, _, sources = self.fixture()
        for i, source in enumerate(sources):
            finalizer.record(i, ParagraphOutput(source, partial.translated(source)))
        finalizer.seal()
        engine.cache.set("batch", "old draft")
        self.assertFalse(finalizer.finalize(SimpleNamespace(final_output=lambda p: None), lambda: None))
        self.assertNotIn("batch", engine.cache.values)
        def cancelled():
            raise asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):
            finalizer.finalize(None, cancelled)
        self.assertEqual(engine.cache.values, {})

    def test_ignore_cache_and_cache_failures_are_nonfatal(self):
        for ignore in (True, False):
            engine, finalizer, _, sources = self.fixture()
            engine.ignore_cache = ignore
            engine.cache.set = Mock(side_effect=OSError("cache unavailable"))
            for i, source in enumerate(sources):
                finalizer.record(i, ParagraphOutput(source, partial.translated(source)))
            finalizer.seal()
            self.assertFalse(finalizer.finalize(None, lambda: None))
            self.assertEqual(engine.cache.set.call_count, 0 if ignore else 1)


class FakeReview:
    def __init__(self, action=None):
        self.items = {}
        self.register_calls = 0
        self.run_calls = 0
        self.action = action

    def register_candidate(self, p, source, output, apply, **kwargs):
        self.register_calls += 1
        self.items[id(p)] = SimpleNamespace(paragraph=p, source=source, output=output, apply=apply, **kwargs)

    def run(self):
        self.run_calls += 1
        if self.action:
            self.action(self)
        return {}

    def final_output(self, p):
        return self.items[id(p)].output


class ReviewIntegrationTests(unittest.TestCase):
    def test_disabled_review_initial_fallback_cache_and_recovery_never_construct_reviewer(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            reviewer = stack.enter_context(patch(
                "babeldoc.format.pdf.quality_review.QualityReview",
                side_effect=AssertionError("disabled review must not run"),
            ))
            ps, recovery, engine, batch, run = partial.PartialBatchTests().fixture(
                stack, directory, partial.PartialBatchTests.partial_response,
            )
            batch.translation_config.semantic_review = False
            batch.il_translator.begin_translation_completion()
            run()
            self.assertEqual(batch.fallback_count, 1)
            self.assertEqual(len(engine.requests), 2)
            key = engine.requests[0]
            self.assertIn(key, engine.cache.values)
            expected = [p.unicode for p in ps]
            recovery.close()
            for target in (directory, stack.enter_context(tempfile.TemporaryDirectory())):
                restored, _, fresh_engine, fresh_batch, rerun = partial.PartialBatchTests().fixture(
                    stack, target, lambda _: self.fail("cached/restored outputs must not retranslate"),
                )
                fresh_engine.cache = engine.cache
                fresh_batch.translation_config.semantic_review = False
                fresh_batch.il_translator.begin_translation_completion()
                rerun()
                self.assertEqual(fresh_engine.requests, [])
                self.assertEqual([p.unicode for p in restored], expected)
            reviewer.assert_not_called()

    def fixture(self, stack, directory, respond, review):
        values = partial.PartialBatchTests().fixture(stack, directory, respond)
        ps, recovery, engine, batch, run = values
        batch.translation_config.semantic_review = True
        stack.enter_context(patch("babeldoc.format.pdf.quality_review.QualityReview", return_value=review))
        batch.il_translator.begin_translation_completion()
        return values

    def test_partial_fallback_review_correction_is_cached_once_and_progress_is_not_repeated(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            def correct(review):
                item = list(review.items.values())[0]
                item.output = "复核后的完整实验译文。{v0}"
                item.apply(item.output)
            review = FakeReview(correct)
            ps, recovery, engine, batch, run = self.fixture(
                stack, directory, partial.PartialBatchTests.partial_response, review,
            )
            run()
            self.assertEqual(review.register_calls, 6)
            self.assertEqual(review.run_calls, 1)
            self.assertEqual((batch.ok_count, batch.fallback_count), (5, 1))
            self.assertEqual(recovery.snapshot()[0]["succeeded"], 6)
            self.assertEqual(json.loads(engine.cache.values[engine.requests[0]])[0]["output"], ps[0].unicode)
            batch.il_translator.finish_translation_completion()
            self.assertEqual(review.run_calls, 1)

    def test_restored_successes_register_review_without_translation_requests(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            _, recovery, _, _, run = partial.PartialBatchTests().fixture(
                stack, directory, partial.PartialBatchTests.partial_response,
            )
            run()
            recovery.close()
            review = FakeReview()
            _, resumed, engine, _, run = self.fixture(
                stack, directory, lambda _: self.fail("restored text must not retranslate"), review,
            )
            run()
            self.assertEqual(review.register_calls, 6)
            self.assertEqual(review.run_calls, 1)
            self.assertEqual(engine.requests, [])
            self.assertEqual(resumed.snapshot()[0]["succeeded"], 6)

    def test_warm_batch_cache_still_registers_and_applies_review(self):
        with ExitStack() as stack:
            first_dir = stack.enter_context(tempfile.TemporaryDirectory())
            _, _, first_engine, _, run = partial.PartialBatchTests().fixture(
                stack, first_dir, partial.PartialBatchTests.partial_response,
            )
            run()
            batch_key = first_engine.requests[0]
            second_dir = stack.enter_context(tempfile.TemporaryDirectory())
            def correct(review):
                item = list(review.items.values())[0]
                item.output = "缓存译文经过了正文复核。{v0}"
                item.apply(item.output)
            review = FakeReview(correct)
            ps, _, engine, _, run = self.fixture(
                stack, second_dir, lambda _: self.fail("warm batch must not retranslate"), review,
            )
            engine.cache = first_engine.cache
            run()
            self.assertEqual(engine.requests, [])
            self.assertEqual(review.register_calls, 6)
            self.assertEqual(json.loads(engine.cache.values[batch_key])[0]["output"], ps[0].unicode)

    def test_restart_repairs_one_missing_unit_then_new_task_reuses_original_batch(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            def first_response(text):
                if text.startswith("["):
                    return partial.PartialBatchTests.partial_response(text)
                raise RuntimeError("single repair failed")
            ps, recovery, first_engine, _, run = partial.PartialBatchTests().fixture(
                stack, directory, first_response,
            )
            missing_source = ps[2].unicode
            run()
            original_batch = first_engine.requests[0]
            cache = first_engine.cache
            self.assertEqual(recovery.snapshot()[0]["failed"], 1)
            self.assertNotIn(original_batch, cache.values)
            recovery.close()

            def repair_response(text):
                self.assertEqual(text, missing_source)
                return partial.translated(text)
            _, recovery, repair_engine, _, run = partial.PartialBatchTests().fixture(
                stack, directory, repair_response,
            )
            repair_engine.cache = cache
            run()
            self.assertEqual(repair_engine.requests, [missing_source])
            self.assertEqual(recovery.snapshot()[0]["succeeded"], 6)
            self.assertIn(original_batch, cache.values)
            recovery.close()

            fresh_directory = stack.enter_context(tempfile.TemporaryDirectory())
            _, fresh, warm_engine, _, run = partial.PartialBatchTests().fixture(
                stack, fresh_directory, lambda _: self.fail("new task should hit the original full batch"),
            )
            warm_engine.cache = cache
            run()
            self.assertEqual(warm_engine.requests, [])
            self.assertEqual(fresh.snapshot()[0]["succeeded"], 6)

    def test_confirmed_quality_failure_remains_failed_after_finalizers(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            def reject(review):
                item = list(review.items.values())[0]
                recovery.record_quality(item.paragraph, "failed", "body-review-v1", "invalid_review")
                item.output = None
            review = FakeReview(reject)
            _, recovery, engine, _, run = self.fixture(
                stack, directory, partial.PartialBatchTests.partial_response, review,
            )
            run()
            self.assertEqual(recovery.snapshot()[0]["failed"], 1)
            self.assertEqual(recovery.snapshot()[0]["succeeded"], 5)
            self.assertNotIn(engine.requests[0], engine.cache.values)

    def test_review_waits_for_real_fallback_executor_to_exit(self):
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            entered, release, exited = threading.Event(), threading.Event(), threading.Event()
            def respond(text):
                if not text.startswith("["):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("test barrier timeout")
                    exited.set()
                return partial.PartialBatchTests.partial_response(text)
            review = FakeReview(lambda _: self.assertTrue(exited.is_set()))
            ps, _, engine, batch, _ = self.fixture(stack, directory, respond, review)
            config = batch.translation_config
            config.disable_rich_text_translate = False
            config.shared_context_cross_split_part = SimpleNamespace(first_paragraph=ps[0], recent_title_paragraph=ps[0])
            config.pool_max_workers = 1
            config.progress_monitor = SimpleNamespace(stage_start=lambda *args: nullcontext(Mock()))
            config.save_detailed_tracking = False
            config.get_working_file_path = lambda name: Path(directory) / name
            batch.il_translator.get_translate_input = lambda p, *args: ILTranslator.TranslateInput(p.unicode, [], p.pdf_style)
            batch.process_cross_page_paragraph = lambda *args: None
            batch.process_cross_column_paragraph = lambda *args: None
            docs = Document(page=[Page(page_number=0, pdf_paragraph=ps)])
            def process(page, executor, pbar, tracker, fallback, translated_ids):
                executor.submit(batch.translate_paragraph,
                                BatchParagraph(ps, [page] * 6, PageTranslateTracker()),
                                pbar, {}, {}, executor=fallback)
            batch.process_page = process
            with ThreadPoolExecutor(max_workers=1) as outer:
                future = outer.submit(batch.translate, docs)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertFalse(future.done())
                    self.assertEqual(review.run_calls, 0)
                    self.assertNotIn(engine.requests[0], engine.cache.values)
                finally:
                    release.set()
                future.result(timeout=5)
            self.assertEqual(review.run_calls, 1)
            self.assertIn(engine.requests[0], engine.cache.values)

    def test_real_review_corrections_and_approvals_are_reused_on_new_task(self):
        with ExitStack() as stack:
            cache = None
            for iteration in range(2):
                directory = stack.enter_context(tempfile.TemporaryDirectory())
                def respond(text):
                    if text.startswith("["):
                        return json.dumps([{"id": row["id"], "output": partial.translated(row["input"])}
                                           for row in json.loads(text)])
                    rows = json.loads(text.rsplit("\n", 1)[1])
                    return json.dumps([{
                        "id": row["id"], "verdict": "corrected", "evidence": "English",
                        "output": "英语" + partial.translated(row["source"]),
                    } for row in rows])
                ps, recovery, engine, batch, run = partial.PartialBatchTests().fixture(stack, directory, respond)
                engine.limits_each_attempt = True
                def request(text, rate_limit_params=None):
                    params = rate_limit_params or {}
                    params.get("on_attempt", lambda: None)()
                    return engine.do_translate(text)
                engine.do_llm_translate = request
                batch.translation_config.semantic_review = True
                batch.translation_config.glossary_entries = [{"source": "English", "target": "英语"}]
                if cache is not None:
                    engine.cache = cache
                run()
                self.assertEqual(len(engine.requests), 3 if iteration == 0 else 0)
                self.assertTrue(all(p.unicode.startswith("英语") for p in ps))
                self.assertEqual(recovery.quality_snapshot()["checked"], 6)
                cache = engine.cache
