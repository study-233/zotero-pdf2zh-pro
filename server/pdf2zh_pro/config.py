from __future__ import annotations

import enum
import json
from typing import Any
from pdf2zh_pro.translator.providers import TRANSLATION_ENGINE_METADATA, CodexSettings, ChatGPTSettings, OpenAISettings, SiliconFlowSettings
from pdf2zh_pro.translator.openai_protocol import normalize_endpoint, parse_request_options
import logging
import re
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field

from pdf2zh_pro.translator.providers import (
    TERM_EXTRACTION_ENGINE_SETTING_TYPE,
)
from pdf2zh_pro.translator.providers import TRANSLATION_ENGINE_METADATA_MAP
from pdf2zh_pro.translator.providers import TRANSLATION_ENGINE_SETTING_TYPE

log = logging.getLogger(__name__)

class WatermarkOutputMode(enum.Enum):
    """Watermark output mode for PDF files"""

    Watermarked = "watermarked"  # Add watermark to translated PDF
    NoWatermark = "no_watermark"  # Don't add watermark
    Both = "both"  # Output both watermarked and non-watermarked versions


class BasicSettings(BaseModel):
    debug: bool = False


class TranslationSettings(BaseModel):
    """Translation related settings"""

    min_text_length: int = Field(
        default=5, description="Minimum text length to translate"
    )
    rpc_doclayout: str | None = Field(
        default=None,
        description="RPC service host address for document layout analysis",
    )
    lang_in: str = Field(default="en", description="Source language code")
    lang_out: str = Field(default="zh", description="Target language code")
    output: str | None = Field(
        default=None, description="Output directory for translated files"
    )
    qps: int = Field(default=4, description="QPS limit for translation service")
    ignore_cache: bool = Field(default=False, description="Ignore translation cache")
    custom_system_prompt: str | None = Field(
        default=None,
        description='Custom system prompt for translation. It is mainly used to add the `/no_think` instruction of Qwen 3 in the prompt. e.g. --custom-system-prompt "/no_think You are a professional, authentic machine translation engine."',
    )
    glossaries: str | None = Field(
        default=None,
        description="Glossary file list.",
    )
    save_auto_extracted_glossary: bool = Field(
        default=False, description="save automatically extracted glossary"
    )
    pool_max_workers: int | None = Field(
        default=50,
        description="Maximum number of workers for translation pool",
    )
    term_qps: int | None = Field(
        default=None,
        description="QPS limit for term extraction translation service. If not set, will follow qps.",
    )
    term_pool_max_workers: int | None = Field(
        default=None,
        description="Maximum number of workers for term extraction translation pool. If not set or 0, will follow pool_max_workers.",
    )
    no_auto_extract_glossary: bool = Field(
        default=True,
        description="Disable auto extract glossary",
    )
    primary_font_family: str | None = Field(
        default=None,
        description="Override primary font family for translated text. Choices: 'serif' for serif fonts, 'sans-serif' for sans-serif fonts, 'script' for script/italic fonts. If not specified, uses automatic font selection based on original text properties.",
    )


