from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import importlib
import importlib.metadata
import json
import logging
import multiprocessing
import os
import queue
import re
import shutil
import signal
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import truststore

# Initialize before HTTP libraries cache SSLContext. Keep this in the application
# so wheel reinstalls cannot remove it by regenerating the console script.
truststore.inject_into_ssl()

from flask import Flask, Response, jsonify, request, send_file, stream_with_context
from werkzeug.exceptions import HTTPException
from pdf2zh_next_service import require_supported_service
from pdf2zh_next_service import diagnose_service_error
from pdf2zh_next_service import translate_pdf_with_callbacks
from pdf2zh_next_service import validate_service_config
from task_manager import TaskManager, TaskStoreError, TaskPersistenceError
from maintenance import install_routes, control_token
from translation_memory import TranslationMemory
from text_translation import TextTranslationService, TextTranslationError
from codex_client import close_codex_clients, get_codex_client, CodexError
from chatgpt_client import close_chatgpt_clients, get_chatgpt_client, ChatGPTError
from chatgpt_routes import install_chatgpt_routes
from codex_discovery import discovery_request, cancel_discovery
from provider_models import ModelDiscoveryError, list_provider_models, list_codex_models
from babeldoc.glossary_options import normalize_glossary_entries
from glossary_manager import GlossaryError, GlossaryManager
from diagnostics import exception_frames

VERSION = "1.9.1"
LOGGER = logging.getLogger("zotero_pdf2zh_server")
DEFAULT_TRANSLATES_DIR = Path(__file__).resolve().parent / "translates"
TRANSLATES_DIR = Path(
    os.getenv("PDF2ZH_DATA_DIR", str(DEFAULT_TRANSLATES_DIR))
).expanduser().resolve()
_IS_TRANSLATION_CHILD = multiprocessing.current_process().name != "MainProcess"
TASK_MANAGER = None if _IS_TRANSLATION_CHILD else TaskManager(TRANSLATES_DIR / "tasks.json")
GLOSSARY_MANAGER = None if _IS_TRANSLATION_CHILD else GlossaryManager(TRANSLATES_DIR / "glossaries")
TEXT_TRANSLATOR = None if _IS_TRANSLATION_CHILD else TextTranslationService()
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 3
MAX_TASK_PDF_BYTES = 512 * 1024 * 1024


class RequestValidationError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.public_message = message


@dataclass(frozen=True)
class PreparedTranslationRequest:
    file_name: str
    service: str
    output_modes: list[str]
    request_payload: dict[str, Any]


