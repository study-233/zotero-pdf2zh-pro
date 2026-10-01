import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { Buffer } from "node:buffer";
import { gzipSync } from "node:zlib";
import { URL } from "node:url";
import ts from "typescript";

const load = (name, imports, globals) => {
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL(`../src/modules/${name}.ts`, import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    const exports = {};
    new Function("require", "exports", ...Object.keys(globals), code)(
        (n) => imports[n],
        exports,
        ...Object.values(globals),
    );
    return exports;
};
function fixture() {
    const prefs = new Map(),
        files = new Map();
    const raw = {
        unconditional: {
            readings: ["phonetic"],
            defs: [{ def_cn: "无条件的", pos_en: "adj." }],
        },
        gramme: { defs: [{ def_cn: "克" }], _pdf2zhSupplement: { kind: "ai" } },
    };
    const gzip = gzipSync(JSON.stringify(raw));
    const digest = (bytes) =>
        crypto.createHash("sha256").update(bytes).digest("hex");
    const state = {
        gzip,
        catalogFailure: false,
        digestFailure: false,
        writeFailure: false,
        resets: 0,
        downloads: 0,
        gate: undefined,
    };
    const pack = {
        id: "collins-en-zh",
        version: "2026.10.01",
        url: "https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/glossary-data/dictionaries/pack.json.gz",
        sha256: digest(gzip),
        sizeBytes: gzip.length,
        entryCount: 2,
        aiEntries: 1,
    };
    const io = {
        exists: async (p) => files.has(p),
        readUTF8: async (p) => files.get(p),
        makeDirectory: async () => {},
        write: async (p, b) => files.set(p, b),
        computeHexDigest: async (p) =>
            state.digestFailure ? "bad" : digest(files.get(p)),
        writeUTF8: async (p, b, options) => {
            assert.equal(options.tmpPath, p + ".tmp");
            if (state.writeFailure) throw Error("private path");
            files.set(p, b);
        },
        remove: async (p) => files.delete(p),
    };
    const zotero = {
        DataDirectory: { dir: "/data" },
        getMainWindow: () => globalThis,
        HTTP: {
            request: async (urlMethod, url, options) => {
                assert.equal(urlMethod, "GET");
                assert.equal(options.errorDelayMax, 0);
                let cancelled = false;
                options.cancellerReceiver(() => {
                    cancelled = true;
                });
                const xhr = {
                    abort() {
                        cancelled = true;
                    },
                };
                options.requestObserver(xhr);
                const isPack = url.endsWith(".gz");
                if (!isPack && state.catalogFailure)
                    throw Error("network private error");
                if (isPack) {
                    state.downloads++;
                    if (state.gate) await state.gate.promise;
                }
                if (cancelled) throw Error("cancelled");
                const bytes = isPack
                    ? state.gzip
                    : Buffer.from(
                          JSON.stringify({ schemaVersion: 1, packs: [pack] }),
                      );
                xhr.onprogress({ loaded: bytes.length });
                if (cancelled) throw Error("oversize");
                return { response: Uint8Array.from(bytes).buffer };
            },
        },
    };
    const globals = {
        Zotero: zotero,
        IOUtils: io,
        PathUtils: { join: path.posix.join, parent: path.posix.dirname },
    };
    const imports = {
        "../../package.json": { config: { addonRef: "test" } },
        "../utils/prefs": { setPref: (k, v) => prefs.set(k, v) },
    };
    const store = load("selectionDictionaryStore", imports, globals);
    const api = load(
        "selectionDictionaryDownload",
        {
            ...imports,
            "./selectionDictionaryStore": store,
            "./selectionTranslate": {
                resetSelectionTranslation() {
                    state.resets++;
                },
            },
            "./diagnostics": { recordDiagnostic() {} },
        },
        globals,
    );
    return { ...api, store, state, pack, files, prefs, digest };
}
const deferred = () => {
    let resolve;
    const promise = new Promise((r) => (resolve = r));
    return { promise, resolve };
};
const flush = async () => {
    for (let i = 0; i < 12; i++) await Promise.resolve();
};

test("download verifies compressed bytes, installs atomically and serves original/AI entries offline", async () => {
    const f = fixture();
    await f.refreshDictionaryDownload();
    assert.equal(f.state.downloads, 0);
    await f.downloadDictionary();
    assert.equal(f.dictionaryDownloadState().phase, "idle");
    assert.equal(f.dictionaryDownloadState().installed.version, "2026.10.01");
    assert.equal(f.prefs.get("selectionDictionary"), "collins");
    assert.equal(f.state.resets, 1);
    assert.equal(
        (await f.store.lookupImportedDictionary("Unconditional")).senses[0]
            .chinese,
        "无条件的",
    );
    assert.equal(
        (await f.store.lookupImportedDictionary("gramme")).aiGenerated,
        true,
    );
    assert.equal(
        [...f.files.keys()].filter((p) => p.endsWith(".part")).length,
        0,
    );
});
test("shared download deduplicates, survives UI unsubscribe/reopen and respects a newer dictionary choice", async () => {
    const f = fixture();
    await f.refreshDictionaryDownload();
    f.state.gate = deferred();
    let events = 0;
    const off = f.subscribeDictionaryDownload(() => events++);
    const pending = f.downloadDictionary();
    await flush();
    off();
    await f.downloadDictionary();
    assert.equal(f.state.downloads, 1);
    f.dictionaryChoiceChanged();
    f.prefs.set("selectionDictionary", "ecdict");
    let reopened = 0;
    f.subscribeDictionaryDownload(() => reopened++);
    f.state.gate.resolve();
    await pending;
    assert.ok(events && reopened);
    assert.equal(f.prefs.get("selectionDictionary"), "ecdict");
    assert.equal(f.dictionaryDownloadState().installed.entries, 2);
});
test("cancelled download retains old data and can retry successfully", async () => {
    const f = fixture();
    await f.downloadDictionary();
    const old = [...f.files.values()][0];
    f.state.gate = deferred();
    const pending = f.downloadDictionary();
    await flush();
    f.cancelDictionaryDownload();
    f.state.gate.resolve();
    await pending;
    assert.equal(f.dictionaryDownloadState().phase, "idle");
    assert.ok([...f.files.values()].includes(old));
    f.state.gate = undefined;
    await f.downloadDictionary();
    assert.equal(f.state.resets, 2);
});
test("digest, truncated payload, bad gzip, invalid JSON, mismatched counts and disk failures preserve installed dictionary", async () => {
    for (const change of [
        (f) => {
            f.state.digestFailure = true;
        },
        (f) => {
            f.pack.sizeBytes++;
        },
        (f) => {
            f.state.gzip = Buffer.from("not gzip");
            f.pack.sizeBytes = f.state.gzip.length;
            f.pack.sha256 = f.digest(f.state.gzip);
        },
        (f) => {
            f.state.gzip = gzipSync("bad json");
            f.pack.sizeBytes = f.state.gzip.length;
            f.pack.sha256 = f.digest(f.state.gzip);
        },
        (f) => {
            f.pack.entryCount = 3;
        },
        (f) => {
            f.state.writeFailure = true;
        },
    ]) {
        const f = fixture();
        await f.downloadDictionary();
        const old = f.files.get("/data/test/dictionaries/odh-collins.json");
        change(f);
        await f.refreshDictionaryDownload();
        await f.downloadDictionary();
        assert.equal(f.dictionaryDownloadState().phase, "failed");
        assert.equal(
            f.files.get("/data/test/dictionaries/odh-collins.json"),
            old,
        );
        assert.equal(f.state.resets, 1);
        assert.equal(
            [...f.files.keys()].some((p) => p.endsWith(".part")),
            false,
        );
    }
});
test("catalog failure remains retryable, rejects foreign hosts and invalid metadata without downloading", async () => {
    const f = fixture();
    f.state.catalogFailure = true;
    await f.refreshDictionaryDownload();
    assert.equal(f.dictionaryDownloadState().error, "catalog");
    f.state.catalogFailure = false;
    await f.refreshDictionaryDownload();
    assert.equal(f.dictionaryDownloadState().phase, "idle");
    for (const change of [
        { url: "https://example.com/dict.json.gz" },
        { sizeBytes: 100000000 },
        { sha256: "bad" },
        { entryCount: 0 },
        { aiEntries: 5 },
        { version: "latest" },
    ])
        assert.throws(() =>
            f.validateDictionaryCatalog({
                schemaVersion: 1,
                packs: [{ ...f.pack, ...change }],
            }),
        );
    assert.equal(f.state.downloads, 0);
});
