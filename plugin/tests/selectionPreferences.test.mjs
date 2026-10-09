import assert from "node:assert/strict";
import fs from "node:fs";
import { URL } from "node:url";
import test from "node:test";
import ts from "typescript";

test("model menu follows saved profiles, keeps document choice, and invalidates only effective changes", () => {
    const nodes = new Map();
    const makeNode = () => ({
        children: [],
        listeners: {},
        attributes: {},
        isConnected: true,
        addEventListener(type, fn) {
            this.listeners[type] = fn;
        },
        setAttribute(key, value) {
            this.attributes[key] = value;
        },
        replaceChildren() {
            this.children = [];
        },
        append(child) {
            this.children.push(child);
        },
        querySelector() {
            return this;
        },
    });
    const node = (id) => {
        if (!nodes.has(id)) nodes.set(id, makeNode());
        return nodes.get(id);
    };
    const prefs = new Map([["selectedApiKey", "a"]]);
    const state = {
        profiles: [
            { key: "a", model: "document-model" },
            { key: "b", model: "selection-model" },
        ],
        changes: 0,
    };
    const events = {};
    const window = {
        document: { getElementById: node, createElementNS: makeNode },
        addEventListener: (type, fn) => {
            events[type] = fn;
        },
        removeEventListener() {},
        setTimeout: (fn) => fn(),
    };
    const imports = {
        "../../package.json": { config: { addonRef: "test" } },
        "../utils/prefs": {
            getPref: (key) => prefs.get(key),
            setPref: (key, value) => prefs.set(key, value),
        },
        "../utils/locale": { getString: (key) => key },
        "./profileStore": { loadProfiles: () => state.profiles },
        "./llmApiManager": {
            profileLabel: (api) => `${api.key} · ${api.model}`,
        },
        "./selectionDictionaryDownload": {
            dictionaryDownloadState: () => ({ phase: "idle" }),
            subscribeDictionaryDownload: () => () => {},
            refreshDictionaryDownload: async () => {},
        },
    };
    const exports = {};
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/selectionPreferences.ts", import.meta.url),
            "utf8",
        ),
        { compilerOptions: { module: ts.ModuleKind.CommonJS } },
    ).outputText;
    new Function("require", "exports", code)((name) => imports[name], exports);
    exports.registerSelectionPreferences(window, () => state.changes++);
    const menu = node("zotero-prefpane-test-selection-model");
    assert.equal(menu.children.length, 3);
    assert.equal(menu.value, "");
    assert.equal(state.changes, 0);
    const provider = node("zotero-prefpane-test-selection-provider");
    assert.deepEqual(
        provider.children.map((n) => n.attributes.value),
        ["bing", "profile", "a", "b"],
    );
    assert.equal(provider.value, "bing");
    assert.equal(
        node("zotero-prefpane-test-selection-model-field").hidden,
        false,
    );
    assert.equal(node("zotero-prefpane-test-selection-trigger").value, "auto");
    assert.equal(
        node("zotero-prefpane-test-selection-display").value,
        "floating",
    );
    prefs.set("selectedApiKey", "b");
    events["profiles-changed"]();
    assert.equal(state.changes, 1);
    menu.value = "b";
    menu.listeners.command();
    assert.equal(prefs.get("selectionApiKey"), "b");
    assert.equal(prefs.get("selectedApiKey"), "b");
    assert.equal(state.changes, 2);
    prefs.set("selectedApiKey", "a");
    events["profiles-changed"]();
    assert.equal(state.changes, 2);
    state.profiles[1].model = "edited-model";
    events["profiles-changed"]();
    assert.equal(state.changes, 3);
    assert.match(menu.children[2].attributes.label, /edited-model/);
    state.profiles[1].name = "renamed";
    state.profiles[1].needsTest = false;
    events["profiles-changed"]();
    assert.equal(state.changes, 3);
    state.profiles.pop();
    events["profiles-changed"]();
    assert.equal(state.changes, 4);
    assert.equal(prefs.get("selectionApiKey"), "b");
    assert.equal(menu.value, "b");
    assert.equal(
        node("zotero-prefpane-test-selection-model-status").hidden,
        false,
    );
    menu.value = "";
    menu.listeners.command();
    assert.equal(
        node("zotero-prefpane-test-selection-model-status").hidden,
        true,
    );
    assert.equal(prefs.get("selectedApiKey"), "a");
    provider.value = "b";
    provider.listeners.command();
    assert.equal(prefs.get("selectionTranslationProvider"), "profile");
    assert.equal(prefs.get("selectionApiKey"), "b");
    assert.equal(
        node("zotero-prefpane-test-selection-model-field").hidden,
        true,
    );
    assert.equal(
        node("zotero-prefpane-test-selection-model-shared").hidden,
        false,
    );
    provider.value = "bing";
    provider.listeners.command();
    assert.equal(prefs.get("selectionApiKey"), "b");
    assert.equal(
        node("zotero-prefpane-test-selection-model-field").hidden,
        false,
    );
    const trigger = node("zotero-prefpane-test-selection-trigger");
    trigger.value = "click";
    trigger.listeners.command();
    const display = node("zotero-prefpane-test-selection-display");
    display.value = "sidebar";
    display.listeners.command();
    assert.equal(prefs.get("selectionTrigger"), "click");
    assert.equal(prefs.get("selectionDisplayMode"), "sidebar");
});

