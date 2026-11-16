import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

function fixture() {
    const mod = {};
    const input = fs.readFileSync(
        new URL("../src/modules/sourceLookup.ts", import.meta.url),
        "utf8",
    );
    const { outputText } = ts.transpileModule(input, {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    });
    const visits = [];
    const item = { id: 123, getFilePathAsync: async () => "same-name.pdf" };
    const zotero = {
        Items: {
            getByLibraryAndKeyAsync: async (library, key) =>
                library === 3 && key === "ORIGINAL" && item,
        },
        Reader: { open: async (...args) => visits.push(args) },
    };
    const sourceHash = "a".repeat(64);
    new Function("require", "exports", "Zotero", "IOUtils", outputText)(
        () => ({ getString: (k) => k }),
        mod,
        zotero,
        { exists: async () => true, computeHexDigest: async () => sourceHash },
    );
    const map = {
        version: 1,
        sourceHash,
        outputHash: "b".repeat(64),
        targetLang: "zh-CN",
        pages: [-3, 3, -7, 7],
        paragraphs: {
            3: [
                ["The mean fell.", "均值下降。"],
                ["The median fell.", "中位数下降。"],
            ],
            7: [["Noise fell.", "噪声下降。"]],
        },
    };
    return { ...mod, map, visits };
}

test("reverse matches stay on the mapped physical page, preserve ambiguity, and survive later translations", () => {
    const { matchSourceParagraphs: match, map } = fixture();
    assert.deepEqual(match(map, 2, "下\n降。"), {
        page: 3,
        candidates: ["The mean fell.", "The median fell."],
        uncertain: true,
    });
    assert.deepEqual(match(map, 4, "噪 声下降。"), {
        page: 7,
        candidates: ["Noise fell."],
        uncertain: false,
    });
    assert.deepEqual(match(map, 1, "下降").candidates, []);
    assert.deepEqual(match(map, 2, "噪声下降").candidates, []);
    const another = {
        ...map,
        paragraphs: { 3: [["New material.", "新版本。"]] },
    };
    assert.deepEqual(match(another, 2, "均值下降").candidates, []);
    assert.deepEqual(match(map, 2, "均值下降").candidates, ["The mean fell."]);
});

test("map integrity and exact source identity guard opening the wrong PDF", async () => {
    const { map, checkedSourceMap, openSourcePage, visits } = fixture();
    assert.equal(checkedSourceMap(map, map.outputHash), map);
    assert.throws(() => checkedSourceMap(map, "wrong hash"));
    assert.throws(() =>
        checkedSourceMap(
            { ...map, paragraphs: { 1: [["bad row"]] } },
            map.outputHash,
        ),
    );
    const binding = { map, sourceKey: "ORIGINAL" };
    await assert.rejects(openSourcePage(4, binding, 3), /missing/);
    await assert.rejects(
        openSourcePage(
            3,
            { ...binding, map: { ...map, sourceHash: "different" } },
            3,
        ),
        /changed/,
    );
    assert.deepEqual(visits, []);
    await openSourcePage(3, binding, 3);
    assert.deepEqual(visits, [[123, { pageIndex: 2 }]]);
});

test("keeps dictionary and forward translation on the original half regardless of output order", () => {
    const { isOriginalSide, map } = fixture();
    const position = (l, r) => ({ pageIndex: 1, rects: [[l, 30, r, 50]] });
    assert.equal(isOriginalSide(map, { pageIndex: 0 }), true);
    assert.equal(isOriginalSide(map, position(50, 100)), false);
    for (const boundary of [550, -550]) {
        const combined = {
            ...map,
            split: [boundary, boundary, boundary, boundary],
        };
        assert.equal(isOriginalSide(combined, position(50, 100)), boundary > 0);
        assert.equal(
            isOriginalSide(combined, position(650, 800)),
            boundary < 0,
        );
        assert.equal(isOriginalSide(combined, position(545, 555)), false);
    }
});

test("page geometry separates repeated phrases by the selected lines and refuses an ambiguous source position", () => {
    const api = {};
    const input = fs.readFileSync(
        new URL("../src/modules/sourcePosition.ts", import.meta.url),
        "utf8",
    );
    new Function(
        "exports",
        ts.transpileModule(input, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText,
    )(api);
    const line = (str, x, y) =>
        Array.from(str, (u, i) => ({
            u,
            rect: [x + i * 5, y, x + (i + 1) * 5, y + 7],
            lineBreakAfter: i === str.length - 1,
        }));
    const chars = [
        ...line("The co-", 5, 90),
        ...line("hort.", 5, 78),
        ...line("The control.", 5, 60),
        ...line("下降：第一组", 150, 90),
        ...line("下降：第二组", 150, 65),
    ];
    for (const edge of [100, -100]) {
        const left = edge > 0 ? 5 : 150;
        const right = edge > 0 ? 150 : 5;
        const positioned =
            edge > 0
                ? chars
                : chars.map((c) => ({
                      ...c,
                      rect: c.rect.map((v, i) =>
                          i % 2 ? v : v + (v < 100 ? 145 : -145),
                      ),
                  }));
        assert.deepEqual(
            api.paragraphPosition(positioned, 0, "The cohort.", edge),
            {
                pageIndex: 0,
                rects: [
                    [left, 90, left + 35, 97],
                    [left, 78, left + 25, 85],
                ],
            },
        );
        assert.deepEqual(
            api.selectedParagraphs(
                positioned,
                { pageIndex: 0, rects: [[right, 65, right + 10, 72]] },
                [
                    ["The cohort.", "下降：第一组"],
                    ["The control.", "下降：第二组"],
                ],
                edge,
            ),
            [["The control.", "下降：第二组"]],
        );
        assert.equal(
            api.paragraphPosition(positioned, 0, "The", edge),
            undefined,
        );
        assert.deepEqual(
            api.selectedParagraphs(
                positioned,
                {
                    pageIndex: 0,
                    rects: [
                        [right, 90, right + 10, 97],
                        [right, 65, right + 10, 72],
                    ],
                },
                [
                    ["The cohort.", "下降：第一组"],
                    ["The control.", "下降：第二组"],
                ],
                edge,
            ),
            [],
        );
    }
});
