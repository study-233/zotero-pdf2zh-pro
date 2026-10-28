"""Explicit non-reasoning capabilities; never infer support from model ordering."""

import copy
import re
from urllib.parse import urlsplit
import hashlib
import threading
import time

_catalog = {}
_catalog_lock = threading.Lock()


def _catalog_key(endpoint, key):
    return (endpoint.rstrip("/"), hashlib.sha256((key or "").encode()).hexdigest())


def remember_capabilities(endpoint, key, entries):
    details = {entry["id"]: model_capabilities(entry["id"], endpoint, entry)
               for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].strip()}
    with _catalog_lock:
        if len(_catalog) >= 64:
            _catalog.pop(next(iter(_catalog)))
        _catalog[_catalog_key(endpoint, key)] = (time.monotonic(), details)
    return details


def cached_capabilities(model, endpoint, key):
    with _catalog_lock:
        cached = _catalog.get(_catalog_key(endpoint, key))
        if cached and time.monotonic() - cached[0] < 600:
            return copy.deepcopy(cached[1].get(model) or model_capabilities(model, endpoint))
    return model_capabilities(model, endpoint)


def model_capabilities(model, endpoint="", entry=None):
    """Describe this endpoint's controls, without equating accepted low with low effort."""
    name = (model or "").lower().split("/")[-1]
    host = (urlsplit(endpoint).hostname or "").lower()
    result = {"id": model, "supportedReasoningEfforts": [],
              "supportsReasoningOff": None, "reasoningKind": "unknown"}
    family = reasoning_family(name)
    if re.fullmatch(r"glm-5\.3(?:-flash(?:x(?:-legal)?)?)?", name):
        result.update(supportedReasoningEfforts=["low", "high", "max"],
                      supportsReasoningOff=False, reasoningRequired=True, reasoningKind="effort")
    elif family or re.match(r"deepseek-v4(?:\.\d+)?-(flash|pro)$", name):
        result.update(supportedReasoningEfforts=["low"], supportsReasoningOff=True, reasoningKind="effort")
    elif name.startswith(("gpt-6", "gpt-5.6")):
        result.update(supportedReasoningEfforts=["low", "medium", "high"],
                      supportsReasoningOff=bool(re.fullmatch(r"gpt-6-luna(?:-\d{4}-\d{2}-\d{2})?", name)),
                      reasoningKind="effort")
    elif name.startswith(("gpt-4", "gpt-3.5")):
        result.update(supportsReasoningOff=False, reasoningKind="none")
    if "xiaomimimo.com" == host or host.endswith(".xiaomimimo.com"):
        if family == "mimo":
            result.update(supportedReasoningEfforts=[], reasoningKind="toggle")
    if host == "api.siliconflow.cn":
        result["supportedProtocols"] = ["chat_completions"]
        if family in {"deepseek", "zhipu"} or name.startswith("deepseek-v4"):
            result.update(supportedReasoningEfforts=["high", "max"], reasoningKind="mapped")
        elif name.startswith("qwen3"):
            result.update(supportsReasoningOff=True, reasoningKind="toggle")
    if host == "generativelanguage.googleapis.com":
        result["supportedProtocols"] = ["chat_completions"]
        if name.startswith("gemini-"):
            result.update(supportedReasoningEfforts=["low", "high"], reasoningKind="effort",
                          supportsReasoningOff=name.startswith("gemini-2.5-flash"))
    if "aliyuncs.com" == host or host.endswith(".aliyuncs.com"):
        if name.startswith(("qwen3", "deepseek-")):
            result.update(reasoningKind="toggle", supportsReasoningOff=True,
                          supportedReasoningEfforts=[])
    if host == "api.commandcode.ai" and name.startswith("claude-"):
        result["supportedProtocols"] = []  # Messages is a separate product adapter.
    if host == "api.commandcode.ai" and name == "qwen3.7-flash":
        result["supportsReasoningOff"] = True
    if isinstance(entry, dict):
        reasoning = entry.get("reasoning")
        if isinstance(reasoning, dict):
            levels = reasoning.get("supported_efforts")
            if isinstance(levels, list) and all(isinstance(v, str) for v in levels):
                result["supportedReasoningEfforts"] = [v for v in levels if v != "none"]
                result["reasoningKind"] = "effort"
                if "none" in levels:
                    result["supportsReasoningOff"] = True
            if isinstance(reasoning.get("mandatory"), bool):
                result["reasoningRequired"] = reasoning["mandatory"]
                result["supportsReasoningOff"] = not reasoning["mandatory"]
            if isinstance(reasoning.get("default_effort"), str):
                result["defaultReasoningEffort"] = reasoning["default_effort"]
        thinking = entry.get("thinking")
        if isinstance(thinking, dict):
            effort = thinking.get("effort", {})
            if isinstance(effort, dict) and isinstance(effort.get("supported_levels"), list):
                result["supportedReasoningEfforts"] = [v for v in effort["supported_levels"] if isinstance(v, str)]
            if isinstance(thinking.get("supported"), bool) and not thinking["supported"]:
                result.update(reasoningKind="none", supportedReasoningEfforts=[], supportsReasoningOff=False)
        endpoints = entry.get("supported_endpoints")
        if isinstance(endpoints, list):
            result["supportedProtocols"] = [p for suffix, p in (("responses", "responses"), ("chat/completions", "chat_completions"))
                                            if any(isinstance(e, str) and e.rstrip("/").endswith(suffix) for e in endpoints)]
    return result