def create_app() -> Flask:
    app = Flask(__name__)

    @app.errorhandler(TaskStoreError)
    def task_store_error(_error):
        return error_response(TaskStoreError.public_message, 503,
                              code="task_store_unavailable", taskStore=TASK_MANAGER.task_store_health())

    @app.errorhandler(TaskPersistenceError)
    def task_persistence_error(_error):
        return error_response(TaskPersistenceError.public_message, 503,
                              code="task_persistence_failed", taskStore=TASK_MANAGER.task_store_health())

    @app.before_request
    def protect_task_store():
        if request.path == "/tasks" or request.path.startswith("/tasks/"):
            TASK_MANAGER.require_task_store()
    install_routes(app, runtime_busy, lambda: TRANSLATES_DIR)
    install_chatgpt_routes(app, runtime_busy)

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            return error
        return exception_response(error, 500)

    @app.get("/health")
    def health() -> tuple[dict[str, Any], int]:
        return build_health_payload(), 200

    @app.post("/translate-text")
    def translate_text():
        try:
            result = TEXT_TRANSLATOR.translate(request.get_json(silent=True),
                TranslationMemory(TRANSLATES_DIR / "translation-memory.sqlite3"))
            return jsonify(result)
        except TextTranslationError as error:
            payload = {"status": "error", "code": error.code}
            if error.message:
                payload["message"] = error.message
            return jsonify(payload), error.status
        except Exception:
            return jsonify({"status": "error", "code": "selection_unavailable"}), 503

    @app.post("/selection-capabilities")
    def selection_capabilities():
        return jsonify({'selectionLearning': True, 'codexProxy': True, 'selectionStream': True, 'chatgptPlan': True})

    @app.post("/translate-text/stream")
    def translate_text_stream():
        try:
            events = TEXT_TRANSLATOR.stream(request.get_json(silent=True),
                TranslationMemory(TRANSLATES_DIR / "translation-memory.sqlite3"))
            return Response(stream_with_context(events), mimetype='text/event-stream',
                headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
        except TextTranslationError as error:
            return jsonify({'status': 'error', 'code': error.code}), error.status
        except Exception:
            return jsonify({'status': 'error', 'code': 'selection_unavailable'}), 503

    @app.post("/cancel-text")
    def cancel_text():
        data = request.get_json(silent=True)
        try:
            if not isinstance(data, dict):
                raise TextTranslationError("invalid_request_id")
            return jsonify(TEXT_TRANSLATOR.cancel(data.get("requestId")))
        except TextTranslationError as error:
            return jsonify({"status": "error", "code": error.code}), error.status

    @app.post("/selection-cache/clear")
    def clear_selection_cache():
        try:
            TEXT_TRANSLATOR.clear_cache(TranslationMemory(TRANSLATES_DIR / "translation-memory.sqlite3"))
            return jsonify({'status': 'ok'})
        except Exception:
            return jsonify({'status': 'error', 'code': 'cache_unavailable'}), 503

    @app.post("/translation-lookup")
    def translation_lookup():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error_response("Expected a JSON object", 400)
        fingerprint, text = data.get("documentFingerprint"), data.get("text")
        page, side = data.get("page"), data.get("side", "source")
        target = data.get("targetLang")
        if (not isinstance(fingerprint, str) or not fingerprint.strip() or len(fingerprint) > 128
                or not isinstance(text, str) or not text.strip() or len(text) > 20000
                or (page is not None and (type(page) is not int or page < 1))
                or side not in ("source", "translation")
                or (target is not None and (not isinstance(target, str) or not target.strip() or len(target) > 32))):
            return error_response("Invalid fingerprint, text, page, side or targetLang", 400)
        try:
            result = TranslationMemory(TRANSLATES_DIR / "translation-memory.sqlite3").lookup(
                fingerprint, text, page=page, side=side, target_lang=target)
            return jsonify(result)
        except Exception:
            LOGGER.warning("Translation memory lookup unavailable")
            return error_response("Translation memory unavailable", 503)

    @app.get("/diagnostics")
    def diagnostics():
        payload = TASK_MANAGER.export_diagnostics()
        payload["environment"].update(serviceVersion=VERSION, babeldocVersion=package_version("babeldoc"), pdf2zhVersion=package_version("pdf2zh_next"))
        return jsonify(payload)

    @app.get("/tasks/<task_id>/diagnostics")
    def task_diagnostics(task_id):
        payload = TASK_MANAGER.export_diagnostics(task_id)
        if payload is None:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        payload["environment"].update(serviceVersion=VERSION, babeldocVersion=package_version("babeldoc"), pdf2zhVersion=package_version("pdf2zh_next"))
        return jsonify(payload)

    @app.get("/glossaries")
    def glossaries():
        try:
            return jsonify({"status": "ok", "packs": GLOSSARY_MANAGER.list_packs()}), 200
        except (GlossaryError, OSError) as exc:
            return exception_response(exc, getattr(exc, "status", 500))

    @app.post("/glossaries/check-updates")
    def check_glossary_updates():
        try:
            return jsonify({"status": "ok", "packs": GLOSSARY_MANAGER.refresh_catalog()}), 200
        except GlossaryError as exc:
            return exception_response(exc, exc.status)

    @app.post("/glossaries/<pack_id>/download")
    def download_glossary(pack_id):
        data = request.get_json(silent=True)
        if data is None and not request.data:
            data = {}
        if (not isinstance(data, dict) or set(data) - {"version"}
                or ("version" in data and not isinstance(data["version"], str))):
            return error_response("Expected a JSON body with an optional version", 400)
        try:
            pack = GLOSSARY_MANAGER.download(pack_id, data.get("version"))
            return jsonify({"status": "ok", "pack": pack}), 202
        except (GlossaryError, OSError) as exc:
            return exception_response(exc, getattr(exc, "status", 500))

    @app.post("/glossaries/<pack_id>/cancel")
    def cancel_glossary_download(pack_id):
        try:
            return jsonify({"status": "ok", "pack": GLOSSARY_MANAGER.cancel(pack_id)}), 200
        except (GlossaryError, OSError) as exc:
            return exception_response(exc, getattr(exc, "status", 500))

    @app.delete("/glossaries/<pack_id>")
    def uninstall_glossary(pack_id):
        try:
            return jsonify({"status": "ok", "pack": GLOSSARY_MANAGER.uninstall(pack_id)}), 200
        except (GlossaryError, OSError) as exc:
            return exception_response(exc, getattr(exc, "status", 500))

    @app.post("/codex/discover")
    def discover_codex():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get("force", False)) is not bool:
            return error_response("Expected a JSON body with an optional boolean force", 400)
        try:
            with discovery_request(data.get("requestId")) as check:
                client = get_codex_client(data.get("cliPath"), proxy_mode=data.get("proxyMode"),
                                          proxy_url=data.get("proxyUrl"))
                runtime = client.discover(force=data.get("force", False), check_cancelled=check)
                return jsonify({"status": "ok", "codexRuntime": runtime})
        except CodexError as error:
            return exception_response(error, error.status_code, code=error.code,
                                  attempts=getattr(error, "attempts", []))

    @app.post("/codex/discover/cancel")
    def cancel_codex_discovery():
        data = request.get_json(silent=True)
        try:
            cancel_discovery(data.get("requestId") if isinstance(data, dict) else None)
            return jsonify({"status": "ok"})
        except CodexError as error:
            return exception_response(error, error.status_code, code=error.code)

    @app.post("/list-models")
    def list_models():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error_response("Expected a JSON body", 400)
        try:
            if data.get("service") == "chatgpt":
                details = get_chatgpt_client(data.get("accountId")).model_catalog()
                return jsonify(status="ok", models=[m["id"] for m in details], modelDetails=details)
            if data.get("service") == "codex":
                details, runtime = list_codex_models(data, include_runtime=True)
                return jsonify({"status": "ok", "models": [model["id"] for model in details],
                                "modelDetails": details, "codexRuntime": runtime}), 200
            models = list_provider_models(data)
        except ChatGPTError as exc:
            return exception_response(exc, exc.status_code, code=exc.code)
        except ModelDiscoveryError as exc:
            return exception_response(exc, exc.status, **({"code": exc.code, "attempts": exc.attempts or []} if exc.code else {}))
        return jsonify({"status": "ok", "models": models}), 200

    @app.post("/translate")
    def translate():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error_response("Expected a JSON body", 400)

        try:
            pdf_bytes, filename, output_mode = translate_pdf_request(data)
        except RequestValidationError as exc:
            return exception_response(exc, 400)
        except RuntimeError as exc:
            return exception_response(exc, 502)
        except Exception as exc:
            return exception_response(exc, 500)

        response = send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
        response.headers["X-PDF2ZH-Output-Mode"] = output_mode
        response.headers["X-PDF2ZH-Version"] = VERSION
        return response

    @app.post("/validate-config")
    def validate_config():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error_response("Expected a JSON body", 400)

        try:
            with discovery_request(data.get("requestId") if data.get("service") == "codex" else None) as check:
                result = validate_config_request(data, check_cancelled=check)
        except RequestValidationError as exc:
            return exception_response(exc, 400)
        except (CodexError, ChatGPTError) as exc:
            return exception_response(exc, exc.status_code, code=exc.code,
                                  attempts=getattr(exc, "attempts", []))
        except RuntimeError as exc:
            return exception_response(exc, 502)
        except Exception as exc:
            return exception_response(exc, 500)

        return (
            jsonify(
                {
                    "status": result.status,
                    "service": result.service,
                    "model": result.model,
                    "diagnostics": result.diagnostics,
                    "liveTest": result.live_test,
                    "resolvedProtocol": result.resolved_protocol,
                    **({"codexRuntime": result.codex_runtime} if getattr(result, "codex_runtime", None) else {}),
                }
            ),
            200,
        )

    @app.route("/tasks", methods=["GET", "POST"])
    def tasks():
        if request.method == "GET":
            if request.args.get("view") == "dashboard":
                try:
                    return jsonify({"status": "ok", **TASK_MANAGER.list_tasks_page(
                        limit=int(request.args.get("limit", 30)), cursor=request.args.get("cursor"))})
                except ValueError as exc:
                    return exception_response(exc, 400)
            return jsonify({"status": "ok", **TASK_MANAGER.list_tasks_snapshot()}), 200

        upload = None
        if request.mimetype == "multipart/form-data":
            # Werkzeug spools files to disk; keep only bounded metadata in memory.
            request.max_content_length = MAX_TASK_PDF_BYTES + 2 * 1024 * 1024
            request.max_form_memory_size = 1024 * 1024
            request.max_form_parts = 4
            try:
                metadata = request.form.get("metadata", "")
                if len(metadata.encode("utf-8")) > 1024 * 1024:
                    return error_response("Upload metadata is too large", 413)
                data = json.loads(metadata)
            except (ValueError, TypeError):
                return error_response("Expected JSON upload metadata", 400)
            upload = request.files.get("file")
            if (not isinstance(data, dict) or upload is None or len(request.files.getlist("file")) != 1
                    or len(request.files) != 1 or len(request.form) != 1
                    or len(request.form.getlist("metadata")) != 1 or "fileContent" in data):
                return error_response("Expected one PDF file and metadata", 400)
        else:
            data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error_response("Expected a JSON body", 400)

        task_id = uuid.uuid4().hex[:12]
        workspace_dir: Path | None = None
        try:
            request_id = data.get("requestId")
            dedup_scope = data.get("dedupScope", "")
            if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", request_id)):
                raise RequestValidationError("Invalid submission requestId")
            if not isinstance(dedup_scope, str) or len(dedup_scope) > 256:
                raise RequestValidationError("Invalid dedupScope")
            workspace_dir = create_workspace_dir(task_id)
            prepared = prepare_translation_request(data, workspace_dir, upload=upload)
            task = TASK_MANAGER.create_task(
                task_id=task_id,
                file_name=prepared.file_name,
                service=prepared.service,
                output_modes=prepared.output_modes,
                request_payload=prepared.request_payload,
                workspace_dir=workspace_dir,
                request_id=request_id,
                dedup_scope=dedup_scope,
            )
            if task["taskId"] != task_id:
                remove_workspace_dir(workspace_dir)
        except TaskStoreError as exc:
            remove_workspace_dir(workspace_dir)
            return task_store_error(exc)
        except TaskPersistenceError as exc:
            remove_workspace_dir(workspace_dir)
            return task_persistence_error(exc)
        except RequestValidationError as exc:
            remove_workspace_dir(workspace_dir)
            return exception_response(exc, 400)
        except ValueError as exc:
            remove_workspace_dir(workspace_dir)
            return exception_response(exc, 409)
        except Exception as exc:
            remove_workspace_dir(workspace_dir)
            return exception_response(exc, 500)

        return jsonify(task_response_payload(task)), 202

    @app.post("/tasks/query")
    def task_query():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return error_response("Expected a JSON body", 400)
        try:
            return jsonify({"status": "ok", **TASK_MANAGER.list_tasks_page(
                limit=data.get("limit", 30), cursor=data.get("cursor"), tracked=data.get("tracked"))})
        except ValueError as exc:
            return exception_response(exc, 400, code="invalid_task_page")

    @app.get("/tasks/<task_id>")
    def task_detail(task_id: str):
        task = TASK_MANAGER.get_task(task_id)
        if task is None:
            return error_response("Task not found", 404)
        return jsonify(task_response_payload(task)), 200

    @app.delete("/tasks/<task_id>")
    def delete_task(task_id: str):
        try:
            task = TASK_MANAGER.delete_task(task_id)
        except ValueError as exc:
            return exception_response(exc, 409)
        if task is None:
            return error_response("Task not found", 404)
        return jsonify(task_response_payload(task)), 200

    @app.post("/tasks/<task_id>/cancel")
    def cancel_task(task_id: str):
        task = TASK_MANAGER.cancel_task(task_id)
        if task is None:
            return error_response("Task not found", 404)
        return jsonify(task_response_payload(task)), 200

    @app.post("/tasks/<task_id>/retry")
    def retry_task(task_id: str):
        try:
            task = TASK_MANAGER.retry_task(task_id)
        except ValueError as exc:
            return exception_response(exc, 409)
        if task is None:
            return error_response("Task not found", 404)
        return jsonify(task_response_payload(task)), 202

    @app.post("/tasks/<task_id>/repair")
    def repair_task(task_id: str):
        try:
            task = TASK_MANAGER.repair_task(task_id)
        except ValueError as exc:
            return exception_response(exc, 409)
        if task is None:
            return error_response("Task not found", 404)
        return jsonify(task_response_payload(task)), 202

    @app.post("/tasks/<task_id>/resume")
    def resume_task(task_id: str):
        try:
            task = TASK_MANAGER.resume_task(task_id)
        except ValueError as exc:
            return exception_response(exc, 409)
        if task is None:
            return error_response("Task not found", 404)
        return jsonify(task_response_payload(task)), 202

    @app.post("/tasks/<task_id>/retry-memory-sync")
    def retry_memory_sync(task_id: str):
        try:
            task = TASK_MANAGER.retry_memory_sync(task_id)
        except ValueError as exc:
            return exception_response(exc, 409)
        if task is None:
            return error_response("Task not found", 404)
        return jsonify(task_response_payload(task)), 202

    @app.post("/tasks/clear-failed")
    def clear_failed_tasks():
        deleted_count = TASK_MANAGER.clear_failed_tasks()
        return jsonify({
            "status": "ok", "deletedCount": deleted_count, **TASK_MANAGER.sync_metadata()
        }), 200

    @app.get("/tasks/events")
    def task_events():
        manager = TASK_MANAGER
        subscription = manager.subscribe(active_only=request.args.get("snapshot") == "active")

        @stream_with_context
        def generate():
            try:
                # Flush the SSE response immediately so clients can transition
                # from "connecting" even when there are no task snapshots yet.
                yield ": connected\n\n"
                for event in subscription.initial_events:
                    resync = manager.subscription_resync(subscription)
                    if resync is not None:
                        yield f"data: {json.dumps(resync, ensure_ascii=False)}\n\n"
                        return
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                subscription.initial_events.clear()
                while True:
                    try:
                        event = manager.next_subscription_event(subscription, timeout=15)
                    except queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    payload = json.dumps(event, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                    if event["type"] == "resync":
                        return
            finally:
                manager.unsubscribe(subscription)

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/tasks/<task_id>/result")
    def task_result(task_id: str):
        requested_mode = request.args.get("mode")
        if requested_mode is not None:
            try:
                requested_mode = normalize_output_mode_value(requested_mode)
            except RequestValidationError as exc:
                return exception_response(exc, 400)

        result = TASK_MANAGER.get_result_file(task_id, requested_mode)
        if result is None:
            return error_response("Task not found", 404)

        task_record, result_file = result
        if task_record.status not in {"completed", "incomplete"}:
            return error_response("Task result is not ready", 409)
        if result_file is None:
            if requested_mode is None and len(task_record.result_files) > 1:
                return error_response("Output mode is required when multiple result files exist", 400)
            return error_response("Requested PDF is unavailable or no longer exists", 404)

        response = send_file(
            result_file.output_path,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=result_file.filename,
        )
        response.headers["X-PDF2ZH-Output-Mode"] = result_file.output_mode
        response.headers["X-PDF2ZH-Version"] = VERSION
        response.headers["X-PDF2ZH-Task-Id"] = task_record.task_id
        response.headers["X-PDF2ZH-Server-Instance-Id"] = task_record.server_instance_id
        response.headers["X-PDF2ZH-Revision"] = str(task_record.revision)
        return response

    return app


def translate_pdf_request(data: dict[str, Any]) -> tuple[bytes, str, str]:
    job_id = f"direct-{uuid.uuid4().hex[:12]}"
    workspace_dir: Path | None = None
    try:
        workspace_dir = create_workspace_dir(job_id)
        prepared = prepare_translation_request(data, workspace_dir)
        if len(prepared.output_modes) != 1:
            raise RequestValidationError(
                "/translate accepts exactly one output mode; use /tasks for multiple outputs"
            )

        LOGGER.info(
            "[%s] accepted request: file=%s service=%s output_modes=%s",
            job_id,
            prepared.file_name,
            prepared.service,
            ",".join(prepared.output_modes),
        )
        result = asyncio.run(
            translate_pdf_with_callbacks(prepared.request_payload, job_id)
        )
        output_mode = prepared.output_modes[0]
        output_file = result.files[output_mode]
        return output_file.output_path.read_bytes(), output_file.filename, output_mode
    except Exception:
        remove_workspace_dir(workspace_dir)
        raise


def validate_config_request(data: dict[str, Any], *, check_cancelled=None):
    job_id = os.urandom(4).hex()
    service = normalize_service(data.get("service"))
    request_payload = {
        "source_lang": normalize_language(data.get("sourceLang"), "en"),
        "target_lang": normalize_language(data.get("targetLang"), "zh-CN"),
        "service": service,
        "qps": parse_int(data.get("qps"), 1, minimum=1),
        "pool_size": parse_int(data.get("poolSize"), 50, minimum=0),
        "ocr": parse_bool(data.get("ocr"), False),
        "auto_ocr": parse_bool(data.get("autoOcr"), True),
        "translate_table_text": parse_bool(
            data.get("translateTableText"), True
        ),
        "skip_references": parse_bool(data.get("skipReferences"), False),
        "skip_text_checks": parse_bool(data.get("skipTextChecks"), False),
        "no_watermark": parse_bool(data.get("noWatermark"), True),
        "no_auto_extract_glossary": parse_bool(
            data.get("disableTermExtraction"), True
        ),
        "font_family": normalize_font_family(data.get("fontFamily")),
        "live_test": parse_bool(data.get("liveTest"), False),
        "llm_api": data.get("llm_api") or {},
        **quality_request_options(data),
    }
    LOGGER.info("[%s] checking config: service=%s", job_id, service)
    return validate_service_config(request_payload, job_id, **({"check_cancelled": check_cancelled} if check_cancelled else {}))


def prepare_translation_request(
    data: dict[str, Any],
    workspace_dir: Path,
    *, upload=None,
) -> PreparedTranslationRequest:
    file_bytes = decode_pdf_content(data.get("fileContent")) if upload is None else None
    file_name = sanitize_pdf_filename(data.get("fileName"))
    service = normalize_service(data.get("service"))
    output_modes = normalize_output_modes(data)
    workspace_dir = workspace_dir.resolve()
    root_prefix = str(workspace_dir) + os.sep
    input_name = os.path.normpath(os.path.join(str(workspace_dir), file_name))
    if not input_name.startswith(root_prefix):
        raise RequestValidationError("Invalid PDF filename")
    input_path = Path(input_name)
    if input_path.parent != workspace_dir or input_path.is_symlink():
        raise RequestValidationError("Invalid PDF filename")
    resolved_name = os.path.realpath(input_name)
    if not resolved_name.startswith(root_prefix):
        raise RequestValidationError("Invalid PDF filename")
    if Path(resolved_name).parent != workspace_dir:
        raise RequestValidationError("Invalid PDF filename")
    output_dir = workspace_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with input_path.open("xb") as output:
            if upload is None:
                output.write(file_bytes)
            else:
                size = 0
                while chunk := upload.stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_TASK_PDF_BYTES:
                        raise RequestValidationError("PDF upload exceeds 512 MiB")
                    output.write(chunk)
                if not size:
                    raise RequestValidationError("PDF upload is empty")
    except FileExistsError:
        raise RequestValidationError("PDF input file already exists") from None

    request_payload = {
        "source_lang": normalize_language(data.get("sourceLang"), "en"),
        "target_lang": normalize_language(data.get("targetLang"), "zh-CN"),
        "output_modes": output_modes,
        "service": service,
        "qps": parse_int(data.get("qps"), 8, minimum=1),
        "pool_size": parse_int(data.get("poolSize"), 50, minimum=0),
        "skip_last_pages": parse_int(data.get("skipLastPages"), 0, minimum=0),
        "ocr": parse_bool(data.get("ocr"), False),
        "auto_ocr": parse_bool(data.get("autoOcr"), True),
        "translate_table_text": parse_bool(
            data.get("translateTableText"), True
        ),
        "skip_references": parse_bool(data.get("skipReferences"), False),
        "skip_text_checks": parse_bool(data.get("skipTextChecks"), False),
        "no_watermark": parse_bool(data.get("noWatermark"), True),
        "no_auto_extract_glossary": parse_bool(
            data.get("disableTermExtraction"), True
        ),
        "font_family": normalize_font_family(data.get("fontFamily")),
        "llm_api": data.get("llm_api") or {},
        **quality_request_options(data),
        "input_path": str(input_path),
        "output_dir": str(output_dir),
        "translation_memory_path": str(TRANSLATES_DIR / "translation-memory.sqlite3"),
    }
    return PreparedTranslationRequest(
        file_name=file_name,
        service=service,
        output_modes=output_modes,
        request_payload=request_payload,
    )


def quality_request_options(data: dict[str, Any]) -> dict[str, Any]:
    try:
        entries = normalize_glossary_entries(data.get("glossaryEntries"))
        metadata = []
        if "glossaryPacks" in data:
            if not isinstance(data["glossaryPacks"], list):
                raise ValueError("glossaryPacks must be an array")
            if data["glossaryPacks"]:
                entries, metadata = GLOSSARY_MANAGER.task_snapshot(
                    data["glossaryPacks"], entries,
                    normalize_language(data.get("sourceLang"), "en"),
                    normalize_language(data.get("targetLang"), "zh-CN"),
                )
    except ValueError as error:
        raise RequestValidationError(str(error)) from None
    return {
        "glossary_entries": entries,
        "semantic_review": parse_bool(data.get("semanticReview"), False),
        **({"glossary_packs": metadata} if metadata else {}),
    }


def create_workspace_dir(job_id: str) -> Path:
    TRANSLATES_DIR.mkdir(parents=True, exist_ok=True)
    workspace_dir = TRANSLATES_DIR / job_id
    workspace_dir.mkdir(parents=True, exist_ok=False)
    LOGGER.info("[%s] workspace ready: %s", job_id, workspace_dir)
    return workspace_dir


def remove_workspace_dir(workspace_dir: Path | None) -> None:
    if workspace_dir is None:
        return
    shutil.rmtree(workspace_dir, ignore_errors=True)


def decode_pdf_content(file_content: Any) -> bytes:
    if not isinstance(file_content, str) or not file_content.strip():
        raise RequestValidationError("fileContent is required")

    payload = file_content.strip()
    if payload.startswith("data:application/pdf;base64,"):
        payload = payload.split(",", 1)[1]

    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RequestValidationError("fileContent is not valid base64 PDF data") from exc


def sanitize_pdf_filename(file_name: Any) -> str:
    if not isinstance(file_name, str) or not file_name.strip():
        return "document.pdf"

    if "\x00" in file_name:
        raise RequestValidationError("Invalid PDF filename")
    sanitized = file_name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    if (not sanitized or sanitized in {".", ".."} or ":" in sanitized
            or any(char in sanitized for char in '<>"|?*')
            or any(ord(char) < 32 for char in sanitized)
            or sanitized.endswith((" ", "."))
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", sanitized)):
        raise RequestValidationError("Invalid PDF filename")
    if not sanitized.lower().endswith(".pdf"):
        sanitized += ".pdf"
    return sanitized

def normalize_output_modes(data: dict[str, Any]) -> list[str]:
    output_modes = data.get("outputModes")
    if output_modes is None:
        return [normalize_single_output_mode(data)]

    if not isinstance(output_modes, list):
        raise RequestValidationError(
            "outputModes must be a list containing 'mono' and/or 'dual'"
        )

    normalized_modes: list[str] = []
    for value in output_modes:
        mode = normalize_output_mode_value(value)
        if mode not in normalized_modes:
            normalized_modes.append(mode)

    if not normalized_modes:
        raise RequestValidationError(
            "outputModes must contain at least one of 'mono' or 'dual'"
        )
    return normalized_modes


def normalize_single_output_mode(data: dict[str, Any]) -> str:
    output_mode = data.get("outputMode") or "dual"
    return normalize_output_mode_value(output_mode)

 
def normalize_output_mode_value(output_mode: Any) -> str:
    if not isinstance(output_mode, str):
        raise RequestValidationError("outputMode must be 'mono' or 'dual'")

    normalized = output_mode.strip().lower()
    if normalized not in {"mono", "dual"}:
        raise RequestValidationError("outputMode must be 'mono' or 'dual'")
    return normalized


def normalize_service(service: Any) -> str:
    try:
        return require_supported_service(service)
    except ValueError as exc:
        raise RequestValidationError("Unsupported translation service") from exc


def normalize_language(value: Any, default: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip()


def normalize_font_family(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized in {"auto", "serif", "sans-serif", "script"}:
        return normalized
    return None


def parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


def parse_int(value: Any, default: int, minimum: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(parsed, minimum)


def task_response_payload(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok", "task": task,
        "serverInstanceId": task["serverInstanceId"], "revision": task["revision"],
    }


def exception_response(error: Exception, status_code: int, **details):
    """Only explicit business messages or fixed diagnoses cross the HTTP boundary."""
    if isinstance(error, (RequestValidationError, GlossaryError, CodexError, ChatGPTError, ModelDiscoveryError)):
        message = error.public_message
    else:
        diagnoses = diagnose_service_error(error)
        details.setdefault("diagnostics", diagnoses)
        message = next((row["message"] for row in diagnoses if row["code"] != "unknown_error"),
                       "The server could not complete this request. Check server logs with the error ID.")
        if status_code < 500:
            message = "The requested operation is not available in the current state."
    if status_code >= 500 or not isinstance(error, (RequestValidationError, GlossaryError, CodexError, ChatGPTError, ModelDiscoveryError)):
        error_id = uuid.uuid4().hex
        frames = exception_frames(error)
        LOGGER.error("request failed errorId=%s errorType=%s frames=%s", error_id, type(error).__name__, frames)
        TASK_MANAGER.diagnostics.record("request_error", {"errorId": error_id, "errorType": type(error).__name__}, frames=frames)
        details["errorId"] = error_id
    return error_response(message, status_code, **details)


def error_response(message: str, status_code: int, **details):
    metadata = TASK_MANAGER.sync_metadata() if request.path.startswith("/tasks") else {}
    return (
        jsonify(
            {
                "status": "error",
                "message": message,
                "diagnostics": diagnose_service_error(message),
                **metadata,
                **details,
            }
        ),
        status_code,
    )


def runtime_busy() -> bool:
    if build_task_stats().get("active", 0) or TASK_MANAGER._queue_blocked:
        return True
    if TEXT_TRANSLATOR.busy():
        return True
    with GLOSSARY_MANAGER._lock:
        return any(job.get("state") in {"downloading", "cancelling"}
                   for job in GLOSSARY_MANAGER._downloads.values())


def build_health_payload() -> dict[str, Any]:
    workspace = build_workspace_health()
    task_store = TASK_MANAGER.task_store_health()
    task_stats = build_task_stats()
    from flask import current_app, has_app_context
    gate = current_app.extensions.get("update_maintenance") if has_app_context() else None
    return {
        "status": "ok" if workspace.get("writable") and task_store["status"] == "ok" else "degraded",
        "version": VERSION,
        "pythonVersion": sys.version.split()[0],
        "supportedApiProtocols": ["auto", "chat_completions", "responses"],
        "capabilities": {"diagnosticsExport": True, "boundedCancellation": True, "detailedTaskProgress": True, "reasoningMode": True, "updateMaintenance": True, "glossaryEntries": True, "semanticReview": True, "glossaryPacks": True, "translationMemory": True, "textTranslation": True, "exactSelectionTranslation": True, "bingSelectionTranslation": True, "selectionLearning": True, "chatgptPlan": True, "codexCli": True, "codexProxy": True, "codexDiscovery": True, "taskSubmissionIdempotency": True, "taskRecovery": True, "taskTiming": True, "binaryTaskUpload": True, "taskPagination": True},
        "supportsModelDiscovery": True,
        "pdf2zhVersion": package_version("pdf2zh_next"),
        "babeldocVersion": package_version("babeldoc"),
        "workspace": workspace,
        "tasks": task_stats,
        "taskStore": task_store,
        "queueBlocked": TASK_MANAGER._queue_blocked,
        "maintenance": gate.snapshot() if gate else {"held": False, "busy": runtime_busy()},
    }


def build_workspace_health() -> dict[str, Any]:
    writable = False
    error: str | None = None
    free_bytes: int | None = None
    probe_path = TRANSLATES_DIR / ".healthcheck"
    try:
        TRANSLATES_DIR.mkdir(parents=True, exist_ok=True)
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink(missing_ok=True)
        writable = True
    except OSError as exc:
        error = str(exc)

    try:
        disk_path = TRANSLATES_DIR if TRANSLATES_DIR.exists() else TRANSLATES_DIR.parent
        free_bytes = shutil.disk_usage(disk_path).free
    except OSError as exc:
        error = str(exc) if error is None else f"{error}; {exc}"

    payload: dict[str, Any] = {
        "path": str(TRANSLATES_DIR),
        "writable": writable,
        "freeBytes": free_bytes,
    }
    if error:
        payload["error"] = error
    return payload


def build_task_stats() -> dict[str, int]:
    return TASK_MANAGER.task_stats()


def package_version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        try:
            module = importlib.import_module(package_name)
        except ImportError:
            return None
        version = getattr(module, "__version__", None)
        return str(version) if version is not None else None


app = None if _IS_TRANSLATION_CHILD else create_app()


def configure_runtime_paths(data_dir: str | Path | None = None) -> None:
    global TRANSLATES_DIR, TASK_MANAGER, GLOSSARY_MANAGER

    requested_dir = data_dir or os.getenv("PDF2ZH_DATA_DIR")
    TRANSLATES_DIR = (
        Path(requested_dir).expanduser().resolve()
        if requested_dir
        else DEFAULT_TRANSLATES_DIR
    )
    TASK_MANAGER.close()
    GLOSSARY_MANAGER.close()
    TASK_MANAGER = TaskManager(TRANSLATES_DIR / "tasks.json")
    GLOSSARY_MANAGER = GlossaryManager(TRANSLATES_DIR / "glossaries")
    try:
        GLOSSARY_MANAGER.list_packs()
    except OSError as error:
        LOGGER.warning("Could not initialize glossary storage: %s", error)


def configure_logging(
    level_name: str | None = None,
    log_file: str | Path | None = None,
) -> None:
    if level_name is None:
        level_name = os.getenv("PDF2ZH_LOG_LEVEL", "INFO")
    level_name = level_name.upper()
    level = getattr(logging, level_name, logging.INFO)

    from diagnostics import SafeLogHandler
    handlers: list[logging.Handler] = [logging.StreamHandler(), SafeLogHandler(TASK_MANAGER.diagnostics.record)]
    requested_log_file = log_file or os.getenv("PDF2ZH_LOG_FILE")
    if requested_log_file:
        log_path = Path(requested_log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_path,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        handlers=handlers,
        force=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the zotero-pdf2zh-pro server")
    parser.add_argument(
        "--host",
        default=os.getenv("PDF2ZH_HOST", "127.0.0.1"),
        help="Server host, default: %(default)s",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=parse_int(os.getenv("PDF2ZH_PORT"), 8890, minimum=1),
        help="Server port, default: %(default)s",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("PDF2ZH_LOG_LEVEL", "INFO"),
        help="Logging level, default: %(default)s",
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("PDF2ZH_DATA_DIR"),
        help="Persistent task and result directory",
    )
    parser.add_argument(
        "--log-file",
        default=os.getenv("PDF2ZH_LOG_FILE"),
        help="Optional rotating log file",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_runtime_paths(args.data_dir)
    # Resolve account storage after --data-dir and propagate it to spawned workers.
    os.environ["PDF2ZH_DATA_DIR"] = str(TRANSLATES_DIR)
    configure_logging(args.log_level, args.log_file)
    control_token(TRANSLATES_DIR)
    def cache_preflight():
        if os.name == "nt":
            import ctypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetCurrentThread.restype = ctypes.c_void_p
            kernel.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
            kernel.SetThreadPriority(kernel.GetCurrentThread(), -2)
        time.sleep(3)
        # Inspect existing files only; PDF engine loading/download behavior stays unchanged.
        from babeldoc.const import CACHE_FOLDER
        available = 0
        for folder in (CACHE_FOLDER / "models", CACHE_FOLDER / "fonts"):
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if runtime_busy():
                    return
                if path.is_file() and path.stat().st_size:
                    available += 1
                time.sleep(0.02)
        LOGGER.debug("existing model/font cache preflight: files=%s", available)
    threading.Thread(target=cache_preflight, name="cache-preflight", daemon=True).start()
    LOGGER.info("server starting on http://%s:%s", args.host, args.port)
    previous_sigterm = None
    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def stop_server(signum, _frame):
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, stop_server)
    try:
        app.run(host=args.host, port=args.port)
    finally:
        try:
            TEXT_TRANSLATOR.close()
        finally:
            try:
                close_codex_clients()
                close_chatgpt_clients()
            finally:
                try:
                    TASK_MANAGER.close()
                finally:
                    try:
                        GLOSSARY_MANAGER.close()
                    finally:
                        if previous_sigterm is not None:
                            signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    main()
