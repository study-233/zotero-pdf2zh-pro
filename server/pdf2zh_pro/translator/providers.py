import logging
import re
import shlex
import typing
from dataclasses import dataclass
from inspect import getdoc
from types import NoneType
from typing import Literal
from typing import TypeAlias

from pydantic import BaseModel
from pydantic import Field

# any field in SENSITIVE_FIELDS will be masked in GUI
SENSITIVE_FIELDS = []
# any field in CREDENTIAL_FIELDS will be masked in GUI and treated as password
CREDENTIAL_FIELDS = []

logger = logging.getLogger(__name__)


def _clean_string(value: str | None) -> str | None:
    """Clean string by trimming whitespace"""
    if value is None:
        return None
    return value.strip()


def _clean_url(value: str | None) -> str | None:
    """Clean URL for OpenAI-compatible services"""
    if value is None:
        return None
    cleaned = value.strip().rstrip("/")
    # Remove /chat/completions suffix for OpenAI-compatible APIs
    cleaned = re.sub(r"/chat/completions/?$", "", cleaned)
    return cleaned.rstrip("/")


def _check_if_positive_float(value: str | None, field: str = "Value") -> str | None:
    """Check if a string can be parsed as a positive float"""
    if value is None:
        return None

    try:
        f = float(value)
    except ValueError as e:
        raise ValueError(f"{field} must be a float") from e

    if f <= 0:
        raise ValueError(f"{field} must be greater than 0")

    return value


