import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const source = fs.readFileSync(
    new URL("../src/modules/selectionDictionary.ts", import.meta.url),
    "utf8",
);
const bundled = fs.readFileSync(
    new URL("../addon/content/dictionaries/ecdict.json", import.meta.url),
    "utf8",
);
function fixture(legacy = false) {
    const state = {
        reads: 0,
        fail: false,
        raw: bundled,
        selected: "ecdict",
        imported: undefined,
        importFail: false,
    };
    const exports = {};
    const code = ts.transpileModule(source, {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    }).outputText;
    const read = async (url) => {
        state.reads++;
        assert.equal(
            url,
            "chrome://pdf2zhpro/content/dictionaries/ecdict.json",
        );
        if (state.fail) throw new Error("read failed");
        return state.raw;
    };
    const File = {
        getContentsAsync: async () => {
            throw new Error(
                "Deprecated URL API must never be used: it returns an HTTP object",
            );
        },
        getContentsFromURLAsync: read,
    };
    if (!legacy) File.getResourceAsync = read;
    new Function("require", "exports", "Zotero", code)(
        (name) =>
            ({
                "../../package.json": { config: { addonRef: "pdf2zhpro" } },
                "../utils/prefs": { getPref: () => state.selected },
                "./selectionDictionaryStore": {
                    lookupImportedDictionary: async (word) => {
                        if (state.importFail) throw new Error();
                        return typeof state.imported === "function"
                            ? state.imported(word)
                            : state.imported;
                    },
                },
            })[name],
        exports,
        { File },
    );
    return { ...exports, state };
}

test("bundled ECDICT includes real definitions, attribution and provenance", () => {
    const data = JSON.parse(bundled);
    assert.equal(Object.keys(data).length, 50000);
    assert.match(data.unconditional[1], /无条件/);
    assert.match(data.architecture[1], /结构|建筑/);
    const license = fs.readFileSync(
        new URL(
            "../addon/content/dictionaries/LICENSE-ECDICT.txt",
            import.meta.url,
        ),
        "utf8",
    );
    assert.match(license, /MIT License/);
    const metadata = JSON.parse(
        fs.readFileSync(
            new URL(
                "../addon/content/dictionaries/source.json",
                import.meta.url,
            ),
        ),
    );
    assert.match(metadata.inputSHA256, /^[0-9a-f]{64}$/);
});

test("dictionary lazy-loads once, shares pending loads and normalizes selections", async () => {
    const f = fixture();
    assert.equal(f.state.reads, 0);
    const [a, b] = await Promise.all([
        f.lookupDictionary(" Uncon\u00additional ", "en", "zh-CN"),
        f.lookupDictionary("UNCONDITIONAL", "en-US", "zh-Hans"),
    ]);
    assert.deepEqual(a, b);
    assert.match(a.text, /无条件/);
    assert.ok(a.phonetic);
    assert.equal(f.state.reads, 1);
    assert.equal(
        (await f.lookupDictionary("constructor", "en", "zh-CN")) instanceof
            Function,
        false,
    );
    assert.equal(
        await f.lookupDictionary("__proto__", "en", "zh-CN"),
        undefined,
    );
});

test("wrong languages and long selections do not load the English-Chinese dictionary", async () => {
    const f = fixture();
    for (const [text, source, target] of [
        ["word", "fr", "zh-CN"],
        ["word", "en", "ja"],
        ["one two three four", "en", "zh-CN"],
        ["", "en", "zh-CN"],
    ]) {
        assert.equal(await f.lookupDictionary(text, source, target), undefined);
    }
    assert.equal(f.state.reads, 0);
});

test("failed resource read is retriable", async () => {
    const f = fixture();
    f.state.fail = true;
    await assert.rejects(
        f.lookupDictionary("word", "en", "zh-CN"),
        /离线词典读取失败/,
    );
    f.state.fail = false;
    assert.ok(await f.lookupDictionary("word", "en", "zh-CN"));
    assert.equal(f.state.reads, 2);
});

test("legacy text API works without getResourceAsync", async () => {
    const f = fixture(true);
    assert.match(
        (await f.lookupDictionary("unconditional", "en", "zh-CN")).text,
        /无条件/,
    );
    assert.equal(f.state.reads, 1);
});

