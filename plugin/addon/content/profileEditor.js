/* eslint-disable no-restricted-globals -- These scripts run in their own native dialog window. */
/* global window, document, URL */
"use strict";
const DEFAULT_SERVICES = {
    openaicompatible: {
        name: "OpenAICompatible",
        models: [
            // deepseek
            "deepseek-v3-2-251201",
            "deepseek-v3-1-terminus",
            "deepseek-v3-1-250821", // 即将下线
            "deepseek-v3-250324",
            "deepseek-r1-250528",
            // kimi
            "kimi-k2-250905",
            // glm
            "glm-4-7-251222",
            // doubao 1.6
            "doubao-seed-1-6-251015",
            "doubao-seed-1-6-250615",
            "doubao-seed-1-6-flash-250615",
            "doubao-seed-1-6-thinking-250715", // 即将下线
            "doubao-seed-1-6-thinking-250615", // 即将下线
            "doubao-seed-translation-250915",
            // doubao 1.8
            "doubao-seed-1-8-251228",
            // doubao 2.0
            "doubao-seed-2-0-pro-260215",
            "doubao-seed-2-0-lite-260215",
            "doubao-seed-2-0-mini-260215",
            "doubao-seed-2-0-code-preview-260215",
            // doubao 1.5
            "doubao-1-5-lite-32k-250115",
            "doubao-1-5-thinking-pro-250415", // 即将下线
        ],
        urls: ["https://ark.cn-beijing.volces.com/api/v3"],
        modelListUrl:
            "https://console.volcengine.com/ark/region:ark+cn-beijing/openManagement?LLM=%7B%7D&advancedActiveKey=model",
        extraData: [],
    },
    openai: {
        name: "OpenAI",
        models: [
            "gpt-5",
            "gpt-5-mini",
            "gpt-4",
            "gpt-4",
            "gpt-4o",
            "gpt-4o-mini",
        ],
        urls: ["https://api.openai.com/v1"],
        modelListUrl: "https://platform.openai.com/docs/models",
    },
    aliyundashscope: {
        name: "AliyunDashScope",
        models: [
            "qwen3.5-plus",
            "qwen3.5-flash",
            "qwen3.5-35b-a3b",
            "qwen3-max",
            "qwen-plus",
            "qwen-flash",
            "qwen-mt-plus",
            "qwen-mt-flash",
        ],
        urls: ["https://dashscope.aliyuncs.com/compatible-mode/v1"],
    },
    siliconflow: {
        name: "SiliconFlow",
        models: [
            "deepseek-ai/DeepSeek-V3.2",
            "deepseek-ai/DeepSeek-V3.1-Terminus",
            "deepseek-ai/DeepSeek-R1",
            "Pro/deepseek-ai/DeepSeek-V3.2",
            "Pro/deepseek-ai/DeepSeek-V3.1-Terminus",
            "Pro/deepseek-ai/DeepSeek-R1",
            "Qwen/Qwen3-8B",
            "Qwen/Qwen2.5-72B-Instruct",
            "moonshotai/Kimi-K2-Thinking",
            "Pro/zai-org/GLM-4.7",
        ],
        urls: ["https://api.siliconflow.cn/v1"],
        modelListUrl: "https://cloud.siliconflow.cn/me/models",
    },
    gemini: {
        name: "Gemini",
        models: [
            "gemini-3.0-flash-preview",
            "gemini-3.0-pro-preview",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
        ],
    },
    azureopenai: {
        name: "AzureOpenAI",
        models: [
            "gpt-5",
            "gpt-5-mini",
            "gpt-4",
            "gpt-4",
            "gpt-4o",
            "gpt-4o-mini",
        ],
    },
    zhipu: {
        name: "Zhipu",
        models: [
            "glm-4-flash",
            "glm-4.5",
            "glm-4.5-x",
            "glm-4.5-air",
            "glm-4.5-airx",
        ],
        urls: ["https://api.zhipu.com/v1"],
    },
    deepseek: {
        name: "DeepSeek",
        models: ["deepseek-chat", "deepseek-coder"],
        urls: ["https://api.deepseek.com/v1"],
        modelListUrl: "https://platform.deepseek.com/",
    },
    qwenmt: {
        name: "QwenMt",
        models: [
            "qwen-plus-latest",
            "qwen-max",
            "qwen-max-latest",
            "qwen-plus",
            "qwen3-235b-a22b",
        ],
    },
    ollama: {
        name: "Ollama",
        models: ["gemma3:12B", "gemma"],
        urls: ["http://localhost:11434"],
        modelListUrl: "https://ollama.com/library",
    },
    modelscope: {
        name: "ModelScope",
        models: ["openai-mirror/gpt-oss-120b", "openai-mirror/gpt-oss-20b"],
        urls: ["https://api.modelscope.com/v1"],
    },
    tencentmechinetranslation: {
        name: "TencentMechineTranslation",
        urls: ["https://tencent.com"],
    },
    grok: {
        name: "Grok",
        models: ["grok-4-0709", "grok-3", "grok-3-mini"],
    },
    xinference: {
        name: "XInference",
        models: ["gemma-2-it"],
        urls: ["http://127.0.0.1:9997"],
    },
    deepl: {
        name: "DeepL",
    },
    siliconflowfree: {
        name: "SiliconFlow Free",
    },
    claudecode: {
        name: "Claude Code",
        models: ["sonnet"],
        urls: ["claude"],
    },
};
const args = window.arguments[0];
const $ = (id) => document.getElementById(id);
const fields = [
    "name",
    "service",
    "apiUrl",
    "apiKey",
    "model",
    "apiProtocol",
    "requestOptions",
    "extraData",
];
let tested = "";
let busy = false;
const fingerprint = () => JSON.stringify(fields.map((id) => $(id).value));
function message(text, error = false) {
    $("status").textContent = text;
    $("status").dataset.error = String(error);
}
function jsonField(id) {
    const value = JSON.parse($(id).value.trim() || "{}");
    if (!value || Array.isArray(value) || typeof value !== "object")
        throw new Error("高级参数必须是 JSON 对象。");
    return value;
}
function read(requireModel = true) {
    const value = { ...args.data };
    for (const id of fields)
        value[id] = ["requestOptions", "extraData"].includes(id)
            ? jsonField(id)
            : $(id).value.trim();
    if (!args.services[value.service])
        throw new Error("请选择支持的接口类型。");
    if (value.service === "openai") {
        let url;
        try {
            url = new URL(value.apiUrl);
        } catch {
            throw new Error("请填写有效的 HTTP(S) API 地址。");
        }
        if (
            !["http:", "https:"].includes(url.protocol) ||
            url.search ||
            url.hash ||
            url.username ||
            url.password
        )
            throw new Error(
                "请填写有效的 HTTP(S) API 地址，不要包含查询参数或账号密码。",
            );
        if (requireModel && !value.model) throw new Error("请填写模型名称。");
    }
    if (!value.name) {
        try {
            value.name = new URL(value.apiUrl).hostname;
        } catch {
            value.name = args.services[value.service];
        }
    }
    value.needsTest = tested !== fingerprint();
    delete value.activate;
    return value;
}
let modelIds = [];
function hideModels() {
    $("models").hidden = true;
    $("model").setAttribute("aria-expanded", "false");
}
function showModels() {
    const query = $("model").value.toLowerCase();
    const matches = modelIds.filter((id) => id.toLowerCase().includes(query));
    $("models").replaceChildren();
    for (const model of matches) {
        const option = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "option",
        );
        option.value = model;
        option.textContent = model;
        $("models").append(option);
    }
    $("models").hidden = !matches.length;
    $("models").selectedIndex = -1;
    $("model").setAttribute("aria-expanded", String(!!matches.length));
}
function setModels(models) {
    modelIds = [...new Set(models)];
    hideModels();
}
function chooseModel() {
    if ($("models").selectedIndex < 0) return;
    $("model").value = $("models").value;
    tested = "";
    message("");
    $("model").focus();
    hideModels();
}
$("model").addEventListener("input", showModels);
$("model").addEventListener("focus", showModels);
$("model").addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && !$("models").hidden) {
        event.preventDefault();
        $("models").focus();
        $("models").selectedIndex = 0;
    }
    if (event.key === "Enter") {
        event.preventDefault();
        hideModels();
    }
    if (event.key === "Escape" && !$("models").hidden) {
        event.stopPropagation();
        hideModels();
    }
});
$("models").addEventListener("click", chooseModel);
$("models").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        chooseModel();
    }
    if (event.key === "Escape") {
        event.stopPropagation();
        $("model").focus();
        hideModels();
    }
});
document.addEventListener("focusin", (event) => {
    if (!["model", "models"].includes(event.target.id)) hideModels();
});
function updateService() {
    const preset = DEFAULT_SERVICES[$("service").value];
    setModels(preset?.models || []);
    $("get-models").disabled =
        busy || !["openai", "openaicompatible"].includes($("service").value);
}
async function perform(kind) {
    if (busy) return;
    const before = fingerprint();
    try {
        const value = read(kind === "test");
        busy = true;
        $("test").disabled = true;
        $("get-models").disabled = true;
        message(kind === "test" ? "正在发送短翻译请求…" : "正在获取模型列表…");
        if (kind === "test") {
            const result = await args.test(value);
            if (window.closed || fingerprint() !== before) return;
            tested = before;
            message(result);
        } else {
            const models = await args.listModels(value);
            if (window.closed || fingerprint() !== before) return;
            setModels(models);
            message(
                models.length
                    ? `已获取 ${models.length} 个模型，请在模型框输入关键词搜索。`
                    : "站点返回了空列表，请手动填写模型名称。",
            );
            $("model").focus();
            showModels();
        }
    } catch (error) {
        if (!window.closed && fingerprint() === before)
            message(error.message || "操作失败", true);
    } finally {
        if (!window.closed) {
            busy = false;
            $("test").disabled = false;
            updateModelButton();
        }
    }
}
function updateModelButton() {
    $("get-models").disabled =
        busy || !["openai", "openaicompatible"].includes($("service").value);
}
function save(use) {
    try {
        args.save(read(), use);
        window.close();
    } catch (error) {
        message(error.message, true);
    }
}
for (const [key, label] of Object.entries(args.services)) {
    const option = document.createElementNS(
        "http://www.w3.org/1999/xhtml",
        "option",
    );
    option.value = key;
    option.textContent = label;
    $("service").append(option);
}
if (args.data.service && !args.services[args.data.service]) {
    const option = document.createElementNS(
        "http://www.w3.org/1999/xhtml",
        "option",
    );
    option.value = args.data.service;
    option.textContent = `${args.data.service}（请选择接口类型）`;
    $("service").append(option);
}
for (const id of fields) {
    const value = args.data[id];
    $(id).value = ["extraData", "requestOptions"].includes(id)
        ? JSON.stringify(value || {}, null, 2)
        : value || (id === "apiProtocol" ? "auto" : "");
    $(id).addEventListener("input", () => {
        tested = "";
        message("");
    });
    $(id).addEventListener("change", () => {
        tested = "";
        message("");
    });
}
if (args.isEdit) {
    $("title").textContent = "编辑翻译配置";
    $("save").textContent = "保存";
    $("save-only").hidden = true;
}
$("service").addEventListener("change", () => {
    if (!$("apiUrl").value && $("service").value !== "openai")
        $("apiUrl").value =
            DEFAULT_SERVICES[$("service").value]?.urls?.[0] || "";
    updateService();
});
for (const id of ["apiUrl", "apiKey"])
    $(id).addEventListener("input", () => setModels([]));
$("reveal").addEventListener("click", () => {
    const show = $("apiKey").type === "password";
    $("apiKey").type = show ? "text" : "password";
    $("reveal").textContent = show ? "隐藏" : "显示";
    $("reveal").setAttribute("aria-pressed", String(show));
});
$("get-models").addEventListener("click", () => perform("models"));
$("test").addEventListener("click", () => perform("test"));
$("cancel").addEventListener("click", () => window.close());
$("save-only").addEventListener("click", () => save(false));
$("profile-form").addEventListener("submit", (event) => {
    event.preventDefault();
    save(!args.isEdit);
});
window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") window.close();
});
updateService();
$("name").focus();
