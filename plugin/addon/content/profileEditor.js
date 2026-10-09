/* eslint-disable no-restricted-globals -- Native configuration dialog. */
/* global window, document, URL */
"use strict";
window.createProfileEditor = function (args, changed) {
    const $ = (id) => document.getElementById(id);
    const t = args.message;
    const fields = [
        "name",
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
    ];
    let base = {},
        preset,
        tested = "",
        sequence = 0,
        controller,
        busy = false,
        statusText = "";
    let modelIds = [],
        modelDetails = [];
    const values = () =>
        Object.fromEntries(fields.map((id) => [id, $(id).value]));
    const fingerprint = () =>
        JSON.stringify(
            fields.filter((id) => id !== "name").map((id) => $(id).value),
        );
    function message(text = "", error = false) {
        statusText = String(text);
        const expandable = statusText.length > 160 || statusText.includes("\n");
        const firstLine = statusText.split("\n")[0];
        $("status").textContent = expandable
            ? firstLine.slice(0, 140) + (firstLine.length > 140 ? "…" : "")
            : statusText;
        $("status").dataset.error = String(error);
        $("status-details").hidden = !expandable;
        $("status-details").open = false;
        $("status-full").textContent = expandable ? statusText : "";
        $("status-full").dataset.error = String(error);
    }
    function buttons() {
        $("test").disabled = busy || !preset;
        $("get-models").disabled = busy || !preset?.discovery;
        $("save").disabled = $("save-only").disabled = !preset;
    }
    function cancel() {
        sequence++;
        controller?.abort();
        controller = undefined;
        if (busy) message(t(tested === fingerprint() ? "tested" : "untested"));
        busy = false;
        buttons();
    }
    function hideModels() {
        $("models").hidden = true;
        $("model").setAttribute("aria-expanded", "false");
    }
    function showModels() {
        const query = $("model").value.toLowerCase();
        const matches = modelIds.filter((id) =>
            id.toLowerCase().includes(query),
        );
        $("models").replaceChildren();
        for (const id of matches) {
            const option = document.createElementNS(
                "http://www.w3.org/1999/xhtml",
                "option",
            );
            option.value = option.textContent = id;
            $("models").append(option);
        }
        $("models").selectedIndex = -1;
        $("models").hidden = !matches.length;
        $("model").setAttribute("aria-expanded", String(!!matches.length));
    }
    function supportsReasoningOff() {
        const model = $("model").value.toLowerCase().trim();
        return (
            ["openai", "deepseek"].includes(preset?.service) &&
            (/^gpt-5\.(1|2|4|5)(-\d{4}-\d{2}-\d{2})?$/.test(model) ||
                [
                    "deepseek-v4-flash",
                    "deepseek-v4-pro",
                    "deepseek-flash",
                ].includes(model))
        );
    }
    function updateReasoning() {
        $("reasoning-off").disabled = !supportsReasoningOff();
        $("reasoning-hint").textContent = t(
            supportsReasoningOff()
                ? "reasoning-supported"
                : "reasoning-unsupported",
        );
        const selected = $("reasoningEffort").value;
        const detail = modelDetails.find(
            (entry) => entry.id === $("model").value.trim(),
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
                t("model-default") +
                    (detail?.defaultReasoningEffort
                        ? " · " + detail.defaultReasoningEffort
                        : "");
            $("reasoningEffort").append(option);
        }
        $("reasoningEffort").value = efforts.includes(selected) ? selected : "";
        $("codex-proxy-url-field").hidden =
            preset?.service !== "codex" || $("proxyMode").value !== "manual";
    }
    function onChange(id) {
        cancel();
        if (id !== "name") {
            tested = "";
            message(t("untested"));
        }
        if (id === "model") $("reasoningEffort").value = "";
        if (
            ["apiUrl", "apiKey", "cliPath", "proxyMode", "proxyUrl"].includes(
                id,
            )
        ) {
            modelIds = [];
            modelDetails = [];
            hideModels();
        }
        updateReasoning();
        if (id === "model") showModels();
        changed();
    }
    function load(state) {
        cancel();
        base = { ...state.profile };
        preset = args.presets.find(
            (item) => item.id === (state.presetId || args.resolvePreset(base)),
        );
        const source = state.values || base;
        for (const id of fields) {
            let value = source[id];
            if (
                !state.values &&
                id === "cliPath" &&
                preset?.service === "claudecode"
            )
                value = base.apiUrl;
            if (["requestOptions", "extraData"].includes(id)) {
                value =
                    typeof value === "string"
                        ? value
                        : JSON.stringify(value || {}, null, 2);
            }
            if (id === "reasoningEffort") {
                $("reasoningEffort").replaceChildren();
                const option = document.createElementNS(
                    "http://www.w3.org/1999/xhtml",
                    "option",
                );
                option.value = option.textContent = value || "";
                $("reasoningEffort").append(option);
            }
            $(id).value =
                value ??
                ({
                    apiProtocol: "chat_completions",
                    reasoningMode: "default",
                    proxyMode: "inherit",
                }[id] ||
                    "");
        }
        tested =
            state.tested ?? (base.needsTest === false ? fingerprint() : "");
        modelIds = state.modelIds || [];
        modelDetails = state.modelDetails || [];
        const retired = !preset;
        const codex = preset?.service === "codex";
        const cli = codex || preset?.service === "claudecode";
        $("platform-name").textContent = preset?.label || base.service;
        $("retired-notice").hidden = !retired;
        $("api-key-field").hidden = !retired && !preset.key;
        $("model-field").hidden = !retired && !preset.model;
        $("api-address").hidden =
            !retired && (cli || preset.service === "deepl");
        $("api-url-field").hidden = !!preset?.url && !state.urlExpanded;
        $("url-summary-row").hidden = !$("api-url-field").hidden;
        $("url-summary").textContent = base.apiUrl || preset?.url || "";
        $("url-summary").title = $("url-summary").textContent;
        $("api-url-hint").textContent = t(
            preset?.service === "aliyundashscope"
                ? "aliyun-url-hint"
                : "url-hint",
        );
        $("account-hint").hidden = !cli;
        $("account-hint").textContent = t(codex ? "codex-hint" : "claude-hint");
        $("protocol-field").hidden =
            codex || (!preset?.protocol && source.apiProtocol !== "responses");
        $("reasoning-mode-field").hidden =
            codex || (!preset?.protocol && source.reasoningMode !== "off");
        $("request-options-field").hidden =
            codex ||
            (!preset?.protocol &&
                ["", "{}"].includes(
                    $("requestOptions").value.replace(/\s/g, ""),
                ));
        $("cli-path-field").hidden = !cli;
        $("cliPath").placeholder = codex ? "codex" : "claude";
        $("codex-reasoning-field").hidden = $("codex-proxy-field").hidden =
            !codex;
        $("extra-data-field").hidden = codex;
        $("legacy-data-field").hidden = !retired;
        const legacy = { ...base };
        delete legacy.apiKey;
        $("legacy-data").value = retired ? JSON.stringify(legacy, null, 2) : "";
        $("advanced").open = !!state.advanced;
        $("apiKey").type = "password";
        $("reveal").textContent = t("show");
        $("reveal").setAttribute("aria-pressed", "false");
        updateReasoning();
        hideModels();
        buttons();
        message(
            state.status ?? (base.needsTest === false ? t("tested") : ""),
            state.error,
        );
    }
    function snapshot() {
        return {
            profile: { ...base },
            presetId: preset?.id,
            values: values(),
            tested,
            modelIds,
            modelDetails,
            advanced: $("advanced").open,
            urlExpanded: !$("api-url-field").hidden,
            status: statusText,
            error: $("status").dataset.error === "true",
        };
    }
    function jsonField(id) {
        try {
            const value = JSON.parse($(id).value.trim() || "{}");
            if (!value || Array.isArray(value) || typeof value !== "object")
                throw new Error();
            return value;
        } catch {
            throw new Error(t("json-error"));
        }
    }
    function read(requireModel = true) {
        if (!preset) throw new Error(t("retired"));
        const raw = values();
        const value = {
            ...base,
            ...Object.fromEntries(
                Object.entries(raw).map(([key, val]) => [key, val.trim()]),
            ),
            service: preset.service,
            providerPreset: preset.id,
        };
        const codex = preset.service === "codex";
        const claude = preset.service === "claudecode";
        for (const id of ["extraData", "requestOptions"])
            value[id] = codex ? {} : jsonField(id);
        if (requireModel && preset.model && !value.model)
            throw new Error(t("model-required"));
        if (preset.key && !value.apiKey) throw new Error(t("key-required"));
        if (!preset.key) value.apiKey = "";
        if (!preset.model) value.model = "";
        if (codex || preset.service === "deepl") value.apiUrl = "";
        else if (claude) value.apiUrl = value.cliPath || "claude";
        else {
            let url;
            try {
                url = new URL(value.apiUrl);
            } catch {
                throw new Error(t("url-error"));
            }
            if (
                !["http:", "https:"].includes(url.protocol) ||
                url.search ||
                url.hash ||
                url.username ||
                url.password
            )
                throw new Error(t("url-error"));
        }
        if (!codex && value.reasoningMode === "off" && !supportsReasoningOff())
            throw new Error(t("reasoning-error"));
        if (codex) {
            if (!["inherit", "manual", "direct"].includes(value.proxyMode))
                throw new Error(t("proxy-error"));
            if (value.proxyMode === "manual") {
                let url;
                try {
                    url = new URL(value.proxyUrl);
                } catch {
                    throw new Error(t("proxy-error"));
                }
                if (
                    !/^https?:\/\//i.test(value.proxyUrl) ||
                    /[\s\\]/.test(value.proxyUrl) ||
                    Array.from(value.proxyUrl).some(
                        (char) =>
                            char.charCodeAt(0) < 0x20 ||
                            char.charCodeAt(0) === 0x7f,
                    ) ||
                    !["http:", "https:"].includes(url.protocol) ||
                    !url.hostname ||
                    url.username ||
                    url.password ||
                    /^https?:\/\/[^/]*@/i.test(value.proxyUrl) ||
                    (url.pathname && url.pathname !== "/") ||
                    /[?#]/.test(value.proxyUrl) ||
                    url.port === "0" ||
                    /:\/?$/.test(value.proxyUrl.replace(/^https?:\/\//i, ""))
                )
                    throw new Error(t("proxy-error"));
                value.proxyUrl = url.origin;
            } else delete value.proxyUrl;
            const detail = modelDetails.find(
                (entry) => entry.id === value.model,
            );
            if (
                value.reasoningEffort &&
                detail &&
                !detail.supportedReasoningEfforts.includes(
                    value.reasoningEffort,
                )
            )
                throw new Error(t("effort-error"));
            for (const id of [
                "apiProtocol",
                "reasoningMode",
                "requestOptions",
                "extraData",
            ])
                delete value[id];
            if (!value.cliPath) delete value.cliPath;
            if (!value.reasoningEffort) delete value.reasoningEffort;
        } else {
            for (const id of [
                "cliPath",
                "reasoningEffort",
                "proxyMode",
                "proxyUrl",
            ])
                delete value[id];
        }
        if (!value.name)
            value.name =
                preset.label + (value.model ? " · " + value.model : "");
        value.needsTest = tested !== fingerprint();
        delete value.activate;
        return value;
    }
    async function perform(kind) {
        if (busy) return;
        const current = ++sequence;
        const before = fingerprint();
        controller = new window.AbortController();
        try {
            const value = read(kind === "test");
            if (kind === "test") tested = "";
            busy = true;
            buttons();
            message(t(kind === "test" ? "testing" : "loading-models"));
            if (kind === "test") {
                const result = await args.test(value, controller.signal);
                if (
                    window.closed ||
                    sequence !== current ||
                    fingerprint() !== before
                )
                    return;
                tested = before;
                message(result);
            } else {
                const catalog = await args.listModels(value, controller.signal);
                if (
                    window.closed ||
                    sequence !== current ||
                    fingerprint() !== before
                )
                    return;
                modelIds = catalog.models;
                modelDetails = catalog.modelDetails || [];
                updateReasoning();
                message(
                    t(modelIds.length ? "models-loaded" : "models-empty", {
                        count: modelIds.length,
                    }),
                );
                $("model").focus();
                showModels();
            }
            changed();
        } catch (error) {
            if (!window.closed && sequence === current) {
                message(error.message || t("failed"), true);
                changed();
            }
        } finally {
            if (!window.closed && sequence === current) {
                busy = false;
                controller = undefined;
                buttons();
            }
        }
    }
    for (const id of fields) {
        $(id).addEventListener("input", () => onChange(id));
        // Select controls do not consistently emit input in older Gecko.
        if ($(id).localName === "select")
            $(id).addEventListener("change", () => onChange(id));
    }
    function chooseModel() {
        if ($("models").selectedIndex < 0) return;
        $("model").value = $("models").value;
        onChange("model");
        hideModels();
        $("model").focus();
    }
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
    $("edit-url").addEventListener("click", () => {
        $("url-summary-row").hidden = true;
        $("api-url-field").hidden = false;
        $("apiUrl").focus();
    });
    $("reveal").addEventListener("click", () => {
        const reveal = $("apiKey").type === "password";
        $("apiKey").type = reveal ? "text" : "password";
        $("reveal").textContent = t(reveal ? "hide" : "show");
        $("reveal").setAttribute("aria-pressed", String(reveal));
    });
    $("test").addEventListener("click", () => perform("test"));
    $("get-models").addEventListener("click", () => perform("models"));
    return { load, snapshot, read, cancel, message };
};
