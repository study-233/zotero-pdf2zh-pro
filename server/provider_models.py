"""Explicit model discovery for OpenAI-compatible endpoints.

Never include upstream bodies or exception strings in user-facing errors: they
may contain credentials. Discovery does not perform a translation or persist keys.
"""
from __future__ import annotations

import httpx
from codex_client import CodexError, get_codex_client
from codex_discovery import discovery_request

from pdf2zh_pro.translator.openai_protocol import normalize_endpoint
from pdf2zh_pro.translator.reasoning_mode import remember_capabilities


class ModelDiscoveryError(ValueError):
    def __init__(self, message: str, status: int = 400, *, code=None, attempts=None):
        super().__init__(message)
        self.public_message = message
        self.status = status
        self.code = code
        self.attempts = attempts


def list_codex_models(data: dict, *, include_runtime=False):
    path = data.get("cliPath")
    try:
        client = get_codex_client(path, proxy_mode=data.get("proxyMode"), proxy_url=data.get("proxyUrl"))
        with discovery_request(data.get("requestId")) as check:
            if include_runtime:
                return client.model_catalog(check_cancelled=check)
            return client.list_models(check_cancelled=check)
    except CodexError as error:
        raise ModelDiscoveryError(error.public_message, error.status_code, code=error.code,
                                  attempts=getattr(error, "attempts", None)) from None


MODEL_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mimo": "https://api.xiaomimimo.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


def list_provider_models(data: dict, *, include_details=False):
    service = data.get("service", "openai")
    if not isinstance(service, str) or service not in MODEL_ENDPOINTS:
        raise ModelDiscoveryError("此接口类型不支持获取模型列表，请手动填写模型。")
    url = data.get("apiUrl")
    if not url and service != "openai":
        url = MODEL_ENDPOINTS[service]
    key = data.get("apiKey", "")
    protocol = data.get("apiProtocol", "auto")
    if not isinstance(url, str) or not url.strip():
        raise ModelDiscoveryError("请先填写 API 地址。")
    if not isinstance(key, str) or "\n" in key or "\r" in key:
        raise ModelDiscoveryError("API Key 格式不正确。")
    try:
        base, _ = normalize_endpoint(url, protocol)
    except (ValueError, TypeError):
        raise ModelDiscoveryError("API 地址或协议无效，请检查地址及接口协议。") from None
    headers = {"Authorization": f"Bearer {key.strip()}"} if key.strip() else {}
    try:
        response = httpx.get(f"{base}/models", headers=headers,
                             params={"sub_type": "chat"} if service == "siliconflow" else None,
                             timeout=15, follow_redirects=False)
    except httpx.TimeoutException:
        raise ModelDiscoveryError("获取模型超时，仍可手动填写模型。", 504) from None
    except (httpx.HTTPError, httpx.InvalidURL, UnicodeError, ValueError):
        raise ModelDiscoveryError("无法连接 API，请检查地址、网络和证书；仍可手动填写模型。", 502) from None
    if response.status_code in (401, 403):
        raise ModelDiscoveryError("API Key 无效或没有模型列表权限，仍可手动填写模型。", 502)
    if response.status_code in (404, 405):
        raise ModelDiscoveryError("此中转站不支持获取模型，请手动填写模型名称。", 502)
    if response.status_code == 429:
        raise ModelDiscoveryError("请求过于频繁或额度不足，请稍后重试或手动填写模型。", 502)
    if not response.is_success:
        raise ModelDiscoveryError("模型列表请求失败，请检查 API 地址；仍可手动填写模型。", 502)
    try:
        payload = response.json()
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise ValueError()
        models = {entry["id"] for entry in entries
                  if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip()}
        if service == "mimo":
            # The catalog also includes speech recognition and synthesis models.
            models = {model for model in models if not {"asr", "tts"}.intersection(model.lower().split("-"))}
        details = remember_capabilities(base, key.strip(), entries)
        if include_details:
            return {"models": sorted(models), "modelDetails": [details[m] for m in sorted(models)]}
        return sorted(models)
    except (ValueError, AttributeError):
        raise ModelDiscoveryError("模型列表格式不受支持，请手动填写模型名称。", 502) from None
