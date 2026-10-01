import assert from "node:assert/strict";
import fs from "node:fs";
import { URL } from "node:url";
import test from "node:test";
import ts from "typescript";

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