test("HTTP objects, bad JSON, empty or malformed data never become cached dictionaries", async () => {
    for (const raw of [
        { responseText: bundled },
        "bad JSON",
        "[]",
        "{}",
        "null",
        '{"word":["p",""]}',
        '{"word":["p",7]}',
    ]) {
        const f = fixture();
        f.state.raw = raw;
        await assert.rejects(
            f.lookupDictionary("word", "en", "zh-CN"),
            /离线词典读取失败/,
        );
        f.state.raw = bundled;
        assert.ok(await f.lookupDictionary("word", "en", "zh-CN"));
        assert.equal(f.state.reads, 2);
    }
});

test("imported dictionary wins; AI provenance is distinct; missing or failed imported data falls back", async () => {
    const f = fixture();
    f.state.selected = "collins";
    f.state.imported = {
        headword: "Unconditional",
        phonetic: "p",
        senses: [{ pos: "adj.", chinese: "无条件的", examples: [] }],
    };
    let result = await f.lookupDictionary("Unconditional", "en", "zh-CN");
    assert.match(result.origin, /柯林斯/);
    assert.equal(f.state.reads, 0);
    f.state.imported.aiGenerated = true;
    result = await f.lookupDictionary("Unconditional", "en", "zh-CN");
    assert.match(result.origin, /AI 补充/);
    assert.doesNotMatch(result.origin, /柯林斯/);
    f.state.imported = undefined;
    result = await f.lookupDictionary("Unconditional", "en", "zh-CN");
    assert.equal(result.origin, "ECDICT 离线词典");
    f.state.importFail = true;
    result = await f.lookupDictionary("Unconditional", "en", "zh-CN");
    assert.match(result.notice, /读取失败/);
    await assert.rejects(
        f.lookupDictionary("nonsense12345", "en", "zh-CN"),
        /重新导入/,
    );
});

test("plural selections use offline headwords and keep exact meanings ahead of fallback", async () => {
    const f = fixture();
    for (const [word, headword] of [
        ["distributions", "distribution"],
        ["categories", "category"],
        ["boxes", "box"],
        ["children", "child"],
    ]) {
        const result = await f.lookupDictionary(word, "en", "zh-CN");
        assert.ok(result, word);
        // Some inflected forms are themselves dictionary entries and take precedence.
        assert.equal(
            result.headword,
            JSON.parse(bundled)[word] ? word : headword,
        );
    }
    const result = await f.lookupDictionary("distributions", "en", "zh-CN");
    assert.match(result.text, /分布/);
    assert.equal(result.headword, "distribution");
    assert.match(result.notice, /distributions → distribution/);
    assert.equal(f.state.reads, 1);
});

test("Collins plural fallback is local; exact ECDICT entries outrank inferred Collins words", async () => {
    const f = fixture();
    f.state.selected = "collins";
    f.state.imported = (word) =>
        ["distribution", "new"].includes(word)
            ? {
                  headword: word,
                  senses: [
                      {
                          chinese: word === "distribution" ? "分布" : "新的",
                          examples: [],
                      },
                  ],
              }
            : undefined;
    const result = await f.lookupDictionary("distributions", "en", "zh-CN");
    assert.equal(result.origin, "柯林斯离线词典");
    assert.equal(result.headword, "distribution");
    assert.equal(
        (await f.lookupDictionary("news", "en", "zh-CN")).headword,
        "news",
    );
    const g = fixture();
    g.state.selected = "collins";
    g.state.imported = f.state.imported;
    g.state.raw = "broken";
    assert.equal(
        (await g.lookupDictionary("distributions", "en", "zh-CN")).headword,
        "distribution",
    );
});

test("plural rules do not stem phrases, suffix lookalikes or absent base entries", async () => {
    const f = fixture();
    f.state.raw = JSON.stringify({
        basi: ["", "基"],
        statu: ["", "像"],
        clas: ["", "类"],
        distribution: ["", "分布"],
    });
    for (const word of [
        "basis",
        "status",
        "class",
        "unknownwords",
        "two distributions",
    ])
        assert.equal(
            await f.lookupDictionary(word, "en", "zh-CN"),
            undefined,
            word,
        );
});
