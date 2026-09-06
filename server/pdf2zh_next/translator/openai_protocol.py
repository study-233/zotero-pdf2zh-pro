"""Wire-format helpers shared by translation and provider health checks."""

from __future__ import annotations

import copy
import json
import re
from urllib.parse import urlsplit, urlunsplit

PROTOCOLS = ("auto", "chat_completions", "responses")
RESERVED_OPTIONS = {
    "model",
    "messages",
    "input",
    "instructions",
    "stream",
    "stream_options",
    "background",
    "conversation",
    "previous_response_id",
    "tools",
    "tool_choice",
    "extra_body",
    "extra_headers",
    "extra_query",
    "api_key",
    "base_url",
}


def normalize_endpoint(url: str | None, protocol: str) -> tuple[str | None, str]:
    if protocol not in PROTOCOLS:
        raise ValueError("apiProtocol must be auto, chat_completions or responses")
    hint = "chat_completions"
    if not url:
        return None, hint
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("API 地址必须是有效的 http(s) URL")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("API 地址不能包含查询参数、片段或账号密码")
    path = parts.path.rstrip("/")
    for suffix, candidate in (
        ("/chat/completions", "chat_completions"),
        ("/responses", "responses"),
    ):
        if path.endswith(suffix):
            if protocol != "auto" and protocol != candidate:
                raise ValueError(
                    "所选 API 协议与地址后缀冲突，请修改协议或填写 Base URL"
                )
            path = path[: -len(suffix)]
            hint = candidate
            break
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")), hint


def parse_request_options(value: str | dict | None) -> dict:
    try:
        result = (
            json.loads(value)
            if isinstance(value, str)
            else (value if value is not None else {})
        )
        if not isinstance(result, dict):
            raise ValueError()
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("requestOptions 必须是有效的 JSON 对象") from None
    if RESERVED_OPTIONS.intersection(result):
        raise ValueError(
            "requestOptions 不能覆盖模型、输入、执行方式、会话、工具或连接设置"
        )
    return copy.deepcopy(result)


def _merge_alias(options: dict, source: str, target: str) -> None:
    if source not in options:
        return
    value = options.pop(source)
    if target in options and options[target] != value:
        raise ValueError(f"请求参数冲突：{source} / {target}")
    options.setdefault(target, value)


def _nested_alias(options: dict, source: str, parent: str, child: str) -> None:
    if source not in options:
        return
    nested = options.setdefault(parent, {})
    if not isinstance(nested, dict):
        raise ValueError(f"{parent} 必须是 JSON 对象")
    value = options.pop(source)
    if child in nested and nested[child] != value:
        raise ValueError(f"请求参数冲突：{source} / {parent}.{child}")
    nested.setdefault(child, value)


def wire_options(options: dict, protocol: str) -> dict:
    options = copy.deepcopy(options)
    if protocol == "responses":
        _merge_alias(options, "max_tokens", "max_output_tokens")
        _merge_alias(options, "max_completion_tokens", "max_output_tokens")
        _nested_alias(options, "reasoning_effort", "reasoning", "effort")
        if "response_format" in options:
            fmt = options["response_format"]
            if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
                if not isinstance(fmt.get("json_schema"), dict):
                    raise ValueError("response_format.json_schema 必须是 JSON 对象")
                options["response_format"] = {
                    "type": "json_schema",
                    **fmt["json_schema"],
                }
        _nested_alias(options, "response_format", "text", "format")
        options.setdefault("store", False)
    else:
        _merge_alias(options, "max_output_tokens", "max_completion_tokens")
        if "max_tokens" in options and "max_completion_tokens" in options:
            _merge_alias(options, "max_tokens", "max_completion_tokens")
        for parent, child, target in (
            ("reasoning", "effort", "reasoning_effort"),
            ("text", "format", "response_format"),
        ):
            if parent in options:
                nested = options.pop(parent)
                if not isinstance(nested, dict) or set(nested) != {child}:
                    raise ValueError(
                        f"Chat Completions 无法转换 {parent} 参数，请指定 Responses"
                    )
                value = nested[child]
                if (
                    target == "response_format"
                    and isinstance(value, dict)
                    and value.get("type") == "json_schema"
                ):
                    value = {
                        "type": "json_schema",
                        "json_schema": {k: v for k, v in value.items() if k != "type"},
                    }
                if target in options and options[target] != value:
                    raise ValueError(f"请求参数冲突：{parent}.{child} / {target}")
                options.setdefault(target, value)
    return options


