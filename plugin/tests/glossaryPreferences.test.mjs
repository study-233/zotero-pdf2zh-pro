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
let packData = [];
const selectedPacks = new Map();
const packActions = [];
const messages = new Map(
    fs
        .readFileSync(
            new URL("../addon/locale/zh-CN/preferences.ftl", import.meta.url),
            "utf8",
        )
        .split(/\r?\n/)
        .filter((line) => line.includes(" = "))
        .map((line) => line.split(" = ")),
);
const client = {
    list: async () => ({ packs: packData }),
    check: async () => ({ packs: packData }),
    download: async () => {},
    cancel: async () => {},
    remove: async () => {},
};
const walk = (node, id) =>
    node.id === id
        ? node
        : node.children?.map((child) => walk(child, id)).find(Boolean);
const document = {
    getElementById: (id) =>
        nodes.get(id.replace("zotero-prefpane-test-", "")) ||
        [...nodes.values()].map((node) => walk(node, id)).find(Boolean),
    createElementNS: (_namespace, tag) => makeNode(tag),
    createTextNode: (text) =>
        Object.assign(makeNode("text"), { textContent: text }),
    l10n: { formatValue: async (key) => messages.get(key) },
};
function makeNode(tag = "div") {
    return {
        tag,
        ownerDocument: document,
        disabled: false,
        checked: false,
        textContent: "",
        hidden: false,
        children: [],
        attributes: new Map(),
        listeners: new Map(),
        get childNodes() {
            return this.children;
        },
        setAttribute(name, value) {
            this.attributes.set(name, value);
            if (name.startsWith("data-l10n-")) {
                const args = JSON.parse(
                    this.attributes.get("data-l10n-args") || "{}",
                );
                this.textContent = (
                    messages.get(this.attributes.get("data-l10n-id")) || ""
                ).replace(/\{\s*\$(\w+)\s*\}/g, (_, key) => args[key] ?? "");
            }
        },
        getAttribute(name) {
            return this.attributes.get(name);
        },
        removeAttribute(name) {
            this.attributes.delete(name);
        },
        append(...children) {
            this.children.push(...children);
        },
        replaceChildren(...children) {
            this.children = children;
        },
        addEventListener(name, callback) {
            this.listeners.set(name, callback);
        },
        focus() {
            document.activeElement = this;
        },
    };
}
globalThis.__glossaryUiTest = {
    config: { addonRef: "test" },
    version: "test",
    getPref: (key) => prefs.get(key),
    setPref: (key, value) => prefs.set(key, value),
    axios: { get: async () => ({ data: health }) },
    listGlossaryPacks: (...args) => client.list(...args),
    checkGlossaryUpdates: (...args) => client.check(...args),
    downloadGlossaryPack: async (...args) => {
        packActions.push(["download", ...args]);
        await client.download(...args);
    },
    cancelGlossaryDownload: async (...args) => {
        packActions.push(["cancel", ...args]);
        await client.cancel(...args);
    },
    removeGlossaryPack: async (...args) => {
        packActions.push(["remove", ...args]);
        await client.remove(...args);
    },
    getSelectedGlossaryPackIds: (server) => {
        new URL(server);
        return selectedPacks.get(server) || [];
    },
    setSelectedGlossaryPackIds: (server, ids) => selectedPacks.set(server, ids),
    supportsGlossaryPackLanguage: (source, target) =>
        source === "en" && target === "zh-CN",
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
                document,
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
    "const {config,version,getPref,setPref,axios,loadGlossaryEntries,importGlossaryCsv,clearGlossaryEntries,listGlossaryPacks,checkGlossaryUpdates,downloadGlossaryPack,cancelGlossaryDownload,removeGlossaryPack,getSelectedGlossaryPackIds,setSelectedGlossaryPackIds,supportsGlossaryPackLanguage}=globalThis.__glossaryUiTest;\n" +
        compile("preferenceScript") +
        "\nexport {importGlossary,refreshServerVersion,refreshGlossaryPacks,renderGlossaryPacks,runGlossaryPackAction};",
);

function reset() {
    nodes.clear();
    selectedPacks.clear();
    packActions.length = 0;
    document.activeElement = null;
    packData = [];
    client.list = async () => ({ packs: packData });
    client.check = async () => ({ packs: packData });
    client.download = client.cancel = client.remove = async () => {};
    health = {
        status: "ok",
        capabilities: {
            glossaryEntries: true,
            semanticReview: true,
            glossaryPacks: true,
        },
    };
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
        nodes.set(id, makeNode());
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
        /术语表或校对不可用/,
    );
    assert.equal(prefs.get("glossaryEntries"), saved);
    health = {
        status: "ok",
        capabilities: { glossaryEntries: true, semanticReview: true },
    };
    await ui.refreshServerVersion();
    assert.equal(nodes.get("qualityCapabilities").hidden, true);
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
        assert.equal(nodes.get("qualityCapabilities").hidden, true);
    } finally {
        axios.get = originalGet;
    }
});

