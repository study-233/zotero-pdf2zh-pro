from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import json
import hashlib
import shutil
import threading
import time
from pdf2zh_pro.types import TranslationOutputFile, TranslationResult, ValidationResult, DiagnosticMessage
from pdf2zh_pro.errors import explain_service_error, diagnose_service_error
from pdf2zh_pro.config import create_model_settings, SERVICE_FIELD_MAP, require_supported_service, build_service_detail
from pathlib import Path
from typing import Any
from typing import Callable

from babeldoc.assets.assets import download_all_fonts_async
import pymupdf

from pdf2zh_pro.translator.providers import TRANSLATION_ENGINE_METADATA
from babeldoc.format.pdf.translation_config import TranslationConfig as BabelDOCConfig
from babeldoc.format.pdf.translation_config import WatermarkOutputMode as BabelDOCWatermarkMode
from babeldoc.glossary import Glossary
from pdf2zh_pro.config import SettingsModel
from babeldoc.format.pdf.high_level import async_translate as babeldoc_translate
from pdf2zh_pro.translator import prepare_translator, prepare_term_translator
from observability import TaskMetricsCollector
from pdf2zh_pro.translator.providers import CodexSettings, ChatGPTSettings, OpenAISettings, SiliconFlowSettings
from pdf2zh_pro.translator.openai_protocol import (
    normalize_endpoint, parse_request_options,
)

LOGGER = logging.getLogger("zotero_pdf2zh_server.translate")
ProgressCallback = Callable[[dict[str, Any]], None]
MetricsCallback = Callable[[dict[str, Any]], None]
ConfigReadyCallback = Callable[[BabelDOCConfig], None]
_TEXT_CHECK_TRANSLATION_LOCK = threading.Lock()
_TEXT_CHECK_PATCH_LOCK = threading.Lock()
_SKIP_TEXT_CHECKS_ENABLED = False
_TEXT_CHECK_PATCH_INSTALLED = False
DEFAULT_TRANSLATION_PROVIDER_TIMEOUT_SECONDS = 120


def install_text_check_bypass() -> None:
    global _TEXT_CHECK_PATCH_INSTALLED
    with _TEXT_CHECK_PATCH_LOCK:
        if _TEXT_CHECK_PATCH_INSTALLED:
            return

        import babeldoc.format.pdf.high_level as babeldoc_high_level
        from babeldoc.format.pdf.document_il.midend import automatic_term_extractor
        from babeldoc.format.pdf.document_il.midend import il_translator_llm_only
        from babeldoc.format.pdf.document_il.midend import paragraph_finder
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import (
            ParagraphFinder,
        )
        from babeldoc.format.pdf.document_il.utils import paragraph_helper

        original_check_cid_char = babeldoc_high_level.check_cid_char
        original_check_cid_paragraph = ParagraphFinder.check_cid_paragraph
        original_is_cid_paragraph = paragraph_helper.is_cid_paragraph

        def text_checks_skipped() -> bool:
            return _SKIP_TEXT_CHECKS_ENABLED

        def check_cid_char(document):
            if text_checks_skipped():
                return False
            return original_check_cid_char(document)

        def check_cid_paragraph(self, document):
            if text_checks_skipped():
                return False
            return original_check_cid_paragraph(self, document)

        def is_cid_paragraph(paragraph):
            if text_checks_skipped():
                return False
            return original_is_cid_paragraph(paragraph)

        babeldoc_high_level.check_cid_char = check_cid_char
        ParagraphFinder.check_cid_paragraph = check_cid_paragraph
        paragraph_helper.is_cid_paragraph = is_cid_paragraph
        paragraph_finder.is_cid_paragraph = is_cid_paragraph
        automatic_term_extractor.is_cid_paragraph = is_cid_paragraph
        il_translator_llm_only.is_cid_paragraph = is_cid_paragraph
        _TEXT_CHECK_PATCH_INSTALLED = True


def set_text_checks_skipped(skip: bool) -> bool:
    global _SKIP_TEXT_CHECKS_ENABLED
    previous = _SKIP_TEXT_CHECKS_ENABLED
    _SKIP_TEXT_CHECKS_ENABLED = skip
    return previous


