/* global AbortController */
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";
import { URL } from "node:url";

const source = fs.readFileSync(
    new URL("../addon/content/profileEditor.js", import.meta.url),
    "utf8",
);
const markup = fs.readFileSync(
    new URL("../addon/content/llmApiManager.xhtml", import.meta.url),
    "utf8",
);

const catalog = {};
new Function(
    "exports",
    ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/llmApiManager.ts", import.meta.url),
            "utf8",
        ),
        { compilerOptions: { module: ts.ModuleKind.CommonJS } },
    ).outputText,
)(catalog);
const labels = Object.fromEntries(
    [
        ...fs
            .readFileSync(
                new URL("../addon/locale/zh-CN/addon.ftl", import.meta.url),
                "utf8",
            )
            .matchAll(/^profile-([\w-]+) = (.*)$/gm),
    ].map((m) => [m[1], m[2]]),
);
function fixture(overrides = {}) {
    const node = () => ({
        value: "",
        textContent: "",
        dataset: {},
        disabled: false,
        listeners: {},
        children: [],
        localName: "input",
        addEventListener(name, callback) {
            (this.listeners[name] ||= []).push(callback);
        },
        append(child) {
            this.children.push(child);
        },
        replaceChildren() {
            this.children = [];
        },
        setAttribute() {},
        focus() {},
    });
    const nodes = Object.fromEntries(
        [...markup.matchAll(/\bid="([^"]+)"/g)].map((match) => [
            match[1],
            node(),
        ]),
    );
    for (const id of [
        "apiProtocol",
        "reasoningMode",
        "reasoningEffort",
        "proxyMode",
        "models",
    ])
        nodes[id].localName = "select";
    const args = {
        presets: catalog.PROVIDER_PRESETS,
        resolvePreset: (api) => catalog.resolveProviderPreset(api)?.id,
        message: (key) => labels[key] || key,
    };
    const context = vm.createContext({
        URL,
        document: {
            getElementById: (id) => nodes[id],
            createElementNS: node,
            addEventListener() {},
        },
        window: { arguments: [args], AbortController, addEventListener() {} },
    });
    vm.runInContext(source, context);
    const editor = context.window.createProfileEditor(args, () => {});
    editor.load({
        profile: {
            key: "test",
            name: "Test",
            service: "openai",
            model: "deepseek-v4-flash",
            apiUrl: "https://relay.invalid/v1",
            apiKey: "test",
            ...overrides,
        },
    });
    context.read = editor.read;
    context.updateReasoningMode = () => nodes.model.listeners.input[0]();
    context.perform = (kind) =>
        nodes[kind === "models" ? "get-models" : "test"].listeners.click[0]();
    context.catalog = (modelDetails) =>
        editor.load({ ...editor.snapshot(), modelDetails });
    context.modelIds = () => editor.snapshot().modelIds;
    return { nodes, context, editor };
}

test("long status details survive draft switches without occupying the footer", () => {
    const { nodes, editor } = fixture();
    const full = "Network error\n" + "Details ".repeat(100);
    editor.message(full, true);
    assert.equal(nodes.status.textContent, "Network error");
    assert.equal(nodes["status-details"].hidden, false);
    assert.equal(nodes["status-details"].open, false);
    assert.equal(nodes["status-full"].textContent, full);
    const draft = editor.snapshot();
    editor.message("OK");
    assert.equal(nodes["status-details"].hidden, true);
    assert.equal(nodes["status-full"].textContent, "");
    editor.load(draft);
    assert.equal(nodes["status-full"].textContent, full);
    assert.equal(nodes.status.dataset.error, "true");
});

test("reasoning editor preserves legacy defaults and saves the selected mode", () => {
    const { nodes, context } = fixture();
    assert.equal(nodes.reasoningMode.value, "default");
    assert.equal(nodes["reasoning-off"].disabled, false);
    nodes.reasoningMode.value = "off";
    assert.equal(context.read().reasoningMode, "off");
});

