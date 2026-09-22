import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { URL } from "node:url";
import { createContext, runInContext } from "node:vm";

const prefs = new Map();
globalThis.__glossaryPacksTest = {
    getPref: (key) => prefs.get(key),
    setPref: (key, value) => prefs.set(key, value),
};
const compiled = ts
    .transpileModule(
        fs.readFileSync(
            new URL("../src/modules/glossaryPacks.ts", import.meta.url),
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
const packs = await import(
    `data:text/javascript;base64,${Buffer.from(
        "const {getPref,setPref}=globalThis.__glossaryPacksTest;\n" + compiled,
    ).toString("base64")}`
);
const server = "http://localhost:8890";
const version = (id, v = "1") => ({ id, version: v, sha256: v.repeat(64) });
const pack = (id, v = "1") => ({
    ...version(id, v),
    status: "installed",
    installedVersions: [version(id, v)],
});
const response = (values) => ({
    ok: true,
    json: async () => ({ packs: values }),
});

test("selection defaults off, normalizes server URLs, survives reload and isolates servers", () => {
    prefs.clear();
    assert.deepEqual(packs.getSelectedGlossaryPackIds(server), []);
    packs.setSelectedGlossaryPackIds(" HTTP://LOCALHOST:8890/ ", [
        "physics",
        "medicine",
        "physics",
    ]);
    assert.deepEqual(packs.getSelectedGlossaryPackIds(server), [
        "medicine",
        "physics",
    ]);
    assert.deepEqual(
        packs.getSelectedGlossaryPackIds("http://localhost:8891"),
        [],
    );
    packs.setSelectedGlossaryPackIds("http://localhost:8891", ["building"]);
    packs.setSelectedGlossaryPackIds(server, []);
    assert.deepEqual(
        packs.getSelectedGlossaryPackIds("http://localhost:8891/"),
        ["building"],
    );
    assert.equal(
        packs.normalizeGlossaryServerUrl("HTTPS://HOST:443/base/"),
        "https://host/base",
    );
    assert.throws(() => packs.normalizeGlossaryServerUrl("file:///tmp"));
    assert.throws(() =>
        packs.normalizeGlossaryServerUrl("http://host?server=1"),
    );
});

test("malformed stored selections remain intact and fail visibly", () => {
    prefs.set("glossaryPackSelections", "broken");
    assert.throws(
        () => packs.setSelectedGlossaryPackIds(server, []),
        /原设置已保留/,
    );
    assert.equal(prefs.get("glossaryPackSelections"), "broken");
});

test("language filtering retains selection and never contacts the server", async () => {
    prefs.clear();
    packs.setSelectedGlossaryPackIds(server, ["medicine"]);
    globalThis.fetch = () => {
        throw new Error("must not fetch");
    };
    for (const [source, target] of [
        ["zh", "en"],
        ["en", "ja"],
        ["en", "zh-TW"],
        ["auto", "zh-CN"],
    ]) {
        assert.deepEqual(
            await packs.resolveSelectedGlossaryPacks(server, source, target),
            [],
        );
    }
    for (const target of ["zh", "zh-CN", "zh_Hans"]) {
        assert.equal(packs.supportsGlossaryPackLanguage("en-US", target), true);
    }
    assert.deepEqual(packs.getSelectedGlossaryPackIds(server), ["medicine"]);
    packs.setSelectedGlossaryPackIds(server, []);
    assert.deepEqual(
        await packs.resolveSelectedGlossaryPacks(server, "en", "zh-CN"),
        [],
    );
});

test("batch references remain pinned across updates and preference edits", async () => {
    prefs.clear();
    packs.setSelectedGlossaryPackIds(server, ["medicine", "physics"]);
    const catalog = [pack("medicine"), pack("physics")];
    globalThis.fetch = async () => response(catalog);
    const captured = await packs.resolveSelectedGlossaryPacks(
        server,
        "en",
        "zh-CN",
    );
    catalog[0].installedVersions[0].version = "changed";
    catalog[1] = pack("physics", "2");
    packs.setSelectedGlossaryPackIds(server, ["physics"]);
    assert.deepEqual(captured, [version("medicine"), version("physics")]);
    assert.deepEqual(
        await packs.resolveSelectedGlossaryPacks(server, "en", "zh-CN"),
        [version("physics", "2")],
    );
});

test("update availability preserves use of the latest installed older pack", async () => {
    prefs.clear();
    packs.setSelectedGlossaryPackIds(server, ["environment"]);
    const catalog = [
        {
            ...pack("environment", "2"),
            status: "update_available",
            installedVersions: [version("environment")],
        },
    ];
    globalThis.fetch = async () => response(catalog);
    assert.deepEqual(
        await packs.resolveSelectedGlossaryPacks(server, "en", "zh-CN"),
        [version("environment")],
    );
    catalog[0].installedVersions = [];
    await assert.rejects(
        packs.resolveSelectedGlossaryPacks(server, "en", "zh-CN"),
        /重新下载或取消勾选/,
    );
});

test("old servers reject selected packs without silently omitting them", async () => {
    prefs.clear();
    packs.setSelectedGlossaryPackIds(server, ["medicine"]);
    globalThis.fetch = async () => ({ status: 404, ok: false });
    await assert.rejects(
        packs.resolveSelectedGlossaryPacks(server, "en", "zh-CN"),
        /升级服务端/,
    );
});

test("management calls use explicit actions and never alter selections", async () => {
    prefs.clear();
    const calls = [];
    globalThis.fetch = async (url, options) => {
        calls.push({ url, ...options });
        return response([]);
    };
    await packs.listGlossaryPacks(server);
    await packs.checkGlossaryUpdates(server);
    await packs.downloadGlossaryPack(server, "medicine", "1");
    await packs.cancelGlossaryDownload(server, "medicine");
    await packs.removeGlossaryPack(server, "medicine");
    assert.deepEqual(
        calls.map((c) => [c.url, c.method]),
        [
            [`${server}/glossaries`, "GET"],
            [`${server}/glossaries/check-updates`, "POST"],
            [`${server}/glossaries/medicine/download`, "POST"],
            [`${server}/glossaries/medicine/cancel`, "POST"],
            [`${server}/glossaries/medicine`, "DELETE"],
        ],
    );
    assert.deepEqual(JSON.parse(calls[2].body), { version: "1" });
    assert.deepEqual(packs.getSelectedGlossaryPackIds(server), []);
});

test("plugin startup supplies abort support in a Zotero sandbox without browser globals", async () => {
    const mainWindow = { AbortController: globalThis.AbortController };
    const timers = new Map();
    const signals = [];
    const sandbox = createContext({
        URL,
        Zotero: {},
        config: { addonInstance: "pdf2zhpro" },
        Addon: class {},
        getPref: () => "{}",
        setPref: () => {},
        setTimeout: (callback, delay) => {
            assert.equal(delay, 30000);
            const id = {};
            timers.set(id, callback);
            return id;
        },
        clearTimeout: (id) => timers.delete(id),
        fetch: async (_url, { signal }) => {
            signals.push(signal);
            return response([]);
        },
    });
    // Match the toolkit's global-first lookup, including getter evaluation.
    sandbox.BasicTool = class {
        getGlobal(name) {
            if (typeof sandbox[name] !== "undefined") return sandbox[name];
            return name === "window" ? mainWindow : mainWindow[name];
        }
    };
    runInContext("globalThis._globalThis = globalThis", sandbox);
    assert.equal(runInContext("typeof AbortController", sandbox), "undefined");
    const entry = ts
        .transpileModule(
            fs.readFileSync(
                new URL("../src/index.ts", import.meta.url),
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
    runInContext(entry + "\n" + compiled.replace(/^export /gm, ""), sandbox);

    await sandbox.listGlossaryPacks(server);
    await sandbox.downloadGlossaryPack(server, "medicine", "1");
    assert.equal(signals.length, 2);
    assert.ok(
        signals.every((signal) => signal instanceof globalThis.AbortSignal),
    );
    assert.equal(timers.size, 0);

    sandbox.fetch = (_url, { signal }) =>
        new Promise((_resolve, reject) => {
            signal.addEventListener("abort", () => reject(signal.reason));
        });
    const pending = sandbox.listGlossaryPacks(server);
    assert.equal(timers.size, 1);
    timers.values().next().value();
    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(timers.size, 0);
});
