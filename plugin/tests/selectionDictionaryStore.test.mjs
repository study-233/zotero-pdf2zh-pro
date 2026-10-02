import process from "node:process";
import assert from "node:assert/strict";
import fs from "node:fs";
import crypto from "node:crypto";
import path from "node:path";
import { URL } from "node:url";
import test from "node:test";
import ts from "typescript";

const raw = {
    Unconditional: {
        readings: ["phonetic"],
        defs: [
            {
                pos_en: "adj.",
                def_cn: "无条件的",
                def_en: "without conditions",
                ext: [{ ext_cn: "支持。", ext_en: "Support." }],
            },
        ],
    },
    empty: { readings: [], defs: [] },
};
function fixture() {
    const files = new Map(),
        prefs = new Map(),
        state = { reads: 0, failWrite: false, writes: [] };
    files.set("input", JSON.stringify(raw));
    const exports = {};
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL(
                "../src/modules/selectionDictionaryStore.ts",
                import.meta.url,
            ),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    const io = {
        exists: async (p) => files.has(p),
        readUTF8: async (p) => {
            state.reads++;
            if (!files.has(p)) throw new Error("missing");
            return files.get(p);
        },
        computeHexDigest: async (p) =>
            crypto.createHash("sha256").update(files.get(p)).digest("hex"),
        makeDirectory: async () => {},
        writeUTF8: async (p, text, options) => {
            state.writes.push({ p, options });
            if (state.failWrite) throw new Error("disk full");
            files.set(p, text);
        },
    };
    new Function("require", "exports", "Zotero", "IOUtils", "PathUtils", code)(
        (name) =>
            ({
                "../../package.json": { config: { addonRef: "pdf2zhpro" } },
                "../utils/prefs": { setPref: (k, v) => prefs.set(k, v) },
            })[name],
        exports,
        { DataDirectory: { dir: "/data" } },
        io,
        { join: path.join, parent: path.dirname },
    );
    return { ...exports, files, prefs, state };
}

test("import converts ODH senses, skips entries without Chinese definitions, hashes source and writes atomically", async () => {
    const f = fixture();
    assert.equal(f.state.reads, 0);
    const info = await f.importODHDictionary("input");
    assert.equal(info.entries, 1);
    assert.equal(info.skipped, 1);
    assert.match(info.sha256, /^[a-f0-9]{64}$/);
    assert.equal(f.prefs.get("selectionDictionary"), "collins");
    assert.equal(
        f.state.writes[0].options.tmpPath,
        f.state.writes[0].p + ".tmp",
    );
    const [a, b] = await Promise.all([
        f.lookupImportedDictionary("Unconditional"),
        f.lookupImportedDictionary("Unconditional"),
    ]);
    assert.deepEqual(a, b);
    assert.equal(a.senses[0].chinese, "无条件的");
    assert.equal(a.senses[0].examples[0].chinese, "支持。");
    const reads = f.state.reads;
    await f.lookupImportedDictionary("Unconditional");
    assert.equal(f.state.reads, reads);
    assert.equal(await f.lookupImportedDictionary("__proto__"), undefined);
});

test("malformed source or failed replacement preserves previous dictionary and selection", async () => {
    const f = fixture();
    await f.importODHDictionary("input");
    const old = await f.lookupImportedDictionary("Unconditional");
    for (const value of [
        "bad JSON",
        "[]",
        "{}",
        JSON.stringify({ word: { defs: [{ def_cn: 4 }] } }),
        JSON.stringify({ word: { defs: [] } }),
        JSON.stringify({ word: { defs: [{ def_cn: "word" }] } }),
    ]) {
        f.files.set("input", value);
        await assert.rejects(f.importODHDictionary("input"));
        assert.deepEqual(
            await f.lookupImportedDictionary("Unconditional"),
            old,
        );
    }
    f.files.set(
        "input",
        JSON.stringify({ word: { defs: [{ def_cn: "词" }] } }),
    );
    f.state.failWrite = true;
    await assert.rejects(f.importODHDictionary("input"));
    assert.deepEqual(await f.lookupImportedDictionary("Unconditional"), old);
    f.state.failWrite = false;
    await f.importODHDictionary("input");
    assert.equal(await f.lookupImportedDictionary("Unconditional"), undefined);
    assert.equal(
        (await f.lookupImportedDictionary("WORD")).senses[0].chinese,
        "词",
    );
});

test("imported AI entries keep provenance and invalid stored files may retry after repair", async () => {
    const f = fixture();
    f.files.set(
        "input",
        JSON.stringify({
            word: {
                defs: [{ def_cn: "词" }],
                _pdf2zhSupplement: { kind: "ai", model: "gpt-6-luna" },
            },
        }),
    );
    const info = await f.importODHDictionary("input");
    assert.equal(info.aiEntries, 1);
    assert.equal((await f.lookupImportedDictionary("word")).aiGenerated, true);
    const g = fixture();
    const file = path.join(
        "/data",
        "pdf2zhpro",
        "dictionaries",
        "odh-collins.json",
    );
    g.files.set(file, "{}");
    await assert.rejects(g.lookupImportedDictionary("word"), /重新导入/);
    g.files.set(file, f.files.get(file));
    assert.equal(
        (await g.lookupImportedDictionary("word")).senses[0].chinese,
        "词",
    );
});

test(
    "actual locally provided ODH corpus converts without retaining its data in test fixtures",
    { skip: !process.env.ODH_DICTIONARY_PATH },
    () => {
        const f = fixture();
        const data = JSON.parse(
            fs.readFileSync(process.env.ODH_DICTIONARY_PATH, "utf8"),
        );
        const result = f.convertODHDictionary(data);
        const ai = Object.values(data).filter(
            (e) => e._pdf2zhSupplement?.kind === "ai",
        ).length;
        assert.equal(
            Object.values(result.entries).filter((e) => e.aiGenerated).length,
            ai,
        );
        assert.equal(
            Object.keys(result.entries).length + result.skipped,
            Object.keys(data).length,
        );
        assert.ok(
            result.entries.unconditional.senses.some((s) =>
                s.chinese.includes("无条件"),
            ),
        );
    },
);
