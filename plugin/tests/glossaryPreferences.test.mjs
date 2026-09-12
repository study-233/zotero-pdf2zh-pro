import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { URL } from "node:url";

const compile = (name) =>
    ts
        .transpileModule(
            fs.readFileSync(
                new URL(`../src/modules/${name}.ts`, import.meta.url),
                "utf8",
            ),
            {
                compilerOptions: {
                    module: ts.ModuleKind.ESNext,
                    target: ts.ScriptTarget.ES2022,
                },
            },
        )
        .outputText.replace(/^import[\s\S]*?;\r?\n/gm, "");
const asModule = (text) =>
    import(
        `data:text/javascript;base64,${Buffer.from(text).toString("base64")}`
    );
const prefs = new Map();
const nodes = new Map();
let selection = false;
let bytes;
let health;
globalThis.__glossaryUiTest = {
    config: { addonRef: "test" },
    getPref: (key) => prefs.get(key),
    setPref: (key, value) => prefs.set(key, value),
    axios: { get: async () => ({ data: health }) },
};
const store = await asModule(
    "const {getPref,setPref}=globalThis.__glossaryUiTest;\n" +
        compile("glossaryStore"),
);
Object.assign(globalThis.__glossaryUiTest, store);
globalThis.addon = {
    data: {
        prefs: {
            window: {
                document: {
                    getElementById: (id) =>
                        nodes.get(id.replace("zotero-prefpane-test-", "")),
                },
            },
        },
    },
};
globalThis.ztoolkit = {
    FilePicker: class {
        async open() {
            return selection;
        }
    },
};
globalThis.IOUtils = { read: async () => bytes };
const ui = await asModule(
    "const {config,getPref,setPref,axios,loadGlossaryEntries,importGlossaryCsv,clearGlossaryEntries}=globalThis.__glossaryUiTest;\n" +
        compile("preferenceScript") +
        "\nexport {importGlossary,refreshServerVersion};",
);

function reset() {
    nodes.clear();
    for (const id of [
        "glossary-import",
        "glossary-clear",
        "glossarySummary",
        "glossaryResult",
        "checkConnection",
        "qualityCapabilities",
        "serverStatus",
        "serverVersion",
        "connectionResult",
        "serverVersionCard",
    ])
        nodes.set(id, {
            disabled: false,
            textContent: "",
            hidden: false,
            setAttribute() {},
        });
    prefs.clear();
    prefs.set("new_serverip", "http://localhost:8890");
    store.importGlossaryCsv("source,target\nold,旧词");
    selection = false;
}

test("cancelling the file picker keeps the previous glossary and restores buttons", async () => {
    reset();
    const saved = prefs.get("glossaryEntries");
    await ui.importGlossary();
    assert.equal(prefs.get("glossaryEntries"), saved);
    assert.equal(nodes.get("glossary-import").disabled, false);
    assert.equal(nodes.get("glossary-clear").disabled, false);
});

test("invalid UTF-8 and invalid CSV show errors without replacing the previous glossary", async () => {
    for (const input of [
        new Uint8Array([0xff]),
        new globalThis.TextEncoder().encode("source,target\nnew,新词\nwrong"),
    ]) {
        reset();
        selection = "glossary.csv";
        bytes = input;
        const saved = prefs.get("glossaryEntries");
        await ui.importGlossary();
        assert.equal(prefs.get("glossaryEntries"), saved);
        assert.match(nodes.get("glossaryResult").textContent, /UTF-8|第 3 行/);
        assert.equal(nodes.get("glossary-import").disabled, false);
    }
});

test("successful import shows the replacement count and the saved terms", async () => {
    reset();
    selection = "glossary.csv";
    bytes = new globalThis.TextEncoder().encode(
        "\uFEFFsource,target\nnew,新词",
    );
    await ui.importGlossary();
    assert.deepEqual(store.loadGlossaryEntries(), [
        { source: "new", target: "新词", tgt_lng: "" },
    ]);
    assert.match(nodes.get("glossaryResult").textContent, /已导入 1 条/);
    assert.match(nodes.get("glossarySummary").textContent, /已保存 1 条/);
});

test("preferences explicitly label missing server capabilities without changing the glossary", async () => {
    reset();
    const saved = prefs.get("glossaryEntries");
    health = { status: "ok", version: "old" };
    await ui.refreshServerVersion();
    assert.match(
        nodes.get("qualityCapabilities").textContent,
        /术语表、定向校对.*不可用/,
    );
    assert.equal(prefs.get("glossaryEntries"), saved);
    health = {
        status: "ok",
        capabilities: { glossaryEntries: true, semanticReview: true },
    };
    await ui.refreshServerVersion();
    assert.match(
        nodes.get("qualityCapabilities").textContent,
        /支持术语表与定向校对/,
    );
});

test("a delayed health response cannot replace the selected server's capability status", async () => {
    reset();
    let resolveOld;
    const oldResponse = new Promise((resolve) => {
        resolveOld = resolve;
    });
    const axios = globalThis.__glossaryUiTest.axios;
    const originalGet = axios.get;
    axios.get = async (url) =>
        url.includes("localhost")
            ? oldResponse
            : {
                  data: {
                      status: "ok",
                      capabilities: {
                          glossaryEntries: true,
                          semanticReview: true,
                      },
                  },
              };
    try {
        const oldCheck = ui.refreshServerVersion();
        prefs.set("new_serverip", "http://new-server");
        await ui.refreshServerVersion();
        resolveOld({ data: { status: "ok" } });
        await oldCheck;
        assert.match(
            nodes.get("qualityCapabilities").textContent,
            /支持术语表与定向校对/,
        );
    } finally {
        axios.get = originalGet;
    }
});