def reasoning_family(model):
    model = (model or "").lower().split("/")[-1]
    if model == "deepseek-flash" or re.fullmatch(r"deepseek-v4(?:\.\d+)?-(flash|pro)", model):
        return "deepseek"
    if model in {"mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed"}:
        return "mimo"
    if model in {"glm-5.2", "glm-5.1", "glm-5", "glm-5-turbo", "glm-4.7", "glm-4.6", "glm-4.5", "glm-4.5-air"}:
        return "zhipu"
    if re.fullmatch(r"gpt-5\.(?:1|2|4|5)(?:-\d{4}-\d{2}-\d{2})?", model):
        return "openai"
    return None


def apply_reasoning_mode(options, mode, model, protocol, *, endpoint="", capabilities=None):
    if mode not in ("default", "low", "off"):
        raise ValueError("推理模式必须为 default、low 或 off")
    if mode == "default":
        return options
    caps = capabilities or model_capabilities(model, endpoint)
    if mode == "off" and caps.get("reasoningRequired"):
        raise ValueError("此模型必须开启推理，无法关闭")
    family = reasoning_family(model)
    if mode == "off" and caps.get("supportsReasoningOff") is not True:
        raise ValueError("此模型尚不支持关闭推理快捷设置，请保持现有设置或使用高级 JSON 参数")
    options = copy.deepcopy(options)
    if mode == "low":
        reasoning = options.get("reasoning", {})
        if (options.get("reasoning_effort", "low") != "low"
                or not isinstance(reasoning, dict) or reasoning.get("effort", "low") != "low"
                or "max_tokens" in reasoning or reasoning.get("enabled") is False
                or options.get("enable_thinking") is False
                or "thinking" in options and options["thinking"] != {"type": "enabled"}):
            raise ValueError("低推理与额外请求参数冲突，请选择提供方默认或修改额外参数")
        if caps["reasoningKind"] in {"none", "mapped"} or (caps["supportedReasoningEfforts"] and "low" not in caps["supportedReasoningEfforts"]):
            return options
        options.pop("reasoning_effort", None)
        host = urlsplit(endpoint).hostname or ""
        if protocol == "responses" or host == "openrouter.ai":
            options.setdefault("reasoning", {})["effort"] = "low"
        elif host.endswith(".aliyuncs.com") or host == "api.siliconflow.cn":
            options["enable_thinking"] = True
        elif family in {"deepseek", "mimo", "zhipu"} and host != "api.commandcode.ai":
            options["thinking"] = {"type": "enabled"}
            if caps["reasoningKind"] == "effort":
                options["reasoning_effort"] = "low"
        else:
            options["reasoning_effort"] = "low"
        return options
    if (options.get("enable_thinking") is True
            or "thinking" in options and options["thinking"] != {"type": "disabled"}
            or "reasoning_effort" in options and options["reasoning_effort"] != "none"
            or "reasoning" in options and (
                not isinstance(options["reasoning"], dict)
                or options["reasoning"].get("effort", "none") != "none")):
        raise ValueError("关闭推理与已有推理参数冲突，请删除或修改高级参数")
    host = urlsplit(endpoint).hostname or ""
    if protocol == "chat_completions" and host == "openrouter.ai":
        options["reasoning"] = {"effort": "none"}
    elif protocol == "chat_completions" and (host.endswith(".aliyuncs.com") or host == "api.siliconflow.cn"):
        options["enable_thinking"] = False
    elif protocol == "chat_completions" and host == "api.commandcode.ai":
        options["reasoning_effort"] = "off" if family in {"deepseek", "mimo", "zhipu"} else "none"
    elif family in {"deepseek", "mimo", "zhipu"} and protocol == "chat_completions":
        options.pop("reasoning_effort", None)
        options.pop("reasoning", None)
        options["thinking"] = {"type": "disabled"}
    elif protocol == "responses":
        options.pop("thinking", None)
        options.pop("reasoning_effort", None)
        options.setdefault("reasoning", {})["effort"] = "none"
    else:
        options.pop("thinking", None)
        options.pop("reasoning", None)
        options["reasoning_effort"] = "none"
    return options


def low_parameter_rejected(error):
    """Only remove our automatic low after an explicit reasoning-parameter rejection."""
    import json
    if getattr(error, "status_code", None) not in (400, 422):
        return False
    message = json.dumps(getattr(error, "body", None), ensure_ascii=False).lower()
    return (any(p in message for p in ("reasoning_effort", "reasoning.effort", "reasoning", "enable_thinking", "thinking"))
            and any(p in message for p in ("unsupported", "not supported", "unknown parameter", "unrecognized", "invalid", "不支持", "无效"))
            and not any(p in message for p in ("api_key", "api key", "quota", "model_not_found", "unsupported_model")))
