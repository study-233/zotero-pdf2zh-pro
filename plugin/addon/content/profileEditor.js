/* eslint-disable no-restricted-globals -- These scripts run in their own native dialog window. */
/* global window, document, URL */
"use strict";
const DEFAULT_SERVICES = {
    openai: { urls: ["https://api.openai.com/v1"] },
    deepseek: { urls: ["https://api.deepseek.com/v1"] },
    gemini: {
        urls: ["https://generativelanguage.googleapis.com/v1beta/openai"],
    },
    grok: { urls: ["https://api.x.ai/v1"] },
    groq: { urls: ["https://api.groq.com/openai/v1"] },
    aliyundashscope: {},
    qwenmt: {
        models: [
            "qwen-mt-plus",
            "qwen-mt-flash",
            "qwen-mt-lite",
            "qwen-mt-turbo",
        ],
    },
    siliconflow: { urls: ["https://api.siliconflow.cn/v1"] },
    zhipu: { urls: ["https://open.bigmodel.cn/api/paas/v4"] },
    modelscope: { urls: ["https://api-inference.modelscope.cn/v1"] },
    azure: { urls: ["https://api.cognitive.microsofttranslator.com"] },
    azureopenai: {},
    ollama: { urls: ["http://localhost:11434"] },
    xinference: { urls: ["http://127.0.0.1:9997"] },
    anythingllm: {},
    deepl: {},
    siliconflowfree: {},
    codex: {},
    claudecode: { models: ["sonnet", "opus", "haiku"], urls: ["claude"] },
};
const MODEL_DISCOVERY_SERVICES = [
    "openai",
    "deepseek",
    "gemini",
    "grok",
    "groq",
    "siliconflow",
    "codex",
];
const OPENAI_SERVICES = [
    "openai",
    "deepseek",
    "gemini",
    "grok",
    "groq",
    "aliyundashscope",
    "zhipu",
    "modelscope",
];
const NO_MODEL_SERVICES = ["azure", "deepl", "siliconflowfree", "anythingllm"];
const args = window.arguments[0];
const $ = (id) => document.getElementById(id);
const fields = [
    "name",
    "service",
    "apiUrl",
    "apiKey",
    "model",
    "apiProtocol",
    "reasoningMode",
    "requestOptions",
    "extraData",
    "cliPath",
    "reasoningEffort",
    "proxyMode",
    "proxyUrl",
    "azureRegion",
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
function supportsReasoningOff(model, service) {
    return (
        ["openai", "deepseek"].includes(service) &&
        (/^gpt-5\.(1|2|4|5)(-\d{4}-\d{2}-\d{2})?$/.test(model.toLowerCase()) ||
            ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-flash"].includes(
                model.toLowerCase(),
            ))
    );
}
let modelDetails = [];
function updateCodexProxy() {
    const codex = $("service").value === "codex";
    $("codex-proxy-url-field").hidden =
        !codex || $("proxyMode").value !== "manual";
    $("codex-proxy-hint").textContent =
        {
            inherit: "使用 Python 服务启动时的代理环境，可能与当前终端不同。",
            manual: "仅此 Codex 配置使用指定代理；代理软件需保持运行，无需开启 TUN。",
            direct: "直接访问 Codex；系统 TUN 路由仍可能生效。",
        }[$("proxyMode").value] || "";
}