test("settings import cancel is nonmutating; success enables Collins and invalidates old work", async () => {
    const nodes = new Map();
    for (const id of [
        "selection-provider",
        "selection-dictionary",
        "selection-import",
        "selection-dictionary-status",
        "selection-download",
        "selection-download-error",
        "selection-download-progress",
        "selection-service-hint",
        "selection-dictionary-details",
    ])
        nodes.set("zotero-prefpane-test-" + id, {
            listeners: {},
            addEventListener(t, fn) {
                this.listeners[t] = fn;
            },
        });
    const state = { path: undefined, imports: [], changed: 0, failed: false },
        prefs = new Map();
    const exports = {};
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/selectionPreferences.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    const imports = {
        "../utils/locale": {
            getString: (key) =>
                key === "dictionary-import-error" ? "已有词库保持不变" : key,
        },
        "./selectionDictionaryDownload": {
            dictionaryDownloadState: () => ({ phase: "idle" }),
            subscribeDictionaryDownload: () => () => {},
            refreshDictionaryDownload: async () => {},
            dictionaryChoiceChanged() {},
            dictionaryImported: async () => {},
        },
        "../../package.json": { config: { addonRef: "test" } },
        "../utils/prefs": {
            getPref: (k) => prefs.get(k),
            setPref: (k, v) => prefs.set(k, v),
        },
        "./selectionDictionaryStore": {
            getImportedDictionaryInfo: async () => undefined,
            importODHDictionary: async (p) => {
                state.imports.push(p);
                if (state.failed) throw new Error("private path");
                prefs.set("selectionDictionary", "collins");
            },
        },
        "./diagnostics": { recordDiagnostic() {} },
    };
    new Function("require", "exports", "ztoolkit", code)(
        (name) => imports[name],
        exports,
        {
            FilePicker: class {
                async open() {
                    return state.path;
                }
            },
        },
    );
    exports.registerSelectionPreferences(
        {
            document: { getElementById: (id) => nodes.get(id) },
            addEventListener() {},
            setTimeout: (fn) => fn(),
        },
        () => state.changed++,
    );
    const flush = async () => {
        for (let i = 0; i < 15; i++) await Promise.resolve();
    };
    await flush();
    const button = nodes.get("zotero-prefpane-test-selection-import");
    button.listeners.click();
    await flush();
    assert.equal(state.imports.length, 0);
    assert.equal(button.disabled, false);
    state.path = "chosen.json";
    button.listeners.click();
    await flush();
    assert.deepEqual(state.imports, ["chosen.json"]);
    assert.equal(state.changed, 1);
    assert.equal(
        nodes.get("zotero-prefpane-test-selection-dictionary").value,
        "collins",
    );
    state.failed = true;
    button.listeners.click();
    await flush();
    assert.equal(state.changed, 1);
    assert.match(
        nodes.get("zotero-prefpane-test-selection-download-error").textContent,
        /已有词库保持不变/,
    );
    assert.equal(button.disabled, false);
    const provider = nodes.get("zotero-prefpane-test-selection-provider");
    provider.value = "profile";
    provider.listeners.command();
    assert.equal(prefs.get("selectionTranslationProvider"), "profile");
    assert.equal(state.changed, 2);
});