test("unknown and unsupported variants cannot silently save reasoning off", () => {
    const { nodes, context } = fixture();
    for (const model of [
        "custom",
        "gpt-5.2-pro",
        "gpt-5.4-codex",
        "gpt-6-astra",
    ]) {
        nodes.model.value = model;
        context.updateReasoningMode();
        assert.equal(nodes["reasoning-off"].disabled, true);
        nodes.reasoningMode.value = "off";
        assert.throws(() => context.read(), /不支持/);
    }
    for (const model of [
        "deepseek-flash",
        "gpt-5.1",
        "gpt-5.2",
        "gpt-5.4",
        "gpt-5.5",
        "gpt-5.2-2025-12-11",
    ]) {
        nodes.model.value = model;
        context.updateReasoningMode();
        assert.equal(nodes["reasoning-off"].disabled, false);
    }
});

test("Codex editor hides HTTP fields and removes inherited HTTP settings", () => {
    const { nodes, context } = fixture({
        service: "codex",
        model: "",
        reasoningMode: "off",
        apiProtocol: "responses",
    });
    assert.equal(nodes.model.value, "");
    nodes.model.value = "gpt-6-luna";
    for (const id of ["api-address", "api-key-field", "reasoning-mode-field"])
        assert.equal(nodes[id].hidden, true);
    for (const id of ["cli-path-field", "codex-reasoning-field"])
        assert.equal(nodes[id].hidden, false);
    assert.equal(nodes["get-models"].disabled, false);
    nodes.requestOptions.value = "invalid hidden JSON";
    nodes.extraData.value = "invalid hidden JSON";
    nodes.cliPath.value = "C:\\Program Files\\Codex\\codex.exe";
    const saved = context.read();
    assert.equal(saved.apiKey, "");
    assert.equal(saved.apiUrl, "");
    assert.equal(saved.cliPath, nodes.cliPath.value);
    for (const id of [
        "apiProtocol",
        "reasoningMode",
        "requestOptions",
        "extraData",
        "reasoningEffort",
    ])
        assert.equal(id in saved, false);
    nodes.model.value = "";
    assert.throws(() => context.read(), /模型名称/);
    assert.doesNotThrow(() => context.read(false));
});

test("Codex reasoning options follow the discovered model and retain an explicit saved setting", () => {
    const { nodes, context } = fixture({
        service: "codex",
        model: "gpt-6-luna",
        reasoningEffort: "low",
    });
    assert.equal(nodes.reasoningEffort.value, "low");
    context.catalog([
        {
            id: "gpt-6-luna",
            defaultReasoningEffort: "medium",
            supportedReasoningEfforts: ["none", "low", "medium"],
        },
    ]);
    assert.deepEqual(
        nodes.reasoningEffort.children.map((option) => option.value),
        ["auto", "", "none", "low", "medium"],
    );
    assert.match(nodes.reasoningEffort.children[1].textContent, /medium/);
    assert.equal(context.read().reasoningEffort, "low");
    nodes.reasoningEffort.value = "ultra";
    assert.throws(() => context.read(), /推理档位/);
    nodes.reasoningEffort.value = "";
    assert.equal("reasoningEffort" in context.read(), false);
});

test("a changed Codex CLI path discards discovered capabilities", () => {
    const { nodes, context } = fixture({
        service: "codex",
        model: "gpt-6-luna",
    });
    context.catalog([
        { id: "gpt-6-luna", supportedReasoningEfforts: ["low", "medium"] },
    ]);
    assert.equal(nodes.reasoningEffort.children.length, 4);
    for (const callback of nodes.cliPath.listeners.input) callback();
    assert.equal(nodes.reasoningEffort.children.length, 2);
});

test("HTTP service omits Codex settings and retains HTTP fields", () => {
    const { nodes, context } = fixture({
        cliPath: "/opt/codex",
        reasoningEffort: "low",
        proxyMode: "manual",
        proxyUrl: "http://localhost:7897",
    });
    assert.equal(nodes["api-url-field"].hidden, false);
    assert.equal(nodes["cli-path-field"].hidden, true);
    const saved = context.read();
    assert.equal(saved.apiKey, "test");
    assert.equal("cliPath" in saved, false);
    assert.equal("reasoningEffort" in saved, false);
    assert.equal("proxyMode" in saved, false);
    assert.equal("proxyUrl" in saved, false);
});

