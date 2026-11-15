import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const api = {};
new Function(
    "exports",
    ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/sourceSentence.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText,
)(api);
const match = api.sourceSentences;
test("partial and multi-sentence selections include complete original sentences, without crossing scientific abbreviations or decimals", () => {
    const a = "We use encoders.",
        b = "Smith et al. report 28.4 BLEU (see Fig. 2).",
        c = "Training took 3.5 days.";
    const src = [a, b, c].join(" "),
        dst =
            "我们使用编码器。Smith 等报告了 28.4 BLEU（见图 2）。训练耗时 3.5 天。";
    assert.equal(match(src, dst, "编码器"), a);
    assert.equal(match(src, dst, "见图 2"), b);
    assert.equal(match(src, dst, "2）。训练\n耗时"), b + " " + c);
    assert.equal(match(src, dst, "3.5 天"), c);
    assert.equal(match(src, dst, "无关"), undefined);
});
test("disambiguates a recurring term by its selected translated sentence or leaves it unresolved", () => {
    const src = "BERT starts here. Results for BERT follow.",
        dst = "BERT 出现在这里。接着是 BERT 的结果。";
    assert.equal(match(src, dst, "BERT"), undefined);
    assert.equal(
        match(src, dst, "BERT", (options) => options[1]),
        "Results for BERT follow.",
    );
    assert.equal(
        match(src, "相同。相同。", "相同", (options) => options[0]),
        undefined,
    );
    assert.equal(match(src, "重复重复。第二句。", "重复"), "BERT starts here.");
});
test("unbalanced and reordered translations do not silently select a neighboring sentence", () => {
    const src =
        "One result. Another result. Jakob tested RNN. Noam replicated this.";
    const dst = "两项结果。Jakob 测试了 RNN。Noam 重复了实验。";
    assert.equal(match(src, dst, "两项结果"), undefined);
    assert.equal(match(src, dst, "重复了实验"), "Noam replicated this.");
    assert.equal(
        match("Alpha wins. Beta loses.", "Beta 落败。Alpha 胜出。", "落败"),
        undefined,
    );
    assert.equal(
        match("Alpha wins. Beta loses.", "Alpha 胜出，而 Beta 落败。", "胜出"),
        undefined,
    );
    assert.equal(
        match(
            "The cohort shrank; mortality fell.",
            "队列缩小了。死亡率下降了。",
            "死亡率",
        ),
        "The cohort shrank; mortality fell.",
    );
});
