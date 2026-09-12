import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { URL } from "node:url";

const prefs = new Map();
globalThis.__glossaryTests = {
    getPref: (key) => prefs.get(key),
    setPref: (key, value) => prefs.set(key, value),
};
const compiled = ts
    .transpileModule(
        fs.readFileSync(
            new URL("../src/modules/glossaryStore.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.ESNext,
                target: ts.ScriptTarget.ES2022,
            },
        },
    )
    .outputText.replace(/^import .*;$/gm, "");
const store = await import(
    `data:text/javascript;base64,${Buffer.from(
        "const {getPref,setPref}=globalThis.__glossaryTests;\n" + compiled,
    ).toString("base64")}`
);

test("UTF-8 BOM, quotes, CRLF, and quoted newlines preserve complete fields", () => {
    const csv =
        '\uFEFFtarget,source,tgt_lng\r\n"带逗号，和引号""的词","camera, ""world""",zh-CN\r\n"多行\r\n译法",multiline,\r\n';
    assert.deepEqual(store.parseGlossaryCsv(csv), [
        {
            source: 'camera, "world"',
            target: '带逗号，和引号"的词',
            tgt_lng: "zh-CN",
        },
        { source: "multiline", target: "多行\n译法", tgt_lng: "" },
    ]);
});

test("language may be missing or empty, and language-specific terms coexist with defaults", () => {
    assert.deepEqual(
        store.parseGlossaryCsv("source,target\n camera , 相机 \n"),
        [{ source: "camera", target: "相机", tgt_lng: "" }],
    );
    assert.equal(
        store.parseGlossaryCsv(
            "source,target,tgt_lng\ncamera,相机,\ncamera,摄影机,zh-CN",
        ).length,
        2,
    );
});

test("equivalent duplicates are collapsed but conflicting translations report both physical lines", () => {
    assert.equal(
        store.parseGlossaryCsv(
            "source,target,tgt_lng\nCamera,相机,zh-CN\ncamera,相机,zh_cn",
        ).length,
        1,
    );
    assert.throws(
        () =>
            store.parseGlossaryCsv(
                'source,target,tgt_lng\n"camera\n pose",相机位姿,zh-CN\ncamera pose,摄影机姿态,zh_cn',
            ),
        /第 4 行.*第 2 行.*冲突/,
    );
});

test("syntax and schema errors include their physical CSV line", () => {
    for (const [csv, error] of [
        ["source,target,source\na,b,c", /第 1 行.*表头/],
        ["source,target,extra\na,b,c", /第 1 行.*表头/],
        ["source,target\nterm", /第 2 行.*2 列/],
        ["source,target\nterm,", /第 2 行.*不能为空/],
        ['source,target\n"term,translation', /第 2 行.*未闭合/],
        ['source,target\n"term"broken,translation', /第 2 行.*结束引号/],
        ['source,target\nter"m,translation', /第 2 行.*引号必须/],
    ])
        assert.throws(() => store.parseGlossaryCsv(csv), error);
});

test("failed replacement leaves the saved glossary intact; valid import replaces and clear persists", () => {
    prefs.clear();
    store.importGlossaryCsv("source,target\nold,旧词");
    const previous = prefs.get("glossaryEntries");
    for (const bad of [
        "source,target\nnew,新词\ninvalid",
        "source,target\na,甲\na,乙",
    ]) {
        assert.throws(() => store.importGlossaryCsv(bad));
        assert.equal(prefs.get("glossaryEntries"), previous);
    }
    store.importGlossaryCsv("source,target,tgt_lng\nnew,新词,zh-CN");
    assert.deepEqual(store.loadGlossaryEntries(), [
        { source: "new", target: "新词", tgt_lng: "zh-CN" },
    ]);
    store.clearGlossaryEntries();
    assert.deepEqual(store.loadGlossaryEntries(), []);
    assert.equal(prefs.get("glossaryEntries"), "[]");
});

test("malformed saved data is reported without silently discarding the glossary", () => {
    for (const value of [
        "not JSON",
        "{}",
        '[{"source":7,"target":"bad"}]',
        '[{"source":"a","target":"甲"},{"source":"a","target":"乙"}]',
    ]) {
        prefs.set("glossaryEntries", value);
        assert.throws(
            () => store.loadGlossaryEntries(),
            /无法读取.*原数据已保留/,
        );
        assert.equal(prefs.get("glossaryEntries"), value);
    }
});
