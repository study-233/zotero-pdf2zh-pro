import json
import threading
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import httpx
from tenacity import wait_none

from babeldoc.format.pdf.quality_review import QualityReview, ReviewBudgetExhausted
from babeldoc.format.pdf.document_il import Document, Page, PdfParagraph, PdfParagraphComposition, PdfCharacter
from babeldoc.format.pdf.translation_recovery import TranslationRecovery
from babeldoc.translator.validation import InvalidTranslation, validate_text
from pdf2zh_next.translator.base_translator import BaseTranslator
from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
import test_openai_protocol as protocol_fixtures


SOURCE = "The world model supports training embodied agents without additional observations. {v1}"
TARGET = "世界模型无需额外观测即可支持训练具身智能体。{v1}"
OLD_TARGET = "世界模型无需额外观测即可支持具身智能体。{v1}"
TERMS = [{"source": "world model", "target": "世界模型"}]


class MemoryCache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


class ReviewTranslator(BaseTranslator):
    limits_each_attempt = True

    def __init__(self, respond):
        self.ignore_cache = False
        self.lang_in, self.lang_out = "en", "zh-CN"
        self.cache = MemoryCache()
        self.rate_limiter = Mock()
        self.metrics_collector = None
        self.translate_call_count = self.translate_cache_call_count = 0
        self.respond = respond
        self.requests = []

    def do_translate(self, text, rate_limit_params=None):
        callback = (rate_limit_params or {}).get("on_attempt")
        if callback:
            callback()
        self.requests.append(text)
        return self.respond(text)

    do_llm_translate = do_translate


def passed(prompt):
    rows = json.loads(prompt.split("\n", 1)[1])
    return json.dumps([{"id": row["id"], "verdict": "passed"} for row in rows])


