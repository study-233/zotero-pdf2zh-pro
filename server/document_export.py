"""Bounded, cancellable attachment exports using the existing process supervisor."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import uuid

from flask import Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from pdf2zh_pro.document import export_worker
from task_runtime import run_translation

EXPORT_TIMEOUT_SECONDS = 300
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
EXPORT_MIMETYPES = {"md": "text/markdown", "json": "application/json", "txt": "text/plain"}


def install_document_export_routes(app, directory, diagnostic_store, save_pdf_input):
    lock = threading.Lock()
    active = {}
    recent = OrderedDict()

    def remember(request_id):
        recent[request_id] = time.monotonic()
        while len(recent) > 128 or (recent and next(iter(recent.values())) < time.monotonic() - 600):
            recent.popitem(last=False)

    def error(code, status):
        return jsonify({"status": "error", "code": code}), status

    def valid_request_id(value):
        return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9-]{16,64}", value)

    @app.before_request
    def protect_export():
        if request.path.startswith("/document-exports"):
            # Browser pages cannot trigger PDF processing with a form submission.
            if request.headers.get("Origin") or request.headers.get("X-PDF2ZH-Client") != "zotero":
                return error("export_forbidden", 403)
            request.max_content_length = MAX_UPLOAD_BYTES + 1024 * 1024

    @app.post("/document-exports/<request_id>/cancel")
    def cancel_export(request_id):
        if not valid_request_id(request_id):
            return error("export_invalid_request", 400)
        with lock:
            remember(request_id)  # Also covers cancellation before the upload arrives.
            if request_id in active:
                active[request_id].set()
        return jsonify({"status": "ok"})

    @app.post("/document-exports")
    def export_attachment():
        request_id = request.headers.get("X-PDF2ZH-Request-ID")
        if not valid_request_id(request_id):
            return error("export_invalid_request", 400)
        cancel = threading.Event()
        with lock:
            if request_id in recent:
                return error("export_cancelled", 409)
            if active:
                return error("export_busy", 429)
            active[request_id] = cancel
        workspace = None
        response_owns_workspace = False
        expired = threading.Event()
        store = diagnostic_store()
        identity = {"taskId": "export-" + request_id, "attempt": 1,
                    "executionId": uuid.uuid4().hex, "serverInstanceId": "document-export"}

        def timeout():
            expired.set()
            cancel.set()

        timer = threading.Timer(EXPORT_TIMEOUT_SECONDS, timeout)
        timer.daemon = True
        timer.start()
        try:
            root = Path(directory())
            root.mkdir(parents=True, exist_ok=True)
            workspace = Path(tempfile.mkdtemp(prefix="export-", dir=root))
            upload = request.files.get("file")
            format = request.form.get("format")
            if upload is None or format not in EXPORT_MIMETYPES:
                return error("export_invalid_request", 400)
            try:
                _, source = save_pdf_input({"fileName": upload.filename}, workspace, upload=upload)
            except ValueError:
                return error("export_invalid_pdf", 400)
            if cancel.is_set():
                raise asyncio.CancelledError
            output = workspace / {"md": "export.md", "json": "export.json", "txt": "export.txt"}[format]
            store.record("document_export_started", identity)
            result = run_translation(
                {"input_path": str(source), "output_path": str(output), "format": format}, identity["taskId"],
                cancel_event=cancel, progress_callback=lambda _: None, metrics_callback=lambda _: None,
                runtime_callback=lambda _: None, store=store, identity=identity,
                lease_dir=root / "diagnostics", target=export_worker,
            )
            if cancel.is_set():
                raise asyncio.CancelledError
            if result.get("code") in {"export_invalid_pdf", "export_needs_ocr"}:
                store.record("document_export_failed", {**identity, "exportCode": result["code"]})
                return error(result["code"], 422)
            store.record("document_export_completed", {**identity, "totalPages": result["pages"]})

            def stream():
                try:
                    with output.open("rb") as archive:
                        while chunk := archive.read(64 * 1024):
                            yield chunk
                finally:
                    shutil.rmtree(workspace)

            response = Response(stream(), mimetype=EXPORT_MIMETYPES[format])
            response.headers["Content-Disposition"] = f'attachment; filename="document-export.{format}"'
            response.headers["Cache-Control"] = "no-store"
            # Covers a client disconnect before the iterable starts, too.
            response.call_on_close(lambda: shutil.rmtree(workspace, ignore_errors=True))
            response_owns_workspace = True
            return response
        except asyncio.CancelledError:
            store.record("document_export_cancelled", {**identity, "exportCode": "export_timeout" if expired.is_set() else "export_cancelled"})
            return error("export_timeout" if expired.is_set() else "export_cancelled", 504 if expired.is_set() else 409)
        except RequestEntityTooLarge:
            return error("export_too_large", 413)
        except Exception as exc:
            store.record("document_export_failed", {**identity, "exportCode": "export_failed", "errorType": type(exc).__name__})
            return error("export_failed", 500)
        finally:
            timer.cancel()
            with lock:
                active.pop(request_id, None)
                remember(request_id)
            if workspace is not None and not response_owns_workspace:
                shutil.rmtree(workspace, ignore_errors=True)