class PDFSettings(BaseModel):
    """PDF processing settings"""

    pages: str | None = Field(
        default=None, description="Pages to translate (e.g. '1,2,1-,-3,3-5')"
    )
    no_dual: bool = Field(
        default=False, description="Do not output bilingual PDF files"
    )
    no_mono: bool = Field(
        default=False, description="Do not output monolingual PDF files"
    )
    formular_font_pattern: str | None = Field(
        default=None, description="Font pattern to identify formula text"
    )
    formular_char_pattern: str | None = Field(
        default=None, description="Character pattern to identify formula text"
    )
    split_short_lines: bool = Field(
        default=False, description="Force split short lines into different paragraphs"
    )
    short_line_split_factor: float = Field(
        default=0.8, description="Split threshold factor for short lines"
    )
    skip_clean: bool = Field(default=False, description="Skip PDF cleaning step")
    dual_translate_first: bool = Field(
        default=False, description="Put translated pages first in dual PDF mode"
    )
    disable_rich_text_translate: bool = Field(
        default=False, description="Disable rich text translation"
    )
    enhance_compatibility: bool = Field(
        default=False, description="Enable all compatibility enhancement options"
    )
    use_alternating_pages_dual: bool = Field(
        default=False, description="Use alternating pages mode for dual PDF"
    )
    watermark_output_mode: str = Field(
        default="watermarked",
        description="Watermark output mode for PDF files (watermarked, no_watermark, or both)",
    )
    max_pages_per_part: int | None = Field(
        default=None, description="Maximum pages per part for split translation"
    )
    translate_table_text: bool = Field(
        default=True, description="Translate table text (experimental)"
    )
    skip_references: bool = Field(
        default=False,
        description="Keep automatically detected reference paragraphs untranslated",
    )
    skip_scanned_detection: bool = Field(
        default=False, description="Skip scanned detection"
    )
    ocr_workaround: bool = Field(
        default=False,
        description="Force translated text to be black and add white background",
    )
    auto_enable_ocr_workaround: bool = Field(
        default=False,
        description="Enable automatic OCR workaround. If a document is detected as heavily scanned, this will attempt to enable OCR processing and skip further scan detection. See documentation for details. (default: False)",
    )
    only_include_translated_page: bool = Field(
        default=False,
        description="Only include translated pages in the output PDF. Effective only when --pages is used.",
    )
    no_merge_alternating_line_numbers: bool = Field(
        default=False,
        description="Handle alternating line numbers and text paragraphs in documents with line numbers",
    )
    no_remove_non_formula_lines: bool = Field(
        default=False,
        description="Remove non-formula lines within paragraph areas",
    )
    non_formula_line_iou_threshold: float = Field(
        default=0.9,
        description="IoU threshold for identifying non-formula lines",
    )
    figure_table_protection_threshold: float = Field(
        default=0.9,
        description="Protection threshold for figures and tables (lines within figures/tables will not be processed)",
    )
    skip_formula_offset_calculation: bool = Field(
        default=False,
        description="Skip formula offset calculation during processing",
    )


