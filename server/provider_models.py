"""Explicit model discovery for OpenAI-compatible endpoints.

Never include upstream bodies or exception strings in user-facing errors: they
may contain credentials. Discovery does not perform a translation or persist keys.
"""
from __future__ import annotations

import httpx

from pdf2zh_next.translator.openai_protocol import normalize_endpoint


class ModelDiscoveryError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def list_provider_models(data: dict) -> list[str]:
    url = data.get("apiUrl")
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
        response = httpx.get(f"{base}/models", headers=headers, timeout=15, follow_redirects=False)
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
        return sorted({entry["id"] for entry in entries
                       if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip()})
    except (ValueError, AttributeError):
        raise ModelDiscoveryError("模型列表格式不受支持，请手动填写模型名称。", 502) from None
