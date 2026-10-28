#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import re
import ssl
import sys

import server

import babeldoc
import observability
import pdf2zh_pro
import pymupdf
import openai
from packaging.version import Version
from babeldoc.format.pdf.new_parser.runtime.cmap_secure_loader import load_verified_cmap_data
from babeldoc.format.pdf.new_parser.native_parse import parse_prepared_pdf_with_new_parser_to_legacy_ir

import truststore
import task_manager
import glossary_manager
import glossary_catalog
import runtime_storage
import maintenance
from bitstring import BitStream

FORBIDDEN_DISTRIBUTIONS = {
    "babeldoc",
    "fastapi",
    "gradio",
    "gradio-i18n",
    "gradio-pdf",
    "legacy-cgi",
    "opencv-python",
    "pandas",
    "pdf2zh-next",
    "pydantic-settings",
    "rapidocr-onnxruntime",
    "ruff",
    "sse-starlette",
    "uvicorn",
}

SECURITY_MINIMUMS = {"pymupdf": "2.0", "pillow": "13.0.0",
                     "filelock": "4.2.1", "certifi": "2026.10.19", "urllib3": "2.8.0",
                     "werkzeug": "3.1.9", "onnx": "1.21.0", "cryptography": "46.0.7"}


def canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_installed_runtime.py <version>")
    expected_version = sys.argv[1]
    for package, minimum in SECURITY_MINIMUMS.items():
        if Version(importlib.metadata.version(package)) < Version(minimum):
            raise RuntimeError(f"Installed {package} is below the security minimum {minimum}")
    installed = {
        canonicalize_name(distribution.metadata["Name"])
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }
    unexpected = installed & FORBIDDEN_DISTRIBUTIONS
    if unexpected:
        raise RuntimeError(f"Forbidden distributions installed: {sorted(unexpected)}")

    distribution = importlib.metadata.distribution("zotero-pdf2zh-pro")
    if distribution.version != expected_version:
        raise RuntimeError("Installed zotero-pdf2zh-pro version mismatch")
    scripts = {
        entry_point.name: entry_point.value
        for entry_point in distribution.entry_points
        if entry_point.group == "console_scripts"
    }
    if scripts != {"zotero-pdf2zh-pro": "service_launcher:main"}:
        raise RuntimeError(f"Installed CLI entry point mismatch: {scripts}")
    if ssl.SSLContext is not truststore.SSLContext:
        raise RuntimeError("Server startup did not enable system certificate verification")
    if pdf2zh_pro.__version__ != expected_version:
        raise RuntimeError(f"Unexpected pdf2zh_pro version: {pdf2zh_pro.__version__}")
    if babeldoc.__version__ != "0.6.4":
        raise RuntimeError(f"Unexpected BabelDOC snapshot: {babeldoc.__version__}")
    if not callable(observability.empty_metrics):
        raise RuntimeError("Observability runtime is incomplete")
    if BitStream("0b1").read("uint:1") != 1:
        raise RuntimeError("Installed PDF bitstream dependency is incompatible")
    if Version(openai.__version__) < Version("2.32.0"):
        raise RuntimeError("Installed OpenAI SDK is too old for the dual-protocol runtime")
    if "cost" in observability.empty_metrics():
        raise RuntimeError("Runtime still exposes removed billing metrics")
    if not callable(task_manager.TaskManager):
        raise RuntimeError("Task manager runtime is incomplete")
    if not callable(glossary_manager.GlossaryManager) or len(glossary_catalog.GLOSSARY_CATALOG["packs"]) != 5:
        raise RuntimeError("Downloadable glossary runtime is incomplete")
    if not callable(runtime_storage.prepare_data_directory):
        raise RuntimeError("Persistent runtime storage is missing")

    health = server.build_health_payload()
    if health.get("capabilities", {}).get("structuredExport") is not True:
        raise RuntimeError("Runtime is missing structured document exports")
    if not callable(maintenance.MaintenanceGate) or health.get("capabilities", {}).get("updateMaintenance") is not True:
        raise RuntimeError("Runtime is missing authenticated update maintenance")
    if health.get("capabilities", {}).get("glossaryPacks") is not True:
        raise RuntimeError("Runtime is missing downloadable glossary capabilities")
    if health.get("supportedApiProtocols") != ["auto", "chat_completions", "responses"]:
        raise RuntimeError("Runtime is missing dual-protocol capabilities")
    if health["pdf2zhVersion"] != expected_version:
        raise RuntimeError(f"Health reports wrong pdf2zh version: {health}")
    if health["babeldocVersion"] != "0.6.4":
        raise RuntimeError(f"Health reports wrong BabelDOC version: {health}")

    if not load_verified_cmap_data("UniGB-UCS2-H"):
        raise RuntimeError("Native parser CMap resources are missing")
    if not callable(parse_prepared_pdf_with_new_parser_to_legacy_ir):
        raise RuntimeError("Native parser is missing")
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Installed PDF runtime")
        with pymupdf.open(stream=document.tobytes(), filetype="pdf") as reopened:
            if "Installed PDF runtime" not in reopened[0].get_text():
                raise RuntimeError("Installed PDF bindings cannot roundtrip text")
            if "Installed PDF runtime" not in reopened.to_markdown(use_ocr=False, show_progress=False):
                raise RuntimeError("Installed Layout/4LLM cannot export Markdown")
    print(
        f"validated installed runtime {expected_version}: "
        f"{len(installed)} distributions"
    )


if __name__ == "__main__":
    main()