test("Codex proxy defaults, validation and mode changes preserve only applicable settings", () => {
    const { nodes, context } = fixture({ service: "codex" });
    assert.equal(nodes.proxyMode.value, "inherit");
    assert.equal(nodes["codex-proxy-field"].hidden, false);
    assert.equal(nodes["codex-proxy-url-field"].hidden, true);
    nodes.proxyMode.value = "manual";
    for (const callback of nodes.proxyMode.listeners.change) callback();
    assert.equal(nodes["codex-proxy-url-field"].hidden, false);
    for (const url of [
        "",
        "localhost:7897",
        "socks5://localhost:7897",
        "http://localhost:0",
        "http://localhost:65536",
        "http://localhost:",
        "http://user:secret@localhost:7897",
        "http://localhost/path",
        "http://localhost?",
        "http://localhost#",
        "http://local host:7897",
    ]) {
        nodes.proxyUrl.value = url;
        assert.throws(() => context.read(), /代理地址/);
    }
    nodes.proxyUrl.value = "http://127.0.0.1:7897/";
    const saved = context.read();
    assert.equal(saved.proxyMode, "manual");
    assert.equal(saved.proxyUrl, "http://127.0.0.1:7897");
    const reopened = fixture(saved);
    assert.equal(reopened.nodes.proxyUrl.value, saved.proxyUrl);
    for (const mode of ["direct", "inherit"]) {
        nodes.proxyMode.value = mode;
        for (const callback of nodes.proxyMode.listeners.change) callback();
        assert.equal(nodes["codex-proxy-url-field"].hidden, true);
        assert.equal("proxyUrl" in context.read(), false);
    }
});

test("changing proxy during discovery rejects the stale model catalog", async () => {
    const { nodes, context } = fixture({ service: "codex" });
    let resolve;
    context.window.arguments[0].listModels = () =>
        new Promise((done) => {
            resolve = done;
        });
    const request = context.perform("models");
    nodes.proxyMode.value = "direct";
    for (const callback of nodes.proxyMode.listeners.change) callback();
    resolve({ models: ["stale-model"], modelDetails: [] });
    await request;
    assert.equal(context.modelIds().length, 0);
    assert.equal(nodes.status.textContent, labels.untested);
});

test("verified providers offer discovery and preserve saved model and endpoint", () => {
    for (const service of [
        "openai",
        "deepseek",
        "mimo",
        "gemini",
        "siliconflow",
    ]) {
        const { nodes, context } = fixture(
            { service, model: "saved-model" },
            true,
        );
        assert.equal(nodes["get-models"].disabled, false);
        assert.equal(nodes.apiUrl.value, "https://relay.invalid/v1");
        assert.equal(context.read().model, "saved-model");
        assert.deepEqual(Array.from(context.modelIds()), []);
    }
});

test("MiMo uses its official endpoint and preserves the selected protocol and thinking mode", () => {
    const preset = catalog.PROVIDER_PRESETS.find(
        (entry) => entry.id === "mimo",
    );
    const { nodes, context } = fixture({
        service: "mimo",
        apiUrl: preset.url,
        model: "mimo-v2.6-flash",
    });
    assert.equal(nodes.apiUrl.value, "https://api.xiaomimimo.com/v1");
    assert.equal(nodes["get-models"].disabled, false);
    for (const model of [
        "mimo-v2.6-flash",
        "mimo-v2.6-pro",
        "mimo-v2.6-pro-ultraspeed",
    ]) {
        nodes.model.value = model;
        context.updateReasoningMode();
        assert.equal(nodes["reasoning-off"].disabled, false);
        nodes.reasoningMode.value = "off";
        nodes.apiProtocol.value = "responses";
        assert.equal(context.read().service, "mimo");
        assert.equal(context.read().apiProtocol, "responses");
        assert.equal(context.read().reasoningMode, "off");
    }
    nodes.model.value = "unknown-mimo-model";
    context.updateReasoningMode();
    assert.equal(nodes["reasoning-off"].disabled, true);
    assert.throws(() => context.read(), /不支持/);
    const relay = fixture({ model: "mimo-v2.6-pro" });
    assert.equal(relay.nodes["reasoning-off"].disabled, false);
});

