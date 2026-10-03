import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { URL } from "node:url";

const source = fs.readFileSync(
    new URL("../addon/content/profileEditor.js", import.meta.url),
    "utf8",
);
const markup = fs.readFileSync(
    new URL("../addon/content/llmApiEditor.xhtml", import.meta.url),
    "utf8",
);

function fixture(overrides = {}) {
    const node = () => ({
        value: "",
        textContent: "",
        dataset: {},
        disabled: false,
        listeners: {},
        children: [],
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
    const context = vm.createContext({
        URL,
        document: {
            getElementById: (id) => nodes[id],
            createElementNS: node,
            addEventListener() {},
        },
        window: {
            arguments: [
                {
                    services: {
                        openai: "OpenAI",
                        openaicompatible: "Compatible",
                        codex: "Codex",
                    },
                    data: {
                        name: "Test",
                        service: "openai",
                        model: "deepseek-v4-flash",
                        apiUrl: "https://relay.invalid/v1",
                        apiKey: "test",
                        ...overrides,
                    },
                },
            ],
            addEventListener() {},
        },
    });
    vm.runInContext(source, context);
    return { nodes, context };
}

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
    assert.equal(nodes.model.value, "gpt-6-luna");
    for (const id of [
        "api-url-field",
        "api-key-field",
        "reasoning-mode-field",
        "advanced",
    ])
        assert.equal(nodes[id].hidden, true);
    for (const id of ["codex-path-field", "codex-reasoning-field"])
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
    vm.runInContext(
        'modelDetails = [{id:"gpt-6-luna",defaultReasoningEffort:"medium",supportedReasoningEfforts:["none","low","medium"]}]',
        context,
    );
    context.updateReasoningMode();
    assert.deepEqual(
        nodes.reasoningEffort.children.map((option) => option.value),
        ["", "none", "low", "medium"],
    );
    assert.match(nodes.reasoningEffort.children[0].textContent, /medium/);
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
    vm.runInContext(
        'modelDetails = [{id:"gpt-6-luna",supportedReasoningEfforts:["low","medium"]}]',
        context,
    );
    context.updateReasoningMode();
    assert.equal(nodes.reasoningEffort.children.length, 3);
    for (const callback of nodes.cliPath.listeners.input) callback();
    assert.equal(nodes.reasoningEffort.children.length, 1);
});

test("HTTP service omits Codex settings and retains HTTP fields", () => {
    const { nodes, context } = fixture({
        cliPath: "/opt/codex",
        reasoningEffort: "low",
    });
    assert.equal(nodes["api-url-field"].hidden, false);
    assert.equal(nodes["codex-path-field"].hidden, true);
    const saved = context.read();
    assert.equal(saved.apiKey, "test");
    assert.equal("cliPath" in saved, false);
    assert.equal("reasoningEffort" in saved, false);
});