class ProgressLogger:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self._last_stage: str | None = None
        self._last_logged_stage_bucket = -1
        self._last_logged_overall_bucket = -1

    def log(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")

        if event_type == "progress_start":
            stage = self._stage_name(event)
            self._last_stage = stage
            self._last_logged_stage_bucket = -1
            self._last_logged_overall_bucket = -1
            LOGGER.info(
                "[%s] stage started: %s (%s items)",
                self.job_id,
                stage,
                event.get("stage_total", "?"),
            )
            return

        if event_type == "progress_update":
            stage = self._stage_name(event)
            stage_progress = self._percentage(event.get("stage_progress"))
            overall_progress = self._percentage(event.get("overall_progress"))
            stage_bucket = int(stage_progress // 10)
            overall_bucket = int(overall_progress // 5)

            if (
                stage == self._last_stage
                and stage_bucket == self._last_logged_stage_bucket
                and overall_bucket == self._last_logged_overall_bucket
            ):
                return

            self._last_stage = stage
            self._last_logged_stage_bucket = stage_bucket
            self._last_logged_overall_bucket = overall_bucket
            LOGGER.info(
                "[%s] stage=%s stage_progress=%.1f%% overall=%.1f%% (%s/%s)",
                self.job_id,
                stage,
                stage_progress,
                overall_progress,
                event.get("stage_current", "?"),
                event.get("stage_total", "?"),
            )
            return

        if event_type == "progress_end":
            stage = self._stage_name(event)
            LOGGER.info(
                "[%s] stage finished: %s overall=%.1f%%",
                self.job_id,
                stage,
                self._percentage(event.get("overall_progress")),
            )
            return

        if event_type == "finish":
            LOGGER.info("[%s] translation finished", self.job_id)
            return

        if event_type == "error":
            LOGGER.error(
                "[%s] translation failed: error_type=translation_event",
                self.job_id,
            )

    @staticmethod
    def _stage_name(event: dict[str, Any]) -> str:
        return str(event.get("stage") or "unknown")

    @staticmethod
    def _percentage(value: Any) -> float:
        try:
            return max(0.0, min(float(value), 100.0))
        except (TypeError, ValueError):
            return 0.0


def create_font_progress_event(
    stage: str,
    current: int,
    total: int,
) -> dict[str, Any]:
    bounded_current = max(0, min(current, total)) if total > 0 else 0
    stage_progress = 100.0 if total <= 0 else bounded_current * 100.0 / total
    if bounded_current == 0:
        event_type = "progress_start"
    elif bounded_current >= total:
        event_type = "progress_end"
    else:
        event_type = "progress_update"
    return {
        "type": event_type,
        "stage": stage,
        "stage_current": bounded_current,
        "stage_total": total,
        "stage_progress": stage_progress,
        "overall_progress": 0.0,
    }


def build_settings_input(payload: dict[str, Any]) -> dict[str, Any]:
    input_path = Path(payload["input_path"])
    output_dir = Path(payload["output_dir"])
    output_modes = payload["output_modes"]
    service = require_supported_service(payload["service"])

    translation_input: dict[str, Any] = {
        "lang_in": payload["source_lang"],
        "lang_out": payload["target_lang"],
        "output": str(output_dir),
        "qps": max(int(payload.get("qps", 8) or 8), 1),
        "no_auto_extract_glossary": bool(
            payload.get("no_auto_extract_glossary", True)
        ),
    }
    pdf_input: dict[str, Any] = {
        "no_mono": "mono" not in output_modes,
        "no_dual": "dual" not in output_modes,
        "watermark_output_mode": (
            "no_watermark" if payload.get("no_watermark", True) else "watermarked"
        ),
        "ocr_workaround": bool(payload.get("ocr")),
        "auto_enable_ocr_workaround": bool(payload.get("auto_ocr")),
        "translate_table_text": bool(payload.get("translate_table_text", True)),
        "skip_references": bool(payload.get("skip_references", False)),
    }
    settings_input: dict[str, Any] = {
        "translation": translation_input,
        "pdf": pdf_input,
    }

    if payload.get("pool_size"):
        translation_input["pool_max_workers"] = int(payload["pool_size"])

    font_family = payload.get("font_family")
    if font_family and font_family != "auto":
        translation_input["primary_font_family"] = font_family

    skip_last_pages = int(payload.get("skip_last_pages", 0) or 0)
    if skip_last_pages > 0:
        with pymupdf.open(input_path) as document:
            total_pages = document.page_count
        last_page = total_pages - skip_last_pages
        if last_page < 1:
            raise ValueError(
                f"skipLastPages={skip_last_pages} removes every page from {input_path.name}"
            )
        pdf_input["pages"] = f"1-{last_page}"

    llm_api = payload.get("llm_api") or {}
    detail = build_service_detail(service, llm_api)
    engine_type = next(item.setting_model_type for item in TRANSLATION_ENGINE_METADATA
                       if item.cli_flag_name == service)
    settings_input["translate_engine_settings"] = engine_type.model_validate(detail)

    return settings_input


def create_runtime_settings(payload):
    return create_model_settings(payload, build_settings_input(payload))


def ensure_translation_provider_timeout(settings: Any) -> None:
    engine = getattr(settings, "translate_engine_settings", None)
    if isinstance(engine, OpenAISettings) and not engine.openai_timeout:
        engine.openai_timeout = str(DEFAULT_TRANSLATION_PROVIDER_TIMEOUT_SECONDS)


def resolve_output_path(output_path: str | Path, output_dir: Path) -> Path:
    path = Path(output_path)
    if path.is_absolute():
        return path
    return output_dir / path


def collect_output_files(
    translate_result: Any,
    output_dir: Path,
    requested_output_modes: list[str],
    input_name: str,
) -> dict[str, TranslationOutputFile]:
    candidates = {
        "mono": getattr(translate_result, "mono_pdf_path", None),
        "dual": getattr(translate_result, "dual_pdf_path", None),
    }
    files: dict[str, TranslationOutputFile] = {}

    for output_mode in requested_output_modes:
        selected_path = candidates.get(output_mode)
        if not selected_path:
            raise RuntimeError(
                f"pdf2zh_pro did not produce a {output_mode} PDF for {input_name}"
            )

        resolved_path = resolve_output_path(selected_path, output_dir)
        if not resolved_path.exists():
            raise FileNotFoundError(f"Translated PDF not found: {resolved_path}")

        persistent_path = output_dir / resolved_path.name
        if resolved_path != persistent_path:
            shutil.move(resolved_path, persistent_path)
            resolved_path = persistent_path

        files[output_mode] = TranslationOutputFile(
            output_mode=output_mode,
            output_path=resolved_path,
            filename=resolved_path.name,
        )

    return files


async def translate_pdf_with_callbacks(
    payload: dict[str, Any],
    job_id: str,
    progress_callback: ProgressCallback | None = None,
    metrics_callback: MetricsCallback | None = None,
    on_config_ready: ConfigReadyCallback | None = None,
    cancel_event: threading.Event | None = None,
    evidence_identity: dict | None = None,
) -> TranslationResult:
    started_at = time.monotonic()
    cancel_event = cancel_event or threading.Event()

    def check_cancelled() -> None:
        if cancel_event.is_set():
            raise asyncio.CancelledError

    check_cancelled()
    input_path = Path(payload["input_path"])
    output_dir = Path(payload["output_dir"])
    output_modes = payload["output_modes"]
    skip_text_checks = bool(payload.get("skip_text_checks"))

    if skip_text_checks:
        install_text_check_bypass()
        LOGGER.warning("[%s] skipping BabelDOC CID text extraction checks", job_id)

    # A cancelled to_thread(lock.acquire) leaves a live waiter that can acquire
    # the lock later with nobody left to release it.
    try:
        while not _TEXT_CHECK_TRANSLATION_LOCK.acquire(blocking=False):
            check_cancelled()
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        cancel_event.set()
        raise
    previous_skip_text_checks = _SKIP_TEXT_CHECKS_ENABLED
    initialization_started_at = time.monotonic()
    metrics_collector: TaskMetricsCollector | None = None
    heartbeat_task: asyncio.Task | None = None
    translation_config: BabelDOCConfig | None = None
    recovery = None
    from diagnostic_evidence import EvidenceLedger
    evidence = EvidenceLedger(input_path.parent, job_id, (evidence_identity or {}).get('attempt', 1))
    evidence_status = 'failed'
    from diagnostics import requested_configuration
    evidence.execution(requestedConfiguration=requested_configuration(payload))
    translation_started = False
    finalize_started_at = None
    try:
        check_cancelled()
        previous_skip_text_checks = set_text_checks_skipped(skip_text_checks)
        progress_logger = ProgressLogger(job_id)
        settings = create_runtime_settings(payload)
        ensure_translation_provider_timeout(settings)
        check_cancelled()
        engine_settings = getattr(settings, "translate_engine_settings", None)
        if isinstance(engine_settings, (OpenAISettings, CodexSettings, ChatGPTSettings)):
            metrics_collector = TaskMetricsCollector(
                task_id=job_id, provider=str(payload["service"]),
                model=(engine_settings.codex_model if isinstance(engine_settings, CodexSettings)
                       else engine_settings.chatgpt_model if isinstance(engine_settings, ChatGPTSettings)
                       else engine_settings.openai_model), callback=metrics_callback,
                evidence=evidence,
            )
            metrics_collector.record_stage("Engine Wait", initialization_started_at - started_at)
        evidence.execution(requestTracing=metrics_collector is not None)
        try:
            translation_config = create_babeldoc_config(
                settings, input_path, glossary_entries=payload.get("glossary_entries"),
                metrics_collector=metrics_collector,
                check_cancelled=check_cancelled,
            )
        finally:
            if metrics_collector is not None:
                metrics_collector.record_stage("Initialization", time.monotonic() - initialization_started_at)
        translation_config.cancel_event = cancel_event
        translation_config.diagnostic_callback = progress_callback
        from diagnostics import safe_fields
        effective_configuration = safe_fields({
                "provider": payload["service"],
                "model": getattr(translation_config.translator, "model", None),
                "protocol": getattr(translation_config.translator, "resolved_protocol", None),
                "qps": getattr(translation_config, "qps", None),
                "poolSize": translation_config.pool_max_workers,
                "sourceLang": getattr(translation_config, "lang_in", payload.get("source_lang")),
                "targetLang": getattr(translation_config, "lang_out", payload.get("target_lang")),
            })
        evidence.execution(effectiveConfiguration=effective_configuration)
        if progress_callback is not None:
            progress_callback({"type": "diagnostic_configuration", "effectiveConfiguration": effective_configuration})
        translation_config.semantic_review = bool(payload.get("semantic_review", False))
        translation_config.review_attempt = int(payload.get("review_attempt", payload.get("repair_attempt", 1)))
        translation_config.glossary_entries = payload.get("glossary_entries") or []
        check_cancelled()
        translation_config.save_detailed_tracking = False
        from babeldoc.format.pdf.translation_recovery import TranslationRecovery
        from babeldoc.format.pdf.document_il.midend.body_input import BODY_INPUT_VERSION
        from babeldoc.format.pdf.quality_review import REVIEW_VERSION
        fingerprint_payload = {k: v for k, v in payload.items() if k not in {
            "input_path", "output_dir", "qps", "pool_size", "repair_attempt", "review_attempt", "translation_memory_path",
        }}
        fingerprint_payload["llm_api"] = {k: v for k, v in payload.get("llm_api", {}).items() if k != "apiKey"}
        if fingerprint_payload["llm_api"].get("reasoningMode") == "default":
            fingerprint_payload["llm_api"].pop("reasoningMode")
        fingerprint_payload["body_input_version"] = BODY_INPUT_VERSION
        fingerprint_payload["parser_version"] = "babeldoc-native-0.6.4-v1"
        from pdf2zh_pro.glossary import GLOSSARY_POLICY_VERSION
        uses_glossary = bool(payload.get("glossary_entries")) or not payload.get("no_auto_extract_glossary", True)
        if uses_glossary:
            fingerprint_payload["glossary_policy_version"] = GLOSSARY_POLICY_VERSION
        review_policy = {"enabled": translation_config.semantic_review, "version": REVIEW_VERSION}
        fingerprint_payload["review_policy"] = review_policy
        fingerprint = hashlib.sha256(input_path.read_bytes())
        fingerprint.update(json.dumps(fingerprint_payload, sort_keys=True).encode())
        recovery = TranslationRecovery(
            input_path.parent / "paragraph-recovery.sqlite3", fingerprint.hexdigest(), progress_callback,
            first_success_callback=metrics_collector.first_paragraph_completed if metrics_collector is not None else None,
            evidence=evidence, task_attempt=(evidence_identity or {}).get('attempt', 1),
        )
        translation_config.recovery = recovery
        if translation_config.semantic_review:
            recovery.configure_review(translation_config.review_attempt)
        slots = threading.BoundedSemaphore(translation_config.pool_max_workers)
        main_translator = translation_config.translator
        for engine in (main_translator, getattr(translation_config, "term_extraction_translator", None)):
            if engine is not None and hasattr(engine, "add_cache_impact_parameters"):
                engine.add_cache_impact_parameters("body_input_version", BODY_INPUT_VERSION)
                engine.add_cache_fingerprint("task_glossary", payload.get("glossary_entries", []))
                engine.add_cache_fingerprint("review_policy", review_policy)
                if uses_glossary:
                    engine.add_cache_impact_parameters("glossary_policy_version", GLOSSARY_POLICY_VERSION)
            if engine is not None and hasattr(engine, "configure_execution"):
                if (getattr(engine.rate_limiter, "max_qps", None)
                        == getattr(main_translator.rate_limiter, "max_qps", None)):
                    engine.rate_limiter = main_translator.rate_limiter
                engine.configure_execution(translation_config.pool_max_workers, translation_config.raise_if_cancelled, slots)
        if metrics_collector is not None:
            translator = translation_config.translator
            if str(payload["service"]).lower() == "deepseek":
                translator.configure_cache_namespace(provider="deepseek")

            async def publish_metrics_heartbeat() -> None:
                while True:
                    await asyncio.sleep(1)
                    metrics_collector.emit_heartbeat()

            heartbeat_task = asyncio.create_task(publish_metrics_heartbeat())
        if on_config_ready is not None:
            on_config_ready(translation_config)
        check_cancelled()

        last_font_progress: tuple[str, int, int] | None = None

        def publish_font_progress(stage: str, current: int, total: int) -> None:
            nonlocal last_font_progress
            check_cancelled()
            progress_key = (stage, current, total)
            if progress_key == last_font_progress:
                return
            last_font_progress = progress_key
            event = create_font_progress_event(stage, current, total)
            if metrics_collector is not None:
                metrics_collector.update_progress(event)
            progress_logger.log(event)
            if progress_callback is not None:
                progress_callback(event)

        font_started_at = time.monotonic()
        font_task = asyncio.create_task(
            download_all_fonts_async(progress_callback=publish_font_progress)
        )
        try:
            while not font_task.done():
                check_cancelled()
                await asyncio.wait({font_task}, timeout=0.05)
            font_task.result()
            check_cancelled()
        except asyncio.CancelledError:
            cancel_event.set()
            font_task.cancel()
            # Drain its async HTTP cleanup before another task prepares fonts.
            while not font_task.done():
                try:
                    await asyncio.shield(font_task)
                except asyncio.CancelledError:
                    continue
            with contextlib.suppress(asyncio.CancelledError):
                font_task.result()
            raise
        finally:
            if metrics_collector is not None:
                metrics_collector.record_stage("Font Preparation", time.monotonic() - font_started_at)

        LOGGER.info(
            "[%s] translation started: file=%s service=%s output_modes=%s",
            job_id,
            input_path.name,
            payload["service"],
            ",".join(output_modes),
        )

        check_cancelled()
        result = None
        translation_started = True
        async with contextlib.aclosing(babeldoc_translate(translation_config)) as events:
            async for event in events:
                check_cancelled()
                if metrics_collector is not None:
                    metrics_collector.update_progress(event)
                progress_logger.log(event)
                if progress_callback is not None:
                    progress_callback(event)
                check_cancelled()

                if event["type"] == "error":
                    error = event.get("error")
                    if isinstance(error, asyncio.CancelledError) or error is asyncio.CancelledError:
                        raise asyncio.CancelledError
                    raise RuntimeError(
                        explain_service_error(
                            error or "pdf2zh_pro translation failed"
                        )
                    )
                if event["type"] == "finish":
                    result = event["translate_result"]

        # The iterator joins the actual translation worker before output commit.
        check_cancelled()
        finalize_started_at = time.monotonic()
        if result is not None:
            files = collect_output_files(
                result,
                output_dir,
                output_modes,
                input_path.name,
            )
            LOGGER.info(
                "[%s] output ready: %s",
                job_id,
                ", ".join(file.filename for file in files.values()),
            )
            if payload.get("repair_attempt"):
                repaired_files = {}
                for mode, file in files.items():
                    check_cancelled()
                    renamed = file.output_path.with_name(f"{file.output_path.stem}.repair-{payload['repair_attempt']}.pdf")
                    file.output_path.replace(renamed)
                    repaired_files[mode] = TranslationOutputFile(mode, renamed, renamed.name)
                files = repaired_files
            summary, failed = translation_config.recovery.snapshot()
            evidence_status = 'incomplete' if summary['failed'] or summary['pending'] else 'completed'
            check_cancelled()
            # The parent commits the PDF result before its background memory sync.
            memory_sync = "pending" if payload.get("translation_memory_path") else None
            check_cancelled()
            return TranslationResult(files=files, translation_summary=summary, failed_paragraphs=failed,
                                     quality_summary=recovery.quality_snapshot(), translation_memory_sync=memory_sync)
    except asyncio.CancelledError:
        cancel_event.set()
        evidence_status = 'cancelled'
        if translation_config is not None:
            translation_config.cancel_translation()
        raise
    finally:
        if finalize_started_at is None:
            finalize_started_at = time.monotonic()
        try:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            if translation_config is not None and not translation_started:
                translation_config.cleanup_temp_files()
        finally:
            try:
                if recovery is not None:
                    if metrics_collector is not None:
                        metrics_collector.set_translation_summary(recovery.snapshot()[0])
                    recovery.close()
            finally:
                try:
                    snapshot = metrics_collector.snapshot() if metrics_collector is not None else None
                    health = evidence.close(evidence_status, recovery.snapshot()[0] if recovery is not None else None,
                        request_summary={**snapshot['requests'], 'cacheHits': snapshot['localCache']['hits']} if snapshot is not None else None)
                    if progress_callback is not None:
                        progress_callback({'type': 'diagnostic_evidence', 'evidenceCollection': health})
                except Exception:
                    LOGGER.warning('diagnostic evidence finalization unavailable')
                finally:
                    set_text_checks_skipped(previous_skip_text_checks)
                    _TEXT_CHECK_TRANSLATION_LOCK.release()
                if metrics_collector is not None:
                    ended_at = time.monotonic()
                    metrics_collector.record_stage("Finalize", ended_at - finalize_started_at)
                    metrics_collector.record_stage("Total", ended_at - started_at)
                    metrics_collector.emit_final()

    raise RuntimeError("pdf2zh_pro finished without producing a result")


def run_live_translator_test(translator: Any, timeout_seconds: int = 20) -> dict[str, Any]:
    if getattr(translator, "name", None) in {"codex", "chatgpt"}:
        from codex_client import CodexError
        from chatgpt_client import ChatGPTError
        # The app-server client interrupts the turn before returning on timeout.
        # Do not leave a background connection test consuming the account quota.
        original_timeout = translator.timeout
        original_check = translator.check_cancelled
        deadline = time.monotonic() + timeout_seconds

        def check_deadline():
            original_check()
            if time.monotonic() >= deadline:
                raise TimeoutError(f"账号通道连接测试超过 {timeout_seconds} 秒，请重试。")

        translator.timeout = min(float(original_timeout), timeout_seconds)
        translator.check_cancelled = check_deadline
        try:
            translated = translator.translate("Hello", ignore_cache=True,
                                              rate_limit_params={"metric_kind": "initialization"})
            return {"enabled": True, "ok": True, "message": str(translated)[:200]}
        except (CodexError, ChatGPTError):
            raise
        except Exception as exc:
            return {"enabled": True, "ok": False, "message": explain_service_error(exc)}
        finally:
            translator.timeout = original_timeout
            translator.check_cancelled = original_check
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        translator.translate,
        "Hello",
        True,
    )
    try:
        translated = future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        return {
            "enabled": True,
            "ok": False,
            "message": f"Provider test timed out after {timeout_seconds} seconds.",
        }
    except Exception as exc:
        executor.shutdown(wait=False, cancel_futures=True)
        return {
            "enabled": True,
            "ok": False,
            "message": explain_service_error(exc),
        }
    finally:
        if future.done():
            executor.shutdown(wait=False, cancel_futures=True)

    return {
        "enabled": True,
        "ok": True,
        "message": str(translated)[:200] if translated is not None else "",
    }


def validate_service_config(payload: dict[str, Any], job_id: str, *, check_cancelled=None) -> ValidationResult:
    validation_payload = payload
    live_test_enabled = bool(payload.get("live_test", False))

    LOGGER.info(
        "[%s] validating llm config: service=%s",
        job_id,
        validation_payload["service"],
    )
    try:
        settings = create_model_settings(validation_payload)
        translator = prepare_translator(settings, **({"check_cancelled": check_cancelled} if check_cancelled else {}))
    except Exception as exc:
        from codex_client import CodexError
        from chatgpt_client import ChatGPTError
        if isinstance(exc, (CodexError, ChatGPTError)):
            raise
        raise RuntimeError(explain_service_error(exc)) from exc

    model = getattr(translator, "model", None)
    diagnostics = [
        DiagnosticMessage(
            code="config_constructed",
            severity="info",
            message="Provider configuration can be loaded by pdf2zh_pro.",
        ).to_dict()
    ]
    live_test = {"enabled": live_test_enabled}
    if live_test_enabled:
        LOGGER.info(
            "[%s] running live provider test: service=%s",
            job_id,
            validation_payload["service"],
        )
        if getattr(translator, "resolved_protocol", None):
            live_test = {"enabled": True, "ok": True, "message": "短文本连接检查通过"}
        else:
            live_test = run_live_translator_test(translator)
        if live_test.get("ok") and getattr(translator, "reasoning_mode", "default") == "off":
            requests = getattr(translator, "_pending_initialization_metrics", [])
            reasoning = [event["usage"].get("reasoning_tokens") for event in requests]
            known = [value for value in reasoning if isinstance(value, (int, float))]
            live_test["reasoningMessage"] = (
                "接口仍返回推理 Token，关闭设置可能未生效" if any(value > 0 for value in known)
                else "接口未报告推理 Token，无法确认是否关闭" if not known
                else "本次测试报告推理 Token 为 0"
            )
        if live_test.get("ok"):
            status_info = getattr(translator, "reasoning_status", None)
            if callable(status_info):
                live_test["reasoning"] = status_info()
            diagnostics.append(
                DiagnosticMessage(
                    code="live_test_ok",
                    severity="info",
                    message="Provider accepted a short translation request.",
                ).to_dict()
            )
        else:
            diagnostics.extend(diagnose_service_error(live_test.get("message", "")))

    status = "warning" if live_test_enabled and not live_test.get("ok") else "ok"
    LOGGER.info(
        "[%s] llm config %s: service=%s model=%s live_test=%s",
        job_id,
        status,
        validation_payload["service"],
        model or "-",
        live_test.get("ok") if live_test_enabled else "skipped",
    )
    runtime = getattr(getattr(translator, "client", None), "runtime", None) if validation_payload["service"] == "codex" else None
    return ValidationResult(
        codex_runtime=runtime if isinstance(runtime, dict) else None,
        service=validation_payload["service"],
        model=model,
        diagnostics=diagnostics,
        live_test=live_test,
        status=status,
        resolved_protocol=getattr(translator, "resolved_protocol", None),
    )


def _get_glossaries(settings: SettingsModel, glossary_entries=None) -> list[Glossary] | None:
    glossaries = []
    for file in (settings.translation.glossaries or "").split(","):
        if not file:
            continue
        glossaries.append(
            Glossary.from_csv(Path(file), target_lang_out=settings.translation.lang_out)
        )
    if glossary_entries:
        from babeldoc.glossary import GlossaryEntry
        from pdf2zh_pro.glossary import glossary_entries_for_language
        entries = glossary_entries_for_language(glossary_entries, settings.translation.lang_out)
        if entries:
            glossaries.append(Glossary("Imported glossary", [
                GlossaryEntry(row["source"], row["target"], row["tgt_lng"])
                for row in entries
            ]))
    return glossaries or None

def create_babeldoc_config(settings: SettingsModel, file: Path, *, glossary_entries=None, metrics_collector=None, check_cancelled=None) -> BabelDOCConfig:
    if not isinstance(settings, SettingsModel):
        raise ValueError(f"{type(settings)} is not SettingsModel")
    cancellation = {"check_cancelled": check_cancelled} if check_cancelled else {}
    translator = prepare_translator(settings, metrics_collector=metrics_collector, **cancellation)
    if translator is None:
        raise ValueError("No translator found")

    if settings.translation.no_auto_extract_glossary:
        term_extraction_translator = translator
    elif (
        settings.term_extraction_engine_settings == settings.translate_engine_settings
        and (settings.translation.term_qps or settings.translation.qps) == settings.translation.qps
        and not getattr(
            translator, "requires_dedicated_term_extraction_translator", False
        )
    ):
        term_extraction_translator = translator
        if recommended_qps := getattr(translator, "recommended_qps", None):
            settings.translation.term_qps = min(settings.translation.qps, recommended_qps)
        if recommended_pool_max_workers := getattr(
            translator, "recommended_pool_max_workers", None
        ):
            settings.translation.term_pool_max_workers = min(
                settings.translation.term_pool_max_workers or settings.translation.pool_max_workers
                or recommended_pool_max_workers, recommended_pool_max_workers,
            )
    else:
        if (
            settings.term_extraction_engine_settings == settings.translate_engine_settings
            and getattr(translator, "resolved_protocol", None)
        ):
            settings.term_extraction_engine_settings = settings.translate_engine_settings.model_copy(update={
                "openai_api_protocol": translator.resolved_protocol,
                "openai_base_url": str(translator.client.base_url),
            })
        term_extraction_translator = prepare_term_translator(settings, metrics_collector=metrics_collector, **cancellation)

    if hasattr(translator, "request_state") and hasattr(term_extraction_translator, "request_state"):
        term_extraction_translator.request_state = translator.request_state

    # 设置分割策略
    split_strategy = None
    if settings.pdf.max_pages_per_part:
        split_strategy = BabelDOCConfig.create_max_pages_per_part_split_strategy(
            settings.pdf.max_pages_per_part
        )

    # 设置水印模式
    watermark_output_mode_maps = {
        "no_watermark": BabelDOCWatermarkMode.NoWatermark,
        "both": BabelDOCWatermarkMode.Both,
        "watermarked": BabelDOCWatermarkMode.Watermarked,
    }

    watermark_output_mode = settings.pdf.watermark_output_mode

    watermark_mode = watermark_output_mode_maps.get(
        watermark_output_mode, BabelDOCWatermarkMode.Watermarked
    )

    babeldoc_config = BabelDOCConfig(
        input_file=file,
        font=None,
        pages=settings.pdf.pages,
        output_dir=settings.translation.output,
        doc_layout_model=None,
        translator=translator,
        debug=settings.basic.debug,
        lang_in=settings.translation.lang_in,
        lang_out=settings.translation.lang_out,
        no_dual=settings.pdf.no_dual,
        no_mono=settings.pdf.no_mono,
        qps=settings.translation.qps,
        # 传递原来缺失的参数
        formular_font_pattern=settings.pdf.formular_font_pattern,
        formular_char_pattern=settings.pdf.formular_char_pattern,
        split_short_lines=settings.pdf.split_short_lines,
        short_line_split_factor=settings.pdf.short_line_split_factor,
        disable_rich_text_translate=settings.pdf.disable_rich_text_translate,
        dual_translate_first=settings.pdf.dual_translate_first,
        enhance_compatibility=settings.pdf.enhance_compatibility,
        use_alternating_pages_dual=settings.pdf.use_alternating_pages_dual,
        watermark_output_mode=watermark_mode,
        min_text_length=settings.translation.min_text_length,
        report_interval=settings.report_interval,
        skip_clean=settings.pdf.skip_clean,
        # 添加分割策略
        split_strategy=split_strategy,
        # Native ruled cells do not require an OCR model.
        translate_table_text=settings.pdf.translate_table_text,
        skip_references=settings.pdf.skip_references,
        skip_scanned_detection=settings.pdf.skip_scanned_detection,
        ocr_workaround=settings.pdf.ocr_workaround,
        custom_system_prompt=settings.translation.custom_system_prompt,
        glossaries=_get_glossaries(settings, glossary_entries),
        auto_enable_ocr_workaround=settings.pdf.auto_enable_ocr_workaround,
        pool_max_workers=settings.translation.pool_max_workers,
        auto_extract_glossary=not settings.translation.no_auto_extract_glossary,
        primary_font_family=settings.translation.primary_font_family,
        only_include_translated_page=settings.pdf.only_include_translated_page,
        # BabelDOC v0.5.1 new options
        merge_alternating_line_numbers=not settings.pdf.no_merge_alternating_line_numbers,
        remove_non_formula_lines=not settings.pdf.no_remove_non_formula_lines,
        non_formula_line_iou_threshold=settings.pdf.non_formula_line_iou_threshold,
        figure_table_protection_threshold=settings.pdf.figure_table_protection_threshold,
        skip_formula_offset_calculation=settings.pdf.skip_formula_offset_calculation,
        # Term extraction translator (can be different from main translator)
        term_extraction_translator=term_extraction_translator,
        term_pool_max_workers=settings.translation.term_pool_max_workers,
    )
    return babeldoc_config
