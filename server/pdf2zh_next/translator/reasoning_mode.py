"""Explicit non-reasoning capabilities; never infer support from model ordering."""

import copy
import re


def reasoning_family(model):
    model = (model or "").lower()
    if model in {"deepseek-v4-flash", "deepseek-v4-pro", "deepseek-flash"}:
        return "deepseek"
    if re.fullmatch(r"gpt-5\.(?:1|2|4|5)(?:-\d{4}-\d{2}-\d{2})?", model):
        return "openai"
    return None


def apply_reasoning_mode(options, mode, model, protocol):
    if mode not in ("default", "off"):
        raise ValueError("推理模式必须为 default 或 off")
    if mode == "default":
        return options
    family = reasoning_family(model)
    if family is None:
        raise ValueError("此模型尚不支持关闭推理快捷设置，请保持现有设置或使用高级 JSON 参数")
    options = copy.deepcopy(options)
    if ("thinking" in options and options["thinking"] != {"type": "disabled"}
            or "reasoning_effort" in options and options["reasoning_effort"] != "none"
            or "reasoning" in options and (
                not isinstance(options["reasoning"], dict)
                or options["reasoning"].get("effort", "none") != "none")):
        raise ValueError("关闭推理与已有推理参数冲突，请删除或修改高级参数")
    if family == "deepseek" and protocol == "chat_completions":
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
