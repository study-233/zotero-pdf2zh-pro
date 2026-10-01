import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const code = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/selectionFormatting.ts", import.meta.url),
        "utf8",
    ),
    {
        compilerOptions: { module: ts.ModuleKind.CommonJS },
    },
).outputText;
const format = {};
new Function("exports", code)(format);

test("known POS preserves grammar and unknown labels are not guessed", () => {
    for (const [input, expected] of [
        ["N-COUNT", "n. [C]"],
        ["N-UNCOUNT", "n. [U]"],
        ["VERB", "v."],
        ["形容词", "adj."],
        ["V-T", "vt."],
        ["vi.", "vi."],
        ["N-PROPER", "N-PROPER"],
        [undefined, ""],
    ])
        assert.equal(format.formatPartOfSpeech(input), expected);
});

test("ECDICT prefixes are formatted without losing unrecognized text or long definitions", () => {
    const long = "原始完整释义".repeat(20);
    const senses = format.dictionarySenses({
        text: `n. 功能; 作用\nvt. 发挥功能\n[数学] ${long}`,
    });
    assert.deepEqual(
        senses.map(({ pos, chinese }) => [pos, chinese]),
        [
            ["n.", "功能；作用"],
            ["vt.", "发挥功能"],
            ["", `[数学] ${long}`],
        ],
    );
});

test("only identical AI senses are deduplicated; offline entries and different examples survive", () => {
    const sense = {
        pos: "副词",
        chinese: "逐点地",
        english: "at each point",
        examples: [],
    };
    const value = {
        text: "",
        senses: [
            sense,
            sense,
            {
                ...sense,
                examples: [{ english: "point-wise", chinese: "逐点地" }],
            },
        ],
    };
    assert.equal(format.dictionarySenses(value).length, 3);
    assert.equal(
        format.dictionarySenses({ ...value, aiGenerated: true }).length,
        2,
    );
});

test("copy includes every sense, English definition, example and usage, with no invented phonetic", () => {
    const senses = Array.from({ length: 4 }, (_, i) => ({
        pos: "n.",
        chinese: `含义${i}`,
        english: `sense ${i}`,
        examples: [{ english: `example ${i}`, chinese: `例句${i}` }],
    }));
    const copy = format.dictionaryCopyText(
        "function",
        "/fʌŋkʃn/",
        senses,
        "用法提示",
    );
    assert.match(copy, /^function\n\/fʌŋkʃn\/\n/);
    assert.match(
        copy,
        /4\. n\. 含义3\nsense 3\nexample 3\n例句3\n用法说明：用法提示$/,
    );
    assert.ok(
        !format
            .dictionaryCopyText("point-wise", undefined, senses)
            .includes("undefined"),
    );
});

test("context validation normalizes POS, accepts sentences without POS and rejects malformed fields", () => {
    const value = format.checkedContextMeaning({
        pos: "N-COUNT",
        meaning: " 河岸 ",
        explanation: "这里描述河流的岸边。",
    });
    assert.equal(
        format.contextCopyText(value),
        "n. [C] 河岸\n这里描述河流的岸边。",
    );
    assert.deepEqual(
        format.checkedContextMeaning({
            meaning: "完整句子",
            explanation: "一句说明。",
        }),
        { meaning: "完整句子", explanation: "一句说明。" },
    );
    for (const invalid of [
        null,
        [],
        {},
        { meaning: "", explanation: "说明" },
        { meaning: "译法", explanation: 3 },
        { meaning: "译法", explanation: "说明", pos: false },
    ])
        assert.throws(() => format.checkedContextMeaning(invalid), /语境格式/);
});