def protocol_rejection_message(message: str) -> bool:
    """Recognize an explicit wire-protocol rejection, including relay messages."""
    message = message.lower()
    if "protocol_not_supported" in message or "unsupported_protocol" in message:
        return True
    protocol = r"(?:responses|chat[ /_]completions)(?:\s+api|\s*协议)?"
    return any(re.search(pattern, message) for pattern in (
        r"(?:only supports?|only supported)\s+(?:the\s+)?" + protocol,
        r"(?:仅支持|只支持)\s*" + protocol,
        r"(?:不支持|does not support|doesn't support)\s*(?:the\s+)?" + protocol,
        protocol + r"\s+(?:is|are)\s+not supported",
        r"unsupported\s+" + protocol,
    ))


def endpoint_unsupported(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    # Inspect only to classify. Never expose request bodies or credentials in logs.
    message = json.dumps(body, ensure_ascii=False).lower() if body is not None else ""
    if any(
        word in message
        for word in (
            "api key",
            "api_key",
            "auth",
            "parameter",
            "quota",
            "rate_limit",
        )
    ):
        return False
    if status in (401, 403, 429) or status is None:
        return False
    # A protocol-specific error code is decisive even when the message is
    # localized. Model, authentication and parameter failures still take priority.
    if any(word in message for word in (
        "model_not_found", "unsupported_model", "model_not_supported",
        "model not found", "model does not exist",
        "模型不存在", "模型不可用", "模型未找到", "模型无权限", "参数",
    )):
        return False
    if "model" in message and any(
        phrase in message
        for phrase in ("not found", "not exist", "unavailable", "not_found", "no such")
    ):
        return False
    if status in (400, 404, 422) and protocol_rejection_message(message):
        return True
    only_protocol = any(
        phrase in message for phrase in ("only supports", "only supported")
    ) and any(
        name in message
        for name in ("responses", "chat completions", "chat/completions")
    )
    if "model" in message:
        return status in (400, 404, 422) and only_protocol
    if only_protocol and status in (400, 404, 422):
        return True
    if status in (405, 501):
        return True
    if status == 404 and any(
        word in message
        for word in ("not found", "unknown route", "unknown endpoint", "cannot post")
    ):
        return True
    return status in (400, 404, 422) and any(
        phrase in message
        for phrase in (
            "unsupported endpoint",
            "endpoint is not supported",
            "unsupported protocol",
            "chat completions is not supported",
            "responses is not supported",
            "chat completions are not supported",
            "responses api is not supported",
        )
    )


def response_text(response, protocol: str) -> str:
    if protocol == "responses":
        if getattr(response, "error", None) or getattr(response, "status", None) in {
            "failed",
            "incomplete",
            "cancelled",
            "queued",
            "in_progress",
        }:
            raise ValueError(
                "Responses 返回失败或不完整结果，请检查输出 token 上限及服务状态"
            )
        chunks = []
        for item in getattr(response, "output", None) or []:
            if (
                getattr(item, "type", None) != "message"
                or getattr(item, "role", None) != "assistant"
            ):
                continue
            if getattr(item, "status", None) in {"incomplete", "in_progress"}:
                raise ValueError("Responses 返回不完整的文本消息")
            for block in getattr(item, "content", None) or []:
                if getattr(block, "type", None) == "refusal":
                    raise ValueError("API 拒绝生成译文")
                if getattr(block, "type", None) == "output_text":
                    value = getattr(block, "text", None)
                    if not isinstance(value, str):
                        raise ValueError("Responses 文本块格式错误")
                    chunks.append(value)
        text = "".join(chunks)
    else:
        choices = getattr(response, "choices", None)
        if not choices:
            raise ValueError("Chat Completions 响应缺少 choices，请检查接口协议和地址")
        choice = choices[0]
        if getattr(choice, "finish_reason", None) in {
            "length",
            "content_filter",
            "tool_calls",
            "function_call",
        }:
            raise ValueError(
                "Chat Completions 返回截断、过滤或工具调用结果，未生成完整译文"
            )
        message = getattr(choice, "message", None)
        if getattr(message, "refusal", None):
            raise ValueError("API 拒绝生成译文")
        text = getattr(message, "content", None)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            f"{protocol} 未返回有效文本译文，请检查接口协议、模型及输出 token 上限"
        )
    return text.strip()