const serverURL = "http://localhost:8890";
const ids = ["computing", "building", "physics", "environment", "medicine"];
const pack = (id = "computing", fields = {}) => ({
    id,
    version: "2026.09",
    sha256: "a".repeat(64),
    entryCount: 200,
    sizeBytes: 4096,
    sourceLang: "en",
    targetLang: "zh-CN",
    sources: [
        {
            name: "Official data",
            url: "https://example.org/data",
            license: "Open license",
            licenseUrl: "https://example.org/license",
        },
    ],
    installedVersions: [],
    status: "not_downloaded",
    ...fields,
});
const installed = (id = "computing", fields = {}) =>
    pack(id, {
        status: "installed",
        installedVersions: [
            {
                version: "2026.09",
                sha256: "a".repeat(64),
                entryCount: 200,
                sizeBytes: 4096,
            },
        ],
        ...fields,
    });
const node = (id) => document.getElementById(`zotero-prefpane-test-${id}`);
const descendants = (root) => [root, ...root.children.flatMap(descendants)];
const choose = (id, checked) => {
    const checkbox = node(`pack-${id}`);
    checkbox.checked = checked;
    checkbox.listeners.get("change")();
};
function packUI() {
    for (const id of [
        "glossaryPacks",
        "glossaryPackSources",
        "glossaryPacksResult",
        "glossaryPackLanguage",
        "glossary-check-updates",
    ])
        nodes.set(id, makeNode());
}

test("five categories start unchecked and downloading never enables a pack", async () => {
    reset();
    packUI();
    packData = ids.map((id) => pack(id));
    await ui.refreshServerVersion();
    for (const id of ids) {
        assert.equal(node(`pack-${id}`).checked, false);
        assert.equal(node(`pack-${id}`).disabled, true);
        assert.equal(node(`pack-${id}-download`).disabled, false);
    }
    assert.equal(node("connectionResult").hidden, true);
    assert.equal(node("qualityCapabilities").hidden, true);
    assert.ok(
        descendants(node("glossaryPacks")).some(
            (item) => item.textContent === "200 条",
        ),
    );
    client.download = async () => {
        packData[0] = installed();
    };
    await ui.runGlossaryPackAction("computing", "download");
    assert.deepEqual(packActions, [["download", serverURL, "computing"]]);
    assert.equal(node("pack-computing").checked, false);
    assert.equal(node("pack-computing").disabled, false);
    assert.equal(selectedPacks.size, 0);
    choose("computing", true);
    assert.deepEqual(selectedPacks.get(serverURL), ["computing"]);
    const links = descendants(node("glossaryPackSources")).filter(
        (item) => item.tag === "a",
    );
    assert.equal(links[0].href, "https://example.org/data");
    assert.equal(links[1].href, "https://example.org/license");
});

test("failed updates keep installed packs selectable and provide retry and removal", async () => {
    reset();
    packUI();
    packData = [
        installed("computing", {
            status: "failed",
            download: { state: "failed", error: "Download interrupted" },
        }),
    ];
    selectedPacks.set(serverURL, ["computing"]);
    await ui.refreshServerVersion();
    assert.equal(node("pack-computing").checked, true);
    assert.equal(node("pack-computing").disabled, false);
    assert.equal(node("pack-computing-download").textContent, "更新");
    assert.equal(node("pack-computing-download").disabled, false);
    client.remove = async () => {
        packData = [pack()];
    };
    await ui.runGlossaryPackAction("computing", "remove");
    assert.deepEqual(packActions, [["remove", serverURL, "computing"]]);
    assert.deepEqual(selectedPacks.get(serverURL), []);
    assert.equal(node("pack-computing").checked, false);
    assert.equal(node("pack-computing").disabled, true);
});

test("downloads show progress and can be cancelled without selecting the pack", async () => {
    reset();
    packUI();
    packData = [
        pack("computing", {
            status: "downloading",
            download: {
                state: "downloading",
                receivedBytes: 512,
                totalBytes: 1024,
            },
        }),
    ];
    await ui.refreshServerVersion();
    assert.ok(
        descendants(node("glossaryPacks")).some(
            (item) => item.textContent === "下载中 50%",
        ),
    );
    assert.equal(node("pack-computing-download"), undefined);
    assert.equal(node("pack-computing-cancel").disabled, false);
    client.cancel = async () => {
        packData = [pack()];
    };
    await ui.runGlossaryPackAction("computing", "cancel");
    assert.deepEqual(packActions, [["cancel", serverURL, "computing"]]);
    assert.equal(node("pack-computing-download").disabled, false);
    assert.equal(selectedPacks.size, 0);
});

test("old and unreachable servers disable management but allow clearing saved selections and CSV import", async () => {
    for (const unavailable of ["old", "offline"]) {
        reset();
        packUI();
        selectedPacks.set(serverURL, ["computing"]);
        if (unavailable === "old")
            health = {
                status: "ok",
                capabilities: { glossaryEntries: true, semanticReview: true },
            };
        else
            client.list = async () => {
                throw new Error("offline");
            };
        await ui.refreshServerVersion();
        assert.equal(node("pack-computing").disabled, false);
        assert.equal(node("pack-building").disabled, true);
        assert.equal(node("pack-computing-download").disabled, true);
        assert.equal(node("glossary-check-updates").disabled, true);
        assert.equal(node("glossary-import").disabled, false);
        choose("computing", false);
        assert.deepEqual(selectedPacks.get(serverURL), []);
        assert.equal(node("pack-computing").disabled, true);
        selection = "glossary.csv";
        bytes = new globalThis.TextEncoder().encode("source,target\nnew,新词");
        await ui.importGlossary();
        assert.equal(store.loadGlossaryEntries()[0].source, "new");
    }
});