test("Zhipu retains old profiles and only offers reasoning off for verified models", () => {
    const preset = catalog.PROVIDER_PRESETS.find(
        (entry) => entry.id === "zhipu",
    );
    assert.equal(preset.url, "https://open.bigmodel.cn/api/paas/v4");
    assert.equal(catalog.RETIRED_SERVICES.includes("zhipu"), false);
    const { nodes, context } = fixture({
        service: "zhipu",
        model: "glm-5.3-flash",
        apiUrl: preset.url,
    });
    assert.equal(nodes.test.disabled, false);
    assert.equal(nodes["get-models"].disabled, true);
    for (const model of [
        "glm-5.3",
        "glm-5.3-flash",
        "glm-5.3-flashx",
        "glm-5.3-flashx-legal",
        "unknown-glm",
    ]) {
        nodes.model.value = model;
        context.updateReasoningMode();
        assert.equal(nodes["reasoning-off"].disabled, true);
        nodes.reasoningMode.value = "off";
        assert.throws(() => context.read(), /不支持/);
    }
    nodes.model.value = "glm-5.3-flash";
    context.updateReasoningMode();
    assert.equal(
        nodes["reasoning-hint"].textContent,
        labels["reasoning-required"],
    );
    for (const model of [
        "glm-5.2",
        "glm-5.1",
        "glm-5",
        "glm-5-turbo",
        "glm-4.7",
        "glm-4.6",
        "glm-4.5",
        "glm-4.5-air",
    ]) {
        nodes.model.value = model;
        context.updateReasoningMode();
        assert.equal(nodes["reasoning-off"].disabled, false);
        nodes.reasoningMode.value = "off";
        assert.equal(context.read().service, "zhipu");
        assert.equal(context.read().reasoningMode, "off");
    }
});

test("retired providers retain parameters but cannot test or save", () => {
    for (const service of catalog.RETIRED_SERVICES) {
        const { nodes, context } = fixture({ service, azureRegion: "eastus" });
        assert.equal(nodes["retired-notice"].hidden, false);
        assert.equal(nodes.test.disabled, true);
        assert.equal(nodes.save.disabled, true);
        assert.equal(nodes.apiKey.value, "test");
        assert.match(nodes["legacy-data"].value, /eastus/);
        assert.throws(() => context.read(), /已移除/);
    }
});

test("a failed retest invalidates a previously successful test", async () => {
    const { context } = fixture({ needsTest: false });
    assert.equal(context.read().needsTest, false);
    context.window.arguments[0].test = async () => {
        throw new Error("test failed");
    };
    await context.perform("test");
    assert.equal(context.read().needsTest, true);
});

test("detected runtime is visible but never saved as a manual path", async () => {
    const { nodes, context, editor } = fixture({
        service: "codex",
        model: "model",
    });
    const runtime = {
        source: "desktop",
        version: "0.162.0-alpha.17.2",
        path: "/Applications/Test.app/codex",
        compatible: true,
        attempts: [{ path: "/old/codex", message: "版本过低" }],
    };
    context.window.arguments[0].codexDiscoverySupported = async () => true;
    context.window.arguments[0].discoverCodex = async () => runtime;
    editor.load(editor.snapshot());
    await Promise.resolve();
    assert.equal(nodes["codex-discovery-field"].hidden, false);
    await nodes["codex-detect"].listeners.click[0]();
    assert.match(nodes["codex-runtime-summary"].textContent, /桌面 App.*alpha/);
    assert.equal(nodes["codex-runtime-path"].textContent, runtime.path);
    assert.match(nodes["codex-runtime-attempts"].textContent, /版本过低/);
    assert.equal(nodes.cliPath.value, "");
    assert.equal("cliPath" in editor.read(), false);
    assert.equal("runtime" in editor.read(), false);
    assert.equal(editor.read().needsTest, true);
});