class SettingsModel(BaseModel):
    """Main settings class that combines all sub-settings"""

    report_interval: float = Field(
        default=0.1, description="Progress report interval in seconds"
    )
    basic: BasicSettings = Field(default_factory=BasicSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    pdf: PDFSettings = Field(default_factory=PDFSettings)
    translate_engine_settings: TRANSLATION_ENGINE_SETTING_TYPE | None = Field(
        default=None, description="Translation engine settings", discriminator="translate_engine_type"
    )
    term_extraction_engine_settings: TERM_EXTRACTION_ENGINE_SETTING_TYPE | None = Field(
        default=None,
        description="Term extraction translation engine settings",
        discriminator="translate_engine_type",
    )

    def clone(self) -> SettingsModel:
        return self.model_copy(deep=True)

    def get_output_dir(self) -> Path:
        """Get output directory, create if not exists"""
        if self.translation.output:
            output_dir = Path(self.translation.output)
        else:
            output_dir = Path.cwd()

        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def validate_settings(self) -> None:
        """Validate settings"""
        # Validate translation service selection

        if not self.translate_engine_settings:
            raise ValueError("Must provide a translation service")

        # Log the current translation engine being used
        engine_name = self.translate_engine_settings.translate_engine_type
        log.info(f"Using translation engine: {engine_name}")

        self.translate_engine_settings.validate_settings()
        if hasattr(self.translate_engine_settings, "transform"):
            from_type = self.translate_engine_settings.translate_engine_type
            self.translate_engine_settings = self.translate_engine_settings.transform()
            to_type = self.translate_engine_settings.translate_engine_type
            log.info(f"Transformed translate_engine_settings: {from_type} -> {to_type}")
            self.translate_engine_settings.validate_settings()

        # Validate and normalize term extraction engine settings
        main_engine_type = self.translate_engine_settings.translate_engine_type
        main_metadata = TRANSLATION_ENGINE_METADATA_MAP.get(main_engine_type)

        if self.term_extraction_engine_settings is not None:
            term_engine_type = (
                self.term_extraction_engine_settings.translate_engine_type
            )
            term_metadata = TRANSLATION_ENGINE_METADATA_MAP.get(term_engine_type)
            if not term_metadata or not term_metadata.support_llm:
                raise ValueError(
                    f"Term extraction engine {term_engine_type} must support LLM"
                )
            # Validate and transform term extraction engine if necessary
            if hasattr(self.term_extraction_engine_settings, "validate_settings"):
                self.term_extraction_engine_settings.validate_settings()
            if hasattr(self.term_extraction_engine_settings, "transform"):
                from_type = self.term_extraction_engine_settings.translate_engine_type
                self.term_extraction_engine_settings = (
                    self.term_extraction_engine_settings.transform()
                )
                to_type = self.term_extraction_engine_settings.translate_engine_type
                log.info(
                    f"Transformed term_extraction_engine_settings: {from_type} -> {to_type}"
                )
                if hasattr(self.term_extraction_engine_settings, "validate_settings"):
                    self.term_extraction_engine_settings.validate_settings()
        else:
            # Default behavior: follow main engine if it supports LLM, otherwise disable auto term extraction
            if main_metadata and main_metadata.support_llm:
                self.term_extraction_engine_settings = self.translate_engine_settings
            else:
                self.term_extraction_engine_settings = None
                if not self.translation.no_auto_extract_glossary:
                    self.translation.no_auto_extract_glossary = True
                    log.warning(
                        "Current translation engine does not support LLM, "
                        "automatic term extraction will be disabled."
                    )

        # Validate PDF output mode
        if self.pdf.no_dual and self.pdf.no_mono:
            raise ValueError("Cannot disable both dual and mono output modes")

        # Validate regex patterns
        if self.pdf.formular_font_pattern:
            try:
                re.compile(self.pdf.formular_font_pattern)
            except re.error as e:
                raise ValueError(f"Invalid formular_font_pattern: {e}") from e

        if self.pdf.formular_char_pattern:
            try:
                re.compile(self.pdf.formular_char_pattern)
            except re.error as e:
                raise ValueError(f"Invalid formular_char_pattern: {e}") from e

        if self.pdf.enhance_compatibility:
            self.pdf.skip_clean = True
            self.pdf.disable_rich_text_translate = True

        if self.pdf.max_pages_per_part and self.pdf.max_pages_per_part < 0:
            raise ValueError("max_pages_per_part must be greater than 0")

        # Validate and store watermark mode
        watermark_output_mode_maps = {
            "nowatermark": "no_watermark",
            "no_watermark": "no_watermark",
            "watermarked": "watermarked",
            "both": "both",
        }

        watermark_output_mode = self.pdf.watermark_output_mode.lower()
        if watermark_output_mode not in watermark_output_mode_maps:
            raise ValueError(
                f"Invalid watermark output mode: {watermark_output_mode}. "
                f"Valid modes: {', '.join(watermark_output_mode_maps.keys())}"
            )

        self.pdf.watermark_output_mode = watermark_output_mode_maps[
            watermark_output_mode
        ]

        if self.translation.qps < 1:
            raise ValueError("qps must be greater than 0")

        if self.translation.term_qps is not None and self.translation.term_qps < 1:
            raise ValueError("term_qps must be greater than 0")

        if (
            self.translation.term_pool_max_workers is not None
            and self.translation.term_pool_max_workers < 0
        ):
            raise ValueError("term_pool_max_workers must be greater than or equal to 0")

        if self.translation.min_text_length < 0:
            raise ValueError("min_text_length must be greater than or equal to 0")

        if self.report_interval < 0.05:
            raise ValueError("report_interval must be greater than or equal to 0.05")

        if self.pdf.split_short_lines and self.pdf.short_line_split_factor < 0.1:
            raise ValueError(
                "short_line_split_factor must be greater than or equal to 0.1"
            )

        if self.pdf.max_pages_per_part and self.pdf.max_pages_per_part < 50:
            raise ValueError("max_pages_per_part must be greater than or equal to 50")

        if (
            self.translation.primary_font_family
            and self.translation.primary_font_family
            not in ["serif", "sans-serif", "script"]
        ):
            raise ValueError(
                f"Invalid primary font family: {self.translation.primary_font_family}"
            )

        if not (0.0 <= self.pdf.non_formula_line_iou_threshold <= 1.0):
            raise ValueError(
                "non_formula_line_iou_threshold must be between 0.0 and 1.0"
            )

        if not (0.0 <= self.pdf.figure_table_protection_threshold <= 1.0):
            raise ValueError(
                "figure_table_protection_threshold must be between 0.0 and 1.0"
            )

        if self.pdf.auto_enable_ocr_workaround and self.pdf.ocr_workaround:
            self.pdf.ocr_workaround = False
            log.warning(
                "The system detection results will override the manually set OCR workaround."
            )

        if self.pdf.auto_enable_ocr_workaround and self.pdf.skip_scanned_detection:
            self.pdf.skip_scanned_detection = False
            log.warning(
                "After enabling automatic OCR Workaround, scan version detection will be forcibly enabled."
            )

    def parse_pages(self) -> list[tuple[int, int]] | None:
        """Parse pages string into list of page ranges"""
        if not self.pdf.pages:
            return None

        ranges: list[tuple[int, int]] = []
        try:
            for part in self.pdf.pages.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-")
                    try:
                        start_as_int = int(start) if start else 1
                        end_as_int = int(end) if end else -1
                        if start_as_int < 1 and start:
                            raise ValueError(f"Invalid start page number: {start}")
                        if end_as_int < -1:
                            raise ValueError(f"Invalid end page number: {end}")
                        if end_as_int != -1 and start_as_int > end_as_int:
                            raise ValueError(
                                f"Start page {start} is greater than end page {end}"
                            )
                        ranges.append((start_as_int, end_as_int))
                    except ValueError as e:
                        if "invalid literal for int()" in str(e):
                            raise ValueError(
                                f"Invalid page number format in range: {part}"
                            ) from e
                        raise
                else:
                    try:
                        page = int(part)
                        if page < 1:
                            raise ValueError(f"Invalid page number: {page}")
                        ranges.append((page, page))
                    except ValueError as e:
                        raise ValueError(f"Invalid page number format: {part}") from e
        except ValueError as e:
            raise ValueError(f"Error parsing pages parameter: {e}") from e

        return ranges


SERVICE_FIELD_MAP = {
    "chatgpt": {"model": "chatgpt_model", "reasoningEffort": "chatgpt_reasoning_effort", "accountId": "chatgpt_account_id"},
    "codex": {
        "model": "codex_model",
        "cliPath": "codex_cli_path",
        "proxyMode": "codex_proxy_mode",
        "proxyUrl": "codex_proxy_url",
        "reasoningEffort": "codex_reasoning_effort",
    },
    "openai": {
        "model": "openai_model",
        "apiKey": "openai_api_key",
        "apiUrl": "openai_base_url",
    },
    "aliyundashscope": {
        "model": "aliyun_dashscope_model",
        "apiKey": "aliyun_dashscope_api_key",
        "apiUrl": "aliyun_dashscope_base_url",
    },
    "deepseek": {
        "model": "deepseek_model",
        "apiKey": "deepseek_api_key",
    },
    "mimo": {
        "model": "mimo_model",
        "apiKey": "mimo_api_key",
        "apiUrl": "mimo_base_url",
    },
    "zhipu": {
        "model": "zhipu_model",
        "apiKey": "zhipu_api_key",
        "apiUrl": "zhipu_base_url",
    },
    "ollama": {
        "model": "ollama_model",
        "apiUrl": "ollama_host",
    },
    "siliconflow": {
        "model": "siliconflow_model",
        "apiKey": "siliconflow_api_key",
        "apiUrl": "siliconflow_base_url",
    },
    "gemini": {
        "model": "gemini_model",
        "apiKey": "gemini_api_key",
    },
    "claudecode": {
        "model": "claude_code_model",
        "apiUrl": "claude_code_path",
    },
    "deepl": {
        "apiKey": "deepl_auth_key",
    },
}


def coerce_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    lowered = stripped.lower()
    if lowered in {"", "null", "none"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return stripped


def require_supported_service(service: Any) -> str:
    if not isinstance(service, str):
        raise ValueError("请选择支持的接口类型。")
    normalized = service.strip().lower().replace("-", "").replace("_", "")
    if normalized in {"openaicompatible", "tencentmechinetranslation", "dify", "siliconflowfree", "grok", "groq", "modelscope", "qwenmt", "azureopenai", "azure", "xinference", "anythingllm"}:
        raise ValueError("此接口类型已移除，请重新选择并配置受支持的接口。")
    if normalized not in SERVICE_FIELD_MAP:
        raise ValueError("未知的接口类型，请重新选择受支持的接口。")
    return normalized


def build_service_detail(service: str, llm_api: dict[str, Any]) -> dict[str, Any]:
    service = require_supported_service(service)
    detail: dict[str, Any] = {}
    field_map = SERVICE_FIELD_MAP.get(service, {})

    for key, field_name in field_map.items():
        value = llm_api.get(key)
        # "none" is a real Codex reasoning effort, not an empty setting.
        if service in {"codex", "chatgpt"}:
            if value is not None and not isinstance(value, str):
                raise ValueError("账号通道配置字段必须为文本")
            value = value.strip() if isinstance(value, str) else value
        else:
            value = coerce_value(value)
        if value not in (None, ""):
            detail[field_name] = value

    if service == "chatgpt":
        if llm_api.get("extraData") or llm_api.get("apiKey") or llm_api.get("apiUrl") or llm_api.get("requestOptions"):
            raise ValueError("ChatGPT 套餐通道不接受 API Key、自定义地址或额外参数")
        return detail

    extra_data = llm_api.get("extraData") or {}
    if isinstance(extra_data, dict):
        for key, value in extra_data.items():
            normalized_key = str(key).strip().replace("-", "_")
            normalized_value = coerce_value(value)
            if normalized_key and normalized_value not in (None, ""):
                detail[normalized_key] = normalized_value

    return detail


def create_model_settings(payload: dict[str, Any], settings_input=None):
    """Construct provider settings without loading a PDF, engine, or user config."""
    service = require_supported_service(payload.get("service"))
    payload = {**payload, "service": service}
    if settings_input is None:
        engine_type = next(item.setting_model_type for item in TRANSLATION_ENGINE_METADATA
                           if item.cli_flag_name == service)
        settings_input = {
            "translation": {
                "lang_in": payload.get("source_lang", "en"),
                "lang_out": payload.get("target_lang", "zh-CN"),
                "qps": max(int(payload.get("qps", 1) or 1), 1),
                "pool_max_workers": int(payload.get("pool_size", 50) or 50),
            },
            "translate_engine_settings": engine_type.model_validate(
                build_service_detail(service, payload.get("llm_api") or {})),
        }
    settings = SettingsModel.model_validate(settings_input)
    settings.validate_settings()
    engine = settings.translate_engine_settings
    term_follows_main = settings.term_extraction_engine_settings is engine
    llm_api = payload.get("llm_api") or {}
    mode = llm_api.get("reasoningMode", "default")
    if mode not in {"default", "low", "off"}:
        raise ValueError("推理模式必须为 default、low 或 off")
    if isinstance(engine, SiliconFlowSettings):
        options = {}
        if engine.siliconflow_send_enable_thinking_param:
            options["enable_thinking"] = engine.siliconflow_enable_thinking
        engine = OpenAISettings(openai_model=engine.siliconflow_model,
            openai_api_key=engine.siliconflow_api_key, openai_base_url=engine.siliconflow_base_url,
            openai_temperature="0", openai_send_temprature=True,
            openai_enable_json_mode=engine.siliconflow_enable_json_mode,
            openai_request_options=json.dumps(options))
        settings.translate_engine_settings = engine
    if isinstance(engine, OpenAISettings):
        # Presets may transform to OpenAISettings with a hard-coded URL.
        # Apply the explicit endpoint and protocol after that transformation.
        if llm_api.get("apiUrl"):
            engine.openai_base_url = str(llm_api["apiUrl"]).strip()
        engine.openai_reasoning_mode = mode
        engine.openai_api_protocol = llm_api.get("apiProtocol", engine.openai_api_protocol)
        if "requestOptions" in llm_api:
            engine.openai_request_options = json.dumps({**parse_request_options(engine.openai_request_options),
                                                       **parse_request_options(llm_api["requestOptions"])})
        normalize_endpoint(engine.openai_base_url, engine.openai_api_protocol)
        parse_request_options(engine.openai_request_options)
    elif payload["service"] == "ollama":
        engine.ollama_reasoning_mode = mode
    elif payload["service"] in {"codex", "chatgpt"}:
        if mode == "off":
            if llm_api.get("reasoningEffort") not in (None, "", "none"):
                raise ValueError("关闭推理与所选推理档位冲突")
            setattr(engine, payload["service"] + "_reasoning_effort", "none")
        if mode == "low" and not llm_api.get("reasoningEffort"):
            setattr(engine, payload["service"] + "_reasoning_effort", "auto")
    elif payload["service"] == "claudecode":
        engine.claude_code_reasoning_mode = mode
    elif llm_api.get("apiProtocol") == "responses" or llm_api.get("requestOptions") or mode != "default":
        raise ValueError("此服务类型不支持双协议参数，请使用 OpenAI 兼容服务")
    if term_follows_main:
        settings.term_extraction_engine_settings = engine
    return settings