class QualityReviewTests(unittest.TestCase):
    def review(self, translator, *, check=lambda: None, recovery=None, terms=TERMS):
        return QualityReview(translator, recovery=recovery, attempt=1,
                             glossary_entries=terms, check_cancelled=check)

    def add(self, review, *, output=OLD_TARGET, source=SOURCE, context="", body=True):
        paragraph = SimpleNamespace(debug_id="arbitrary-id")
        apply = Mock()
        review.register_candidate(paragraph, source, output, apply, context=context,
                                  risk_hints=("continuation",), is_body=body)
        return paragraph, apply

    def test_corrected_final_output_is_approved_on_warm_run(self):
        translator = ReviewTranslator(lambda prompt: json.dumps([
            {"id": 0, "verdict": "corrected", "evidence": "training embodied agents", "output": TARGET}]))
        review = self.review(translator)
        paragraph, apply = self.add(review, context="original adjacent text")
        self.assertEqual(review.run()["corrected"], 1)
        apply.assert_called_once_with(TARGET)
        self.assertEqual(review.final_output(paragraph), TARGET)
        warm = self.review(translator)
        warm_paragraph, warm_apply = self.add(warm, output=TARGET, context="original adjacent text")
        warm_paragraph.debug_id = "new-process-random-id"
        self.assertEqual(warm.run()["passed"], 1)
        self.assertEqual(len(translator.requests), 1)
        warm_apply.assert_not_called()
        self.assertEqual(warm.final_output(warm_paragraph), TARGET)
        self.assertTrue(all(key.startswith("quality-review:") for key in translator.cache.values))

    def test_candidate_mapping_reuses_correction_but_context_and_glossary_separate(self):
        translator = ReviewTranslator(lambda prompt: json.dumps([
            {"id": 0, "verdict": "corrected", "evidence": "training embodied agents", "output": TARGET}]))
        for context, terms in (("a", TERMS), ("a", TERMS), ("b", TERMS),
                               ("b", [{"source": "world model", "target": "世界建模"}])):
            review = self.review(translator, terms=terms)
            _, apply = self.add(review, context=context)
            review.run()
            apply.assert_called_once_with(TARGET)
        self.assertEqual(len(translator.requests), 3)

    def test_confirmed_invalid_correction_fails_without_cache(self):
        translator = ReviewTranslator(lambda prompt: '[{"id":0,"verdict":"corrected","evidence":"training embodied agents","output":"错误的公式 {v2}"}]')
        recovery = Mock()
        recovery.reserve_review_request.return_value = True
        review = self.review(translator, recovery=recovery)
        paragraph, apply = self.add(review)
        review.run()
        self.assertIsNone(review.final_output(paragraph))
        self.assertEqual(len(translator.requests), 1)
        apply.assert_not_called()
        self.assertEqual(translator.cache.values, {})
        self.assertEqual(recovery.record_quality.call_args.args[1::2], ("failed", "invalid_review"))

    def test_apply_validation_failure_is_not_approved(self):
        translator = ReviewTranslator(lambda prompt: json.dumps([
            {"id": 0, "verdict": "corrected", "evidence": "training embodied agents", "output": TARGET}]))
        review = self.review(translator)
        paragraph, apply = self.add(review)
        apply.side_effect = InvalidTranslation("placeholder_mismatch")
        self.assertEqual(review.run()["failed"], 1)
        self.assertIsNone(review.final_output(paragraph))
        self.assertEqual(translator.cache.values, {})

    def test_malformed_review_is_unchecked_and_never_approved(self):
        translator = ReviewTranslator(lambda prompt: '[{"id":true,"verdict":"passed"}]')
        review = self.review(translator)
        paragraph, _ = self.add(review)
        self.assertEqual(review.run()["unchecked"], 1)
        self.assertEqual(len(translator.requests), 2)
        self.assertEqual(review.final_output(paragraph), OLD_TARGET)
        self.assertEqual(translator.cache.values, {})

    def test_non_body_and_twenty_paragraph_limit(self):
        translator = ReviewTranslator(passed)
        review = self.review(translator)
        for i in range(25):
            self.add(review, source=SOURCE + str(i))
        excluded, apply = self.add(review, body=False)
        result = review.run()
        self.assertEqual((result["selected"], result["notSelected"], result["requestsUsed"]), (20, 5, 5))
        self.assertTrue(all(len(json.loads(prompt.split("\n", 1)[1])) <= 4 for prompt in translator.requests))
        self.assertEqual(review.final_output(excluded), OLD_TARGET)
        apply.assert_not_called()

    def test_cancellation_after_provider_response_never_applies_or_caches(self):
        cancelled = threading.Event()
        def respond(prompt):
            cancelled.set()
            return passed(prompt)
        def check():
            if cancelled.is_set():
                raise InterruptedError("cancelled")
        translator = ReviewTranslator(respond)
        review = self.review(translator, check=check)
        paragraph, apply = self.add(review)
        with self.assertRaises(InterruptedError):
            review.run()
        apply.assert_not_called()
        self.assertIsNone(review.final_output(paragraph))
        self.assertEqual(translator.cache.values, {})

    def test_exhausted_persistent_budget_makes_no_request(self):
        translator = ReviewTranslator(passed)
        recovery = Mock()
        recovery.reserve_review_request.return_value = False
        review = self.review(translator, recovery=recovery)
        self.add(review)
        review.run()
        self.assertEqual(translator.requests, [])
        self.assertEqual(recovery.record_quality.call_args.args[1::2], ("unchecked", "budget_exhausted"))

    def test_unsupported_adapter_never_issues_unbudgeted_request(self):
        translator = ReviewTranslator(passed)
        translator.limits_each_attempt = False
        review = self.review(translator)
        self.add(review)
        self.assertEqual(review.run()["unchecked"], 1)
        self.assertEqual(translator.requests, [])

    def test_global_ignore_cache_includes_review_cache(self):
        translator = ReviewTranslator(passed)
        translator.ignore_cache = True
        for _ in range(2):
            review = self.review(translator)
            self.add(review)
            review.run()
        self.assertEqual(len(translator.requests), 2)
        self.assertEqual(translator.cache.values, {})

    def test_evidence_must_be_a_verbatim_source_excerpt(self):
        for evidence in (None, "", "The paper says something else"):
            with self.subTest(evidence=evidence):
                translator = ReviewTranslator(lambda prompt: json.dumps([
                    {"id": 0, "verdict": "corrected", "evidence": evidence, "output": TARGET}]))
                review = self.review(translator)
                paragraph, apply = self.add(review)
                self.assertEqual(review.run()["unchecked"], 1)
                self.assertEqual(review.final_output(paragraph), OLD_TARGET)
                apply.assert_not_called()
                self.assertEqual(len(translator.requests), 1)
                self.assertEqual(translator.cache.values, {})

    def test_only_approved_risks_select_body_candidates(self):
        translator = ReviewTranslator(passed)
        review = self.review(translator, terms=[])
        paragraphs = []
        for count in (299, 300):
            paragraph = object()
            paragraphs.append(paragraph)
            review.register_candidate(paragraph, "word " * count + "{v1}", TARGET, Mock())
        for count in (7, 8):
            paragraph = object()
            paragraphs.append(paragraph)
            styles = "".join(f"<style id='{i}'>词</style>" for i in range(count))
            review.register_candidate(paragraph, "Scientific content " + styles, "科学内容 " + styles, Mock())
        ordinary = object()
        review.register_candidate(ordinary, SOURCE, TARGET, Mock())
        result = review.run()
        self.assertEqual((result["selected"], result["notSelected"]), (2, 3))
        self.assertEqual([review.candidates[id(p)].status for p in paragraphs],
                         ["not_selected", "passed", "not_selected", "passed"])
        self.assertEqual(review.candidates[id(ordinary)].status, "not_selected")

    def test_glossary_risk_ignores_style_boundaries_in_source_and_target(self):
        source = "The world<style id='0'> model</style> learns visual dynamics."
        for target, selected in (("环境<style id='0'>模型</style>学习视觉动态。", 1),
                                 ("世界<style id='0'>模型</style>学习视觉动态。", 0)):
            translator = ReviewTranslator(passed)
            review = self.review(translator)
            review.register_candidate(object(), source, target, Mock())
            self.assertEqual(review.run()["selected"], selected)
            if selected:
                row = json.loads(translator.requests[0].split("\n", 1)[1])[0]
                self.assertEqual(row["glossary"][0]["source"], "world model")
                self.assertEqual(row["translation"], target)
            else:
                self.assertEqual(translator.requests, [])

    def test_formula_token_does_not_join_glossary_word_fragments(self):
        translator = ReviewTranslator(passed)
        review = self.review(translator, terms=[{"source": "worldmodel", "target": "世界模型"}])
        review.register_candidate(object(), "The world{v1}model learns visual dynamics.",
                                  "世界{v1}模型学习视觉动态。", Mock())
        self.assertEqual(review.run()["selected"], 0)
        self.assertEqual(translator.requests, [])

    def test_document_order_is_independent_of_worker_registration_order(self):
        translator = ReviewTranslator(passed)
        review = self.review(translator)
        for index in reversed(range(25)):
            review.register_candidate(object(), SOURCE + f" order={index}", TARGET, Mock(),
                                      risk_hints=("continuation",), order_key=index)
        self.assertEqual(review.run()["selected"], 20)
        sent = [row["source"] for prompt in translator.requests
                for row in json.loads(prompt.split("\n", 1)[1])]
        self.assertEqual(sent, [SOURCE + f" order={i}" for i in range(20)])

    def test_continuation_group_is_never_split_between_requests(self):
        translator = ReviewTranslator(passed)
        review = self.review(translator)
        for index in range(5):
            review.register_candidate(object(), SOURCE + str(index), TARGET, Mock(),
                                      risk_hints=("continuation",), order_key=index,
                                      continuation_group="pair" if index >= 3 else None)
        self.assertEqual(review.run()["checked"], 5)
        self.assertEqual([len(json.loads(prompt.split("\n", 1)[1])) for prompt in translator.requests], [3, 2])

    def test_mixed_group_reuses_approval_and_only_reviews_unapproved_neighbor(self):
        translator = ReviewTranslator(lambda prompt: json.dumps([
            {"id": 0, "verdict": "corrected", "evidence": "training embodied agents", "output": TARGET}]))
        initial = self.review(translator)
        self.add(initial)
        self.assertEqual(initial.run()["corrected"], 1)

        neighbor_source = SOURCE + " The evaluation covers a second setting."
        def review_neighbor(prompt):
            rows = json.loads(prompt.split("\n", 1)[1])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source"], neighbor_source)
            self.assertEqual(rows[0]["approved_neighbors"], [{"source": SOURCE, "translation": TARGET}])
            self.assertIn("never revise them or return rows for them", prompt)
            return '[{"id":0,"verdict":"passed"}]'
        translator.respond = review_neighbor
        mixed = self.review(translator)
        approved, neighbor = object(), object()
        apply_approved, apply_neighbor = Mock(), Mock()
        mixed.register_candidate(approved, SOURCE, OLD_TARGET, apply_approved,
                                 risk_hints=("continuation",), order_key=0, continuation_group="chain")
        mixed.register_candidate(neighbor, neighbor_source, OLD_TARGET, apply_neighbor,
                                 risk_hints=("continuation",), order_key=1, continuation_group="chain")
        result = mixed.run()
        self.assertEqual((result["corrected"], result["passed"]), (1, 1))
        self.assertEqual(len(translator.requests), 2)
        apply_approved.assert_called_once_with(TARGET)
        apply_neighbor.assert_not_called()
        self.assertEqual(mixed.final_output(approved), TARGET)

    def test_corrected_terminology_retains_selection_priority_on_warm_run(self):
        translator = ReviewTranslator(lambda prompt: json.dumps([
            {"id": 0, "verdict": "corrected", "evidence": "world model", "output": TARGET}]))
        for target in ("环境模型支持训练具身智能体。{v1}", TARGET):
            review = self.review(translator)
            review.register_candidate(object(), SOURCE, target, Mock())
            self.assertEqual(review.run()["checked"], 1)
        self.assertEqual(len(translator.requests), 1)

    def test_cache_priority_rejects_wrong_version_and_boolean(self):
        for overrides in ({"priority": True}, {"priority": 5}, {"version": "old-review"}):
            translator = ReviewTranslator(passed)
            review = self.review(translator, terms=[])
            paragraph = object()
            review.register_candidate(paragraph, SOURCE, TARGET, Mock())
            candidate = review.candidates[id(paragraph)]
            translator._set_review_cache(review._identity(candidate, TARGET), json.dumps({
                "version": "body-review-v1", "priority": 1, "output": TARGET, **overrides}))
            self.assertEqual(review.run()["selected"], 0)
            self.assertEqual(translator.requests, [])

    def test_approved_recovery_reuses_without_global_cache_even_when_cache_ignored(self):
        translator = ReviewTranslator(passed)
        translator.ignore_cache = True
        recovery = Mock()
        recovery.reviewed_output.return_value = {
            "version": "body-review-v1", "priority": 1, "output": TARGET,
        }
        review = self.review(translator, recovery=recovery)
        paragraph = object()
        review.register_candidate(paragraph, SOURCE, TARGET, Mock())
        review.run()
        recovery.reviewed_output.assert_called_once_with(paragraph, SOURCE, TARGET, "body-review-v1")
        self.assertEqual(review.candidates[id(paragraph)].status, "passed")
        self.assertEqual(translator.requests, [])
        self.assertEqual(translator.cache.values, {})

    def test_reopened_recovery_reuses_approval_without_global_cache(self):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        translator = ReviewTranslator(passed)
        translator.ignore_cache = True
        for _ in range(2):
            paragraph = PdfParagraph(debug_id="body", unicode=SOURCE, layout_label="text",
                                     pdf_paragraph_composition=[PdfParagraphComposition(
                                         pdf_character=PdfCharacter(char_unicode="T"))])
            document = Document(page=[Page(page_number=0, pdf_paragraph=[paragraph])])
            recovery = TranslationRecovery(directory / "recovery.sqlite3", "fixed-source-config")
            self.addCleanup(recovery.close)
            recovery.configure_review(1)
            recovery.register(document, SimpleNamespace(skip_references=False, min_text_length=3))
            recovery.record(paragraph, "succeeded", input=SOURCE, translation=TARGET)
            review = self.review(translator, recovery=recovery)
            review.register_candidate(paragraph, SOURCE, TARGET, Mock(), risk_hints=("continuation",))
            self.assertEqual(review.run()["passed"], 1)
            recovery.close()
        self.assertEqual(len(translator.requests), 1)
        self.assertEqual(translator.cache.values, {})

    def test_continuation_group_does_not_cross_twenty_paragraph_limit(self):
        translator = ReviewTranslator(passed)
        review = self.review(translator)
        grouped = []
        for index in range(22):
            paragraph = object()
            group = "pair" if index in (19, 20) else None
            if group:
                grouped.append(paragraph)
            review.register_candidate(paragraph, SOURCE + str(index), TARGET, Mock(),
                                      risk_hints=("continuation",), order_key=index,
                                      continuation_group=group)
        self.assertEqual(review.run()["selected"], 20)
        self.assertTrue(all(review.candidates[id(p)].status == "not_selected" for p in grouped))