function readCodexProxy(value) {
    if (!["inherit", "manual", "direct"].includes(value.proxyMode))
        throw new Error("请选择有效的 Codex 代理模式。");
    if (value.proxyMode !== "manual") {
        delete value.proxyUrl;
        return;
    }
    const message =
        "请填写 HTTP(S) 代理地址（如 http://127.0.0.1:7897），不要包含路径、参数或账号密码。";
    try {
        const url = new URL(value.proxyUrl);
        if (
            !/^https?:\/\//i.test(value.proxyUrl) ||
            /[\s\\]/.test(value.proxyUrl) ||
            Array.from(value.proxyUrl).some(
                (character) =>
                    character.charCodeAt(0) < 0x20 ||
                    character.charCodeAt(0) === 0x7f,
            ) ||
            !["http:", "https:"].includes(url.protocol) ||
            !url.hostname ||
            /^https?:\/\/[^/]*@/i.test(value.proxyUrl) ||
            url.username ||
            url.password ||
            (url.pathname && url.pathname !== "/") ||
            /[?#]/.test(value.proxyUrl) ||
            url.port === "0" ||
            /:\/?$/.test(value.proxyUrl.replace(/^https?:\/\//i, ""))
        )
            throw new Error(message);
        value.proxyUrl = url.origin;
    } catch {
        throw new Error(message);
    }
}
function updateCodexReasoning() {
    const selected = $("reasoningEffort").value;
    const detail = modelDetails.find(
        (model) => model.id === $("model").value.trim(),
    );
    const efforts =
        detail?.supportedReasoningEfforts || (selected ? [selected] : []);
    $("reasoningEffort").replaceChildren();
    for (const effort of ["", ...efforts]) {
        const option = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "option",
        );
        option.value = effort;
        option.textContent =
            effort ||
            `模型默认${detail?.defaultReasoningEffort ? `（${detail.defaultReasoningEffort}）` : ""}`;
        $("reasoningEffort").append(option);
    }
    $("reasoningEffort").value = efforts.includes(selected) ? selected : "";
    $("codex-reasoning-hint").textContent = detail
        ? "仅列出该模型支持的档位；模型是否可用以连接测试为准。"
        : "获取模型列表后可选择该模型支持的推理档位。";
}
function updateReasoningMode() {
    if ($("service").value === "codex") updateCodexReasoning();
    const supported = supportsReasoningOff(
        $("model").value.trim(),
        $("service").value,
    );
    $("reasoning-off").disabled = !supported;
    $("reasoning-hint").textContent = supported
        ? "关闭推理可减少等待；中转接口以测试结果为准。"
        : "此模型尚不支持快捷关闭，可使用高级 JSON 参数。";
}
function read(requireModel = true) {
    const value = { ...args.data };
    const codex = $("service").value === "codex";
    for (const id of fields) {
        if (id === "azureRegion" && $("service").value !== "azure") continue;
        if (
            codex &&
            [
                "apiUrl",
                "apiKey",
                "apiProtocol",
                "reasoningMode",
                "requestOptions",
                "extraData",
            ].includes(id)
        )
            continue;
        value[id] = ["requestOptions", "extraData"].includes(id)
            ? jsonField(id)
            : $(id).value.trim();
    }
    if (codex) {
        readCodexProxy(value);
        value.apiUrl = "";
        value.apiKey = "";
        for (const id of [
            "apiProtocol",
            "reasoningMode",
            "requestOptions",
            "extraData",
        ])
            delete value[id];
        if (requireModel && !value.model) throw new Error("请填写模型名称。");
        const detail = modelDetails.find((model) => model.id === value.model);
        if (
            value.reasoningEffort &&
            detail &&
            !detail.supportedReasoningEfforts.includes(value.reasoningEffort)
        )
            throw new Error("该模型不支持所选推理档位，请重新选择。");
        if (!value.cliPath) delete value.cliPath;
        if (!value.reasoningEffort) delete value.reasoningEffort;
    } else {
        delete value.cliPath;
        delete value.reasoningEffort;
        delete value.proxyMode;
        delete value.proxyUrl;
    }
    if (
        value.reasoningMode === "off" &&
        !supportsReasoningOff(value.model, value.service)
    )
        throw new Error("此模型不支持快捷关闭推理，请选择保持现有设置。");
    if (!args.services[value.service])
        throw new Error("请选择支持的接口类型。");
    if (
        requireModel &&
        !NO_MODEL_SERVICES.includes(value.service) &&
        !value.model
    )
        throw new Error(
            value.service === "azureopenai"
                ? "请填写 Azure 部署名称。"
                : "请填写模型名称。",
        );
    if (
        value.service === "qwenmt" &&
        value.model &&
        !value.model.startsWith("qwen-mt-")
    )
        throw new Error(
            "Qwen-MT 仅支持 qwen-mt-* 模型；通用通义模型请选择阿里云接口。",
        );
    if (
        [
            "openai",
            "aliyundashscope",
            "qwenmt",
            "azureopenai",
            "anythingllm",
        ].includes(value.service)
    ) {
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
    if (
        $("service").value === "codex" &&
        $("model").value !== $("models").value
    )
        $("reasoningEffort").value = "";
    $("model").value = $("models").value;
    updateReasoningMode();
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
    const service = $("service").value;
    const codex = service === "codex";
    for (const id of [
        "api-url-field",
        "api-key-field",
        "reasoning-mode-field",
        "advanced",
    ])
        $(id).hidden = codex;
    for (const id of [
        "codex-path-field",
        "codex-reasoning-field",
        "codex-proxy-field",
    ])
        $(id).hidden = !codex;
    $("azure-region-field").hidden = service !== "azure";
    $("model-field").hidden = NO_MODEL_SERVICES.includes(service);
    $("model-label").textContent =
        service === "azureopenai" ? "部署名称" : "模型";
    $("model").placeholder =
        service === "azureopenai"
            ? "填写 Azure 中创建的部署名称"
            : "输入或搜索模型名称";
    // Keep incompatible saved options visible so the user can correct them.
    const commonProtocol = OPENAI_SERVICES.includes(service);
    $("reasoning-mode-field").hidden =
        codex || (!commonProtocol && $("reasoningMode").value === "default");
    $("protocol-field").hidden =
        !commonProtocol && $("apiProtocol").value !== "responses";
    $("request-options-field").hidden =
        !commonProtocol &&
        ["", "{}"].includes($("requestOptions").value.replace(/\s/g, ""));
    $("api-key-field").hidden =
        codex ||
        ["siliconflowfree", "claudecode", "ollama", "xinference"].includes(
            service,
        );
    $("api-url-field").hidden =
        codex || ["siliconflowfree", "deepl"].includes(service);
    $("api-url-label").textContent =
        service === "claudecode" ? "Claude CLI 路径" : "API 地址";
    const hints = {
        azureopenai:
            "填写资源地址或以 /openai/v1 结尾的 v1 地址；下方填写部署名称。",
        azure: "填写 Azure 资源对应的端点和区域。中国区端点：https://api.translator.azure.cn。",
        anythingllm:
            "填写完整工作区聊天地址：http://localhost:3001/api/v1/workspace/工作区标识/chat。",
        claudecode:
            "填写本地服务所在电脑的 claude 命令或完整路径，并提前登录。",
        aliyundashscope:
            "从百炼控制台复制业务空间与地域对应的兼容地址，例如 https://业务空间ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1。旧共享地址仍可用。",
        qwenmt: "从百炼控制台复制业务空间与地域对应的 OpenAI 兼容地址；只使用 Qwen-MT 翻译模型。",
        ollama: "填写 Ollama 主机地址，例如 http://localhost:11434；模型须已在该主机安装。",
        xinference:
            "填写 Xinference 主机地址，例如 http://localhost:9997；模型填写已部署的 model UID。",
    };
    $("api-url-hint").textContent =
        hints[service] ||
        "支持 Base URL 或完整的 /chat/completions、/responses 地址；请保留站点提供的路径。";
    updateCodexProxy();
    $("test").textContent = codex ? "测试连接" : "测试 API";
    $("name").placeholder = codex
        ? "例如：Codex 全文翻译"
        : "留空时使用 API 地址的主机名";
    $("models-hint").textContent = codex
        ? "使用当前登录账号的模型目录；连接测试会确认模型是否可用。"
        : service === "azureopenai"
          ? "部署名称由你在 Azure 中设置，可能与模型名称不同。"
          : MODEL_DISCOVERY_SERVICES.includes(service)
            ? "获取账号可见的模型后选择，或直接手动填写；模型是否可用以连接测试为准。"
            : "请按服务控制台或本地部署填写模型名称。";
    modelDetails = [];
    if (codex && !$("model").value.trim()) $("model").value = "gpt-6-luna";
    const preset = DEFAULT_SERVICES[$("service").value];
    setModels(preset?.models || []);
    $("get-models").disabled =
        busy || !MODEL_DISCOVERY_SERVICES.includes($("service").value);
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
            const catalog = await args.listModels(value);
            if (window.closed || fingerprint() !== before) return;
            const models = Array.isArray(catalog) ? catalog : catalog.models;
            modelDetails = Array.isArray(catalog)
                ? []
                : catalog.modelDetails || [];
            setModels(models);
            updateReasoningMode();
            message(
                models.length
                    ? `已获取 ${models.length} 个模型，请在模型框输入关键词搜索。`
                    : "服务返回了空列表，请手动填写模型名称。",
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
        busy || !MODEL_DISCOVERY_SERVICES.includes($("service").value);
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
    const value =
        id === "azureRegion" && args.isEdit && args.data.service === "azure"
            ? (args.data.azureRegion ??
              args.data.extraData?.azure_region ??
              "chinaeast2")
            : args.data[id];
    if (id === "reasoningEffort" && value) {
        const option = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            "option",
        );
        option.value = value;
        option.textContent = value;
        $(id).append(option);
    }
    $(id).value = ["extraData", "requestOptions"].includes(id)
        ? JSON.stringify(value || {}, null, 2)
        : value ||
          (id === "apiProtocol"
              ? "auto"
              : id === "reasoningMode"
                ? "default"
                : id === "proxyMode"
                  ? "inherit"
                  : "");
    $(id).addEventListener("input", () => {
        tested = "";
        if (id === "model" && $("service").value === "codex")
            $("reasoningEffort").value = "";
        updateReasoningMode();
        updateCodexProxy();
        message("");
    });
    $(id).addEventListener("change", () => {
        tested = "";
        if (id === "model" && $("service").value === "codex")
            $("reasoningEffort").value = "";
        updateReasoningMode();
        updateCodexProxy();
        message("");
    });
}
if (args.isEdit) {
    $("title").textContent = "编辑翻译配置";
    $("save").textContent = "保存";
    $("save-only").hidden = true;
}
let previousService = $("service").value;
$("service").addEventListener("change", () => {
    if (
        !$("apiUrl").value ||
        (!args.isEdit &&
            $("apiUrl").value === DEFAULT_SERVICES[previousService]?.urls?.[0])
    )
        $("apiUrl").value =
            DEFAULT_SERVICES[$("service").value]?.urls?.[0] || "";
    previousService = $("service").value;
    updateService();
    updateReasoningMode();
});
for (const id of ["cliPath", "proxyMode", "proxyUrl"])
    for (const event of ["input", "change"])
        $(id).addEventListener(event, () => {
            modelDetails = [];
            setModels([]);
            updateCodexReasoning();
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
updateReasoningMode();
$("name").focus();