test("old server hides detection and switching a draft discards late runtime", async () => {
    const { nodes, context, editor } = fixture({ service: "codex" });
    context.window.arguments[0].codexDiscoverySupported = async () => false;
    editor.load(editor.snapshot());
    await Promise.resolve();
    assert.equal(nodes["codex-discovery-field"].hidden, true);
    let finish, signal;
    context.window.arguments[0].discoverCodex = (_api, value) => {
        signal = value;
        return new Promise((resolve) => {
            finish = resolve;
        });
    };
    const pending = nodes["codex-detect"].listeners.click[0]();
    nodes.cliPath.value = "/changed/codex";
    nodes.cliPath.listeners.input[0]();
    assert.equal(signal.aborted, true);
    finish({
        source: "desktop",
        version: "0.160.0",
        path: "/old/codex",
        compatible: true,
        attempts: [],
    });
    await pending;
    assert.equal(nodes["codex-runtime-summary"].hidden, true);
    assert.equal(nodes.cliPath.value, "/changed/codex");
});

test("editing while capabilities load does not lose the detection control", async () => {
    const { nodes, context, editor } = fixture({ service: "codex" });
    let finish;
    context.window.arguments[0].codexDiscoverySupported = () =>
        new Promise((resolve) => {
            finish = resolve;
        });
    editor.load(editor.snapshot());
    nodes.model.value = "edited";
    nodes.model.listeners.input[0]();
    finish(true);
    await Promise.resolve();
    assert.equal(nodes["codex-discovery-field"].hidden, false);
    assert.equal(nodes["codex-detect"].disabled, false);
});

test("new profiles prefer Responses and low while saved choices remain intact", () => {
    const { nodes, context } = fixture({ key: "", model: "gpt-6-luna" });
    assert.equal(nodes.apiProtocol.value, "auto");
    assert.equal(nodes.reasoningMode.value, "low");
    assert.equal(nodes["reasoning-off"].disabled, false);
    assert.equal(context.read().reasoningMode, "low");
    const saved = fixture({
        apiProtocol: "chat_completions",
        reasoningMode: "off",
    });
    assert.equal(saved.context.read().apiProtocol, "chat_completions");
    assert.equal(saved.context.read().reasoningMode, "off");
});

test("recommended settings reset protocol and reasoning without replacing credentials", () => {
    const { nodes, context } = fixture({
        apiProtocol: "chat_completions",
        reasoningMode: "off",
    });
    nodes["apply-recommended"].listeners.click[0]();
    const saved = context.read();
    assert.equal(saved.apiProtocol, "auto");
    assert.equal(saved.reasoningMode, "low");
    assert.equal(saved.apiKey, "test");
});

test("endpoint catalog controls disabling reasoning for otherwise unknown models", () => {
    const { nodes, context } = fixture({ model: "vendor/future" });
    assert.equal(nodes["reasoning-off"].disabled, true);
    context.catalog([
        {
            id: "vendor/future",
            supportedReasoningEfforts: ["low"],
            supportsReasoningOff: true,
        },
    ]);
    assert.equal(nodes["reasoning-off"].disabled, false);
    nodes.reasoningMode.value = "off";
    assert.equal(context.read().reasoningMode, "off");
    context.catalog([
        {
            id: "vendor/future",
            supportedReasoningEfforts: ["low"],
            supportsReasoningOff: false,
        },
    ]);
    assert.equal(nodes["reasoning-off"].disabled, true);
    assert.throws(() => context.read(), /不支持/);
});

test("new account profiles prefer low through their native catalog", () => {
    const { nodes, context } = fixture({
        key: "",
        service: "codex",
        model: "gpt-6-luna",
    });
    assert.equal(nodes.reasoningEffort.value, "auto");
    context.catalog([
        { id: "gpt-6-luna", supportedReasoningEfforts: ["medium", "high"] },
    ]);
    assert.equal(context.read().reasoningEffort, "auto");
});

test("DeepL strips inapplicable recommended HTTP settings", () => {
    const { context } = fixture({
        key: "",
        service: "deepl",
        reasoningMode: "low",
        apiProtocol: "auto",
    });
    const saved = context.read();
    assert.equal("reasoningMode" in saved, false);
    assert.equal("apiProtocol" in saved, false);
});