class ReviewProviderBudgetTests(unittest.TestCase):
    translator = protocol_fixtures.ProtocolTests.translator

    def test_provider_retries_count_toward_eight_real_requests(self):
        translator = self.translator(lambda request: httpx.Response(503, json={"error": "temporary"}),
                                     protocol="chat_completions")
        translator.do_llm_translate = MethodType(
            OpenAITranslator.do_llm_translate.retry_with(wait=wait_none()), translator)
        review = QualityReview(translator, recovery=None, attempt=1,
                               glossary_entries=TERMS, check_cancelled=lambda: None)
        for i in range(20):
            review.register_candidate(object(), SOURCE + str(i), OLD_TARGET, Mock(), risk_hints=("continuation",))
        result = review.run()
        self.assertEqual(len(self.requests), 8)
        self.assertEqual(result["requestsUsed"], 8)
        self.assertEqual(result["unchecked"], 20)


class DeferredCacheTests(unittest.TestCase):
    def test_defer_preserves_cache_read_and_final_commit_does_not_issue_request(self):
        translator = ReviewTranslator(lambda _: TARGET)
        params = {"defer_cache_write": True, "validate_output": lambda value: validate_text(SOURCE, value, "zh-CN")}
        self.assertEqual(translator.llm_translate(SOURCE, rate_limit_params=params), TARGET)
        self.assertEqual(translator.cache.values, {})
        self.assertTrue(translator._commit_validated_cache(SOURCE, TARGET, params))
        self.assertEqual(translator.llm_translate(SOURCE, rate_limit_params=params), TARGET)
        self.assertEqual(len(translator.requests), 1)
        self.assertFalse(translator._commit_validated_cache(SOURCE, "错误 {v2}", params))
        self.assertEqual(translator.cache.get(SOURCE), TARGET)

    def test_commit_respects_cancellation_ignore_cache_and_cache_failure(self):
        translator = ReviewTranslator(lambda _: TARGET)
        self.assertFalse(translator._commit_validated_cache(SOURCE, TARGET, ignore_cache=True))
        with self.assertRaises(InterruptedError):
            translator._commit_validated_cache(SOURCE, TARGET, {"check_cancelled": Mock(side_effect=InterruptedError)})
        translator.cache.set = Mock(side_effect=OSError("cache unavailable"))
        self.assertFalse(translator._commit_validated_cache(SOURCE, TARGET))
        self.assertEqual(translator.requests, [])


if __name__ == "__main__":
    unittest.main()