test("unsupported languages show an inactive notice and allow unchecking existing selections", async () => {
    reset();
    packUI();
    packData = [installed(), installed("building")];
    selectedPacks.set(serverURL, ["computing"]);
    prefs.set("targetLang", "ja");
    await ui.refreshServerVersion();
    assert.equal(node("glossaryPackLanguage").hidden, false);
    assert.equal(node("pack-computing").disabled, false);
    assert.equal(node("pack-building").disabled, true);
    choose("computing", false);
    assert.deepEqual(selectedPacks.get(serverURL), []);
    prefs.set("targetLang", "zh-CN");
    ui.renderGlossaryPacks();
    assert.equal(node("glossaryPackLanguage").hidden, true);
    assert.equal(node("pack-building").disabled, false);
});

test("empty or invalid server addresses do not break the preferences or custom glossary", async () => {
    for (const url of ["", "invalid address"]) {
        reset();
        packUI();
        prefs.set("new_serverip", url);
        const saved = prefs.get("glossaryEntries");
        await ui.refreshServerVersion();
        assert.equal(node("pack-computing").disabled, true);
        assert.equal(node("glossary-import").disabled, false);
        assert.equal(prefs.get("glossaryEntries"), saved);
    }
});

test("an update check failure keeps installed packs usable", async () => {
    reset();
    packUI();
    packData = [installed()];
    selectedPacks.set(serverURL, ["computing"]);
    await ui.refreshServerVersion();
    client.check = async () => {
        throw new Error("Catalog unavailable");
    };
    await ui.refreshGlossaryPacks(true);
    assert.equal(
        node("glossaryPacksResult").textContent,
        "Catalog unavailable",
    );
    assert.equal(node("pack-computing").checked, true);
    assert.equal(node("pack-computing").disabled, false);
    assert.equal(node("pack-computing-remove").disabled, false);
    assert.equal(node("glossary-check-updates").disabled, false);
});

test("delayed catalog responses and management actions cannot replace another server's UI", async () => {
    reset();
    packUI();
    packData = [installed()];
    await ui.refreshServerVersion();
    let resolveCatalog;
    client.list = async (server) =>
        server === serverURL
            ? new Promise((resolve) => {
                  resolveCatalog = resolve;
              })
            : { packs: [installed("building")] };
    const oldCatalog = ui.refreshGlossaryPacks();
    prefs.set("new_serverip", "http://new-server");
    selectedPacks.set("http://new-server", ["building"]);
    await ui.refreshServerVersion();
    resolveCatalog({ packs: [installed()] });
    await oldCatalog;
    assert.equal(node("pack-building").checked, true);
    assert.equal(node("pack-building").disabled, false);
    assert.equal(node("pack-computing").disabled, true);

    let resolveDownload;
    client.download = async () =>
        new Promise((resolve) => {
            resolveDownload = resolve;
        });
    const oldDownload = ui.runGlossaryPackAction("building", "download");
    prefs.set("new_serverip", serverURL);
    client.list = async () => ({ packs: [installed()] });
    await ui.refreshServerVersion();
    resolveDownload();
    await oldDownload;
    assert.equal(node("pack-computing").disabled, false);
    assert.equal(node("pack-building").disabled, true);
    assert.deepEqual(selectedPacks.get("http://new-server"), ["building"]);
});

test("closing preferences invalidates a pending health check before it loads packs", async () => {
    reset();
    packUI();
    for (const field of ["sourceLang", "targetLang"]) {
        const select = makeNode("menulist");
        select.querySelector = () => makeNode("menupopup");
        nodes.set(`${field}Select`, select);
    }
    const listeners = new Map();
    const window = {
        document,
        addEventListener: (name, callback) => listeners.set(name, callback),
        clearTimeout() {},
    };
    let resolveHealth;
    const axios = globalThis.__glossaryUiTest.axios;
    const originalGet = axios.get;
    axios.get = async () =>
        new Promise((resolve) => {
            resolveHealth = resolve;
        });
    let catalogRequests = 0;
    client.list = async () => {
        catalogRequests++;
        return { packs: [installed()] };
    };
    try {
        await ui.registerPrefsScripts(window);
        const statusBeforeClose = node("serverStatus").textContent;
        listeners.get("unload")();
        resolveHealth({ data: health });
        await new Promise((resolve) => globalThis.setImmediate(resolve));
        assert.equal(catalogRequests, 0);
        assert.equal(node("serverStatus").textContent, statusBeforeClose);
    } finally {
        axios.get = originalGet;
        globalThis.addon.data.prefs = { window: { document } };
    }
});
