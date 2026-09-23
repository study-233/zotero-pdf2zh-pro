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

function fixture() {
    const node = () => ({
        value: "",
        textContent: "",
        dataset: {},
        disabled: false,
        addEventListener() {},
        append() {},
        replaceChildren() {},
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
                    },
                    data: {
                        name: "Test",
                        service: "openai",
                        model: "deepseek-v4-flash",
                        apiUrl: "https://relay.invalid/v1",
                        apiKey: "test",
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