class TranslateEngineSettingError(Exception):
    """Translate engine setting error"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


# Provider settings accept the existing profile field names.

class OpenAISettings(BaseModel):
    """OpenAI API settings"""

    translate_engine_type: Literal["OpenAI"] = Field(default="OpenAI")
    support_llm: Literal["yes", "no"] = Field(
        default="yes", description="Whether the translator supports LLM"
    )

    openai_api_protocol: str = Field(default="chat_completions", description="API protocol: auto, chat_completions, responses")
    openai_reasoning_mode: str = Field(default="default", description="Reasoning mode: default or off")
    openai_request_options: str | None = Field(default=None, description="Additional API request parameters as JSON")
    openai_model: str = Field(default="gpt-4o-mini", description="OpenAI model to use")
    openai_base_url: str | None = Field(
        default=None, description="Base URL for OpenAI API"
    )
    openai_api_key: str | None = Field(
        default=None, description="API key for OpenAI service"
    )
    openai_timeout: str | None = Field(
        default=None, description="Timeout (seconds) for OpenAI service"
    )
    openai_temperature: str | None = Field(
        default=None, description="Temperature for OpenAI service"
    )
    openai_reasoning_effort: str | None = Field(
        default=None,
        description="Reasoning effort for OpenAI service (minimal/low/medium/high)",
    )
    openai_enable_json_mode: bool | None = Field(
        default=None, description="Enable JSON mode for OpenAI service"
    )

    # This parameter contains a spelling error, but it will not be corrected for compatibility reasons.
    # For details, see: https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/issues/175#issuecomment-3213568681
    openai_send_temprature: bool | None = Field(
        default=None, description="Send temprature to OpenAI service"
    )
    openai_send_reasoning_effort: bool | None = Field(
        default=None, description="Send reasoning effort to OpenAI service"
    )

    def validate_settings(self) -> None:
        if not self.openai_api_key:
            raise ValueError("OpenAI API key is required")
        self.openai_api_key = _clean_string(self.openai_api_key)
        self.openai_base_url = _clean_string(self.openai_base_url)
        self.openai_model = _clean_string(self.openai_model)
        self.openai_timeout = _check_if_positive_float(
            _clean_string(self.openai_timeout),
            field="Timeout",
        )
        self.openai_temperature = _clean_string(self.openai_temperature)
        self.openai_reasoning_effort = _clean_string(self.openai_reasoning_effort)
        if self.openai_send_temprature:
            if not self.openai_temperature:
                raise ValueError(
                    "Temperature is required when send temperature is enabled"
                )
            try:
                float(self.openai_temperature)
            except ValueError as e:
                raise ValueError("Temperature must be a float") from e
        if self.openai_send_reasoning_effort and not self.openai_reasoning_effort:
            raise ValueError(
                "Reasoning effort is required when send reasoning effort is enabled"
            )


CREDENTIAL_FIELDS.append("openai_api_key")
SENSITIVE_FIELDS.append("openai_base_url")






class DeepLSettings(BaseModel):
    """Bing Translation settings"""

    translate_engine_type: Literal["DeepL"] = Field(default="DeepL")
    deepl_auth_key: str | None = Field(default=None, description="DeepL auth key")

    def validate_settings(self) -> None:
        if not self.deepl_auth_key:
            raise ValueError("DeepL Auth key is required")
        self.deepl_auth_key = _clean_string(self.deepl_auth_key)


CREDENTIAL_FIELDS.append("deepl_auth_key")

# for openai compatibility translator
# You only need to add the corresponding configuration class
# and return the OpenAISettings instance using the transform method.


class DeepSeekSettings(BaseModel):
    """DeepSeek settings"""

    translate_engine_type: Literal["DeepSeek"] = Field(default="DeepSeek")
    support_llm: Literal["yes", "no"] = Field(
        default="yes", description="Whether the translator supports LLM"
    )
    deepseek_model: str = Field(
        default="deepseek-flash", description="DeepSeek model to use"
    )
    deepseek_api_key: str | None = Field(
        default=None, description="API key for DeepSeek service"
    )
    deepseek_enable_json_mode: bool | None = Field(
        default=None, description="Enable JSON mode for DeepSeek service"
    )

    def validate_settings(self) -> None:
        if not self.deepseek_api_key:
            raise ValueError("DeepSeek API key is required")
        self.deepseek_api_key = _clean_string(self.deepseek_api_key)
        self.deepseek_model = _clean_string(self.deepseek_model)

    def transform(self) -> OpenAISettings:
        return OpenAISettings(
            openai_model=self.deepseek_model,
            openai_api_key=self.deepseek_api_key,
            openai_base_url="https://api.deepseek.com/v1",
            openai_enable_json_mode=self.deepseek_enable_json_mode,
        )


CREDENTIAL_FIELDS.append("deepseek_api_key")


class MiMoSettings(BaseModel):
    """Xiaomi MiMo OpenAI-compatible API settings"""

    translate_engine_type: Literal["MiMo"] = Field(default="MiMo")
    support_llm: Literal["yes", "no"] = Field(default="yes")
    mimo_model: str = Field(default="mimo-v2.6-flash", description="MiMo model to use")
    mimo_api_key: str | None = Field(default=None, description="API key for MiMo service")
    mimo_base_url: str = Field(
        default="https://api.xiaomimimo.com/v1", description="Base URL for MiMo API"
    )

    def validate_settings(self) -> None:
        self.mimo_api_key = _clean_string(self.mimo_api_key)
        if not self.mimo_api_key:
            raise ValueError("MiMo API key is required")
        self.mimo_model = _clean_string(self.mimo_model)
        self.mimo_base_url = _clean_string(self.mimo_base_url)

    def transform(self) -> OpenAISettings:
        return OpenAISettings(
            openai_model=self.mimo_model,
            openai_api_key=self.mimo_api_key,
            openai_base_url=self.mimo_base_url,
        )


CREDENTIAL_FIELDS.append("mimo_api_key")
SENSITIVE_FIELDS.append("mimo_base_url")


class ZhipuSettings(BaseModel):
    """Zhipu GLM OpenAI-compatible API settings"""

    translate_engine_type: Literal["Zhipu"] = Field(default="Zhipu")
    support_llm: Literal["yes", "no"] = Field(default="yes")
    zhipu_model: str = Field(default="glm-5.3-flash", description="Zhipu model to use")
    zhipu_api_key: str | None = Field(default=None, description="API key for Zhipu service")
    zhipu_base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4", description="Base URL for Zhipu API"
    )
    zhipu_enable_json_mode: bool | None = Field(default=None, description="Enable JSON mode for Zhipu service")

    def validate_settings(self) -> None:
        self.zhipu_api_key = _clean_string(self.zhipu_api_key)
        if not self.zhipu_api_key:
            raise ValueError("Zhipu API key is required")
        self.zhipu_model = _clean_string(self.zhipu_model)
        self.zhipu_base_url = _clean_string(self.zhipu_base_url)

    def transform(self) -> OpenAISettings:
        return OpenAISettings(
            openai_model=self.zhipu_model,
            openai_api_key=self.zhipu_api_key,
            openai_base_url=self.zhipu_base_url,
            openai_enable_json_mode=self.zhipu_enable_json_mode,
        )


CREDENTIAL_FIELDS.append("zhipu_api_key")
SENSITIVE_FIELDS.append("zhipu_base_url")


class OllamaSettings(BaseModel):
    """Ollama API settings"""

    translate_engine_type: Literal["Ollama"] = Field(default="Ollama")
    support_llm: Literal["yes", "no"] = Field(
        default="yes", description="Whether the translator supports LLM"
    )

    ollama_model: str = Field(default="gemma2", description="Ollama model to use")
    ollama_host: str | None = Field(
        default="http://localhost:11434", description="Ollama host"
    )
    ollama_reasoning_mode: str = Field(default="default")
    num_predict: int | None = Field(
        default=2000, description="The max number of token to predict."
    )

    def validate_settings(self) -> None:
        if not self.ollama_host:
            raise ValueError("Ollama host is required")
        self.ollama_host = _clean_string(self.ollama_host)
        self.ollama_model = _clean_string(self.ollama_model)


SENSITIVE_FIELDS.append("ollama_host")


class SiliconFlowSettings(BaseModel):
    """SiliconFlow API settings"""

    translate_engine_type: Literal["SiliconFlow"] = Field(default="SiliconFlow")
    support_llm: Literal["yes", "no"] = Field(
        default="yes", description="Whether the translator supports LLM"
    )

    siliconflow_base_url: str | None = Field(
        default="https://api.siliconflow.cn/v1",
        description="Base URL for SiliconFlow API",
    )
    siliconflow_model: str = Field(
        default="Qwen/Qwen2.5-7B-Instruct", description="SiliconFlow model to use"
    )
    siliconflow_api_key: str | None = Field(
        default=None, description="API key for SiliconFlow service"
    )
    siliconflow_enable_thinking: bool | None = Field(
        default=False, description="Enable thinking for SiliconFlow service"
    )
    siliconflow_send_enable_thinking_param: bool | None = Field(
        default=False,
        description="Send enable thinking param to SiliconFlow service",
    )
    siliconflow_enable_json_mode: bool | None = Field(
        default=False, description="Enable JSON mode for SiliconFlow service"
    )

    def validate_settings(self) -> None:
        if not self.siliconflow_api_key:
            raise ValueError("SiliconFlow API key is required")
        self.siliconflow_api_key = _clean_string(self.siliconflow_api_key)
        self.siliconflow_base_url = _clean_string(self.siliconflow_base_url)
        self.siliconflow_model = _clean_string(self.siliconflow_model)


CREDENTIAL_FIELDS.append("siliconflow_api_key")
SENSITIVE_FIELDS.append("siliconflow_base_url")


class GeminiSettings(BaseModel):
    """Gemini API settings"""

    translate_engine_type: Literal["Gemini"] = Field(default="Gemini")
    support_llm: Literal["yes", "no"] = Field(
        default="yes", description="Whether the translator supports LLM"
    )

    gemini_model: str = Field(
        default="gemini-3.8-flash", description="Gemini model to use"
    )
    gemini_api_key: str | None = Field(
        default=None, description="API key for Gemini service"
    )
    gemini_enable_json_mode: bool | None = Field(
        default=None, description="Enable JSON mode for Gemini service"
    )

    def validate_settings(self) -> None:
        if not self.gemini_api_key:
            raise ValueError("Gemini API key is required")
        self.gemini_api_key = _clean_string(self.gemini_api_key)
        self.gemini_model = _clean_string(self.gemini_model)

    def transform(self) -> OpenAISettings:
        return OpenAISettings(
            openai_model=self.gemini_model,
            openai_api_key=self.gemini_api_key,
            openai_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            openai_enable_json_mode=self.gemini_enable_json_mode,
        )


CREDENTIAL_FIELDS.append("gemini_api_key")


class AliyunDashScopeSettings(BaseModel):
    """Aliyun DashScope settings"""

    translate_engine_type: Literal["AliyunDashScope"] = Field(default="AliyunDashScope")
    support_llm: Literal["yes", "no"] = Field(
        default="yes", description="Whether the translator supports LLM"
    )

    aliyun_dashscope_model: str = Field(
        default="qwen-plus-latest", description="Aliyun DashScope model to use"
    )
    aliyun_dashscope_base_url: str | None = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Base URL for Aliyun DashScope API",
    )
    aliyun_dashscope_api_key: str | None = Field(
        default=None, description="API key for Aliyun DashScope service"
    )
    aliyun_dashscope_timeout: str | None = Field(
        default="500", description="Timeout (seconds) for Aliyun DashScope service"
    )
    aliyun_dashscope_temperature: str | None = Field(
        default="0.0", description="Temperature for Aliyun DashScope service"
    )
    aliyun_dashscope_send_temperature: bool | None = Field(
        default=None, description="Send temperature to Aliyun DashScope service"
    )
    aliyun_dashscope_enable_json_mode: bool | None = Field(
        default=None, description="Enable JSON mode for Aliyun DashScope service"
    )

    def validate_settings(self) -> None:
        if not self.aliyun_dashscope_api_key:
            raise ValueError("Aliyun DashScope API key is required")
        if not self.aliyun_dashscope_base_url:
            raise ValueError("Aliyun DashScope base URL is required")
        if not self.aliyun_dashscope_model:
            raise ValueError("Aliyun DashScope model is required")
        self.aliyun_dashscope_api_key = _clean_string(self.aliyun_dashscope_api_key)
        self.aliyun_dashscope_base_url = _clean_url(self.aliyun_dashscope_base_url)
        self.aliyun_dashscope_model = _clean_string(self.aliyun_dashscope_model)
        self.aliyun_dashscope_timeout = _check_if_positive_float(
            _clean_string(self.aliyun_dashscope_timeout), field="Timeout"
        )
        self.aliyun_dashscope_temperature = _clean_string(
            self.aliyun_dashscope_temperature
        )
        if self.aliyun_dashscope_send_temperature:
            if not self.aliyun_dashscope_temperature:
                raise ValueError(
                    "Temperature is required when send temperature is enabled"
                )
            try:
                float(self.aliyun_dashscope_temperature)
            except ValueError as e:
                raise ValueError("Temperature must be a float") from e

    def transform(self) -> OpenAISettings:
        return OpenAISettings(
            openai_model=self.aliyun_dashscope_model,
            openai_api_key=self.aliyun_dashscope_api_key,
            openai_base_url=self.aliyun_dashscope_base_url,
            openai_timeout=self.aliyun_dashscope_timeout,
            openai_temperature=self.aliyun_dashscope_temperature,
            openai_send_temprature=self.aliyun_dashscope_send_temperature,
            openai_enable_json_mode=self.aliyun_dashscope_enable_json_mode,
        )


CREDENTIAL_FIELDS.append("aliyun_dashscope_api_key")


class ChatGPTSettings(BaseModel):
    """Translation with an independently authorized ChatGPT plan."""
    translate_engine_type: Literal['ChatGPT'] = Field(default='ChatGPT')
    support_llm: Literal['yes', 'no'] = Field(default='yes')
    chatgpt_model: str = Field(default='gpt-6-luna')
    chatgpt_reasoning_effort: str | None = Field(default=None)
    chatgpt_account_id: str | None = Field(default=None)
    chatgpt_timeout: float = Field(default=120, gt=0, le=600)

    def validate_settings(self):
        if not self.chatgpt_model.strip():
            raise ValueError('请选择 ChatGPT 模型')
        if self.chatgpt_reasoning_effort not in (None, 'auto', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh'):
            raise ValueError('ChatGPT 推理档位无效，请重新获取模型列表')
        if self.chatgpt_account_id is not None and not re.fullmatch(r'[0-9a-f]{64}', self.chatgpt_account_id):
            raise ValueError('ChatGPT 账号标识无效，请重新登录并保存配置')


class CodexSettings(BaseModel):
    """Translation through an installed, signed-in Codex CLI."""

    translate_engine_type: Literal["Codex"] = Field(default="Codex")
    support_llm: Literal["yes", "no"] = Field(default="yes")
    codex_model: str = Field(default="gpt-6-luna", description="Codex model")
    codex_cli_path: str | None = Field(default=None, description="Optional Codex CLI path")
    codex_proxy_mode: str = Field(default="inherit", description="inherit, manual or direct")
    codex_proxy_url: str | None = Field(default=None, description="HTTP(S) proxy address for Codex only")
    codex_reasoning_effort: str | None = Field(default=None, description="Model reasoning effort; omitted uses its default")
    codex_timeout: float = Field(default=120, gt=0, description="Codex request timeout in seconds")

    def validate_settings(self):
        from codex_client import normalize_codex_proxy
        self.codex_proxy_mode, self.codex_proxy_url = normalize_codex_proxy(
            self.codex_proxy_mode, self.codex_proxy_url)
        if not self.codex_model.strip():
            raise ValueError("请选择 Codex 模型")
        if self.codex_reasoning_effort is not None and self.codex_reasoning_effort not in {
            "auto", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
        }:
            raise ValueError("Codex 推理档位无效，请重新获取模型列表")


class ClaudeCodeSettings(BaseModel):
    """Claude Code settings"""

    translate_engine_type: Literal["ClaudeCode"] = Field(default="ClaudeCode")
    claude_code_reasoning_mode: str = Field(default="default")
    claude_code_path: str = Field(
        default="claude", description="Path to Claude Code CLI"
    )
    claude_code_model: str = Field(
        default="sonnet", description="Claude Code model to use"
    )

    def validate_settings(self):
        if not self.claude_code_path:
            raise ValueError("Claude Code path is required")






# 所有翻译引擎
TRANSLATION_ENGINE_SETTING_TYPE: TypeAlias = (
    OpenAISettings
    | AliyunDashScopeSettings
    | DeepLSettings
    | DeepSeekSettings
    | MiMoSettings
    | ZhipuSettings
    | OllamaSettings
    | SiliconFlowSettings
    | GeminiSettings
    | ClaudeCodeSettings
    | CodexSettings
    | ChatGPTSettings
)

# 不支持的翻译引擎
NOT_SUPPORTED_TRANSLATION_ENGINE_SETTING_TYPE: TypeAlias = NoneType

SENSITIVE_FIELDS.extend(CREDENTIAL_FIELDS)


@dataclass
class TranslationEngineMetadata:
    translate_engine_type: str
    cli_flag_name: str
    cli_detail_field_name: str | None
    setting_model_type: type[BaseModel]
    support_llm: bool

    def __init__(
        self,
        setting_model_type: type[BaseModel],
    ) -> None:
        self.translate_engine_type = setting_model_type.model_fields[
            "translate_engine_type"
        ].default
        self.cli_flag_name = self.translate_engine_type.lower()
        self.cli_detail_field_name = self.cli_flag_name + "_detail"
        self.setting_model_type = setting_model_type
        if len(setting_model_type.model_fields) == 1:
            self.cli_detail_field_name = None
        self.support_llm = (
            (sl := setting_model_type.model_fields.get("support_llm", None))
            and sl.default == "yes"
        ) or False


args = typing.get_args(TRANSLATION_ENGINE_SETTING_TYPE)

TRANSLATION_ENGINE_METADATA = [
    TranslationEngineMetadata(
        setting_model_type=arg,
    )
    for arg in args
]

TRANSLATION_ENGINE_METADATA_MAP = {
    metadata.translate_engine_type: metadata for metadata in TRANSLATION_ENGINE_METADATA
}


# auto check duplicate translation engine metadata
assert len(TRANSLATION_ENGINE_METADATA_MAP) == len(TRANSLATION_ENGINE_METADATA), (
    "Duplicate translation engine metadata"
)

# auto check duplicate cli flag name and cli detail field name
dedup_set = set()
for metadata in TRANSLATION_ENGINE_METADATA:
    if metadata.cli_flag_name in dedup_set:
        raise ValueError(f"Duplicate cli flag name: {metadata.cli_flag_name}")
    dedup_set.add(metadata.cli_flag_name)
    if metadata.cli_detail_field_name and metadata.cli_detail_field_name in dedup_set:
        raise ValueError(
            f"Duplicate cli detail field name: {metadata.cli_detail_field_name}"
        )
    dedup_set.add(metadata.cli_detail_field_name)
del dedup_set


_TERM_EXTRACTION_ENGINE_SETTING_TYPE: type[BaseModel] | None = None
for metadata in TRANSLATION_ENGINE_METADATA:
    if not metadata.support_llm:
        continue
    if _TERM_EXTRACTION_ENGINE_SETTING_TYPE is None:
        _TERM_EXTRACTION_ENGINE_SETTING_TYPE = metadata.setting_model_type
    else:
        _TERM_EXTRACTION_ENGINE_SETTING_TYPE = (
            _TERM_EXTRACTION_ENGINE_SETTING_TYPE | metadata.setting_model_type
        )

assert _TERM_EXTRACTION_ENGINE_SETTING_TYPE is not None, (
    "No LLM-capable translation engines configured"
)

# 术语提取引擎：仅包含 support_llm == \"yes\" 的翻译引擎设置类型
TERM_EXTRACTION_ENGINE_SETTING_TYPE: TypeAlias = _TERM_EXTRACTION_ENGINE_SETTING_TYPE
