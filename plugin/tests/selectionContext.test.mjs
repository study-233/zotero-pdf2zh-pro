import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";
const code = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/selectionContext.ts", import.meta.url),
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
new Function("exports", code)(exports);
const { reconstructParagraphs, resolveSelectionContext, getSelectionContext } =
    exports;

test("context resolves word to sentence, sentence to paragraph and paragraph to neighbors", () => {
    const paragraphs = [
        "Previous paragraph.",
        "We use physical devices. This enables the experiment.",
        "Next paragraph.",
    ];
    assert.deepEqual(resolveSelectionContext("physical devices", paragraphs), {
        level: "word",
        context: "We use physical devices.",
    });
    assert.deepEqual(
        resolveSelectionContext("We use physical devices.", paragraphs),
        { level: "sentence", context: paragraphs[1] },
    );
    assert.deepEqual(resolveSelectionContext(paragraphs[1], paragraphs), {
        level: "paragraph",
        context: paragraphs.join("\n\n"),
    });
    assert.equal(
        resolveSelectionContext("not in page", paragraphs).context,
        "not in page",
    );
});

test("page reconstruction respects vertical paragraph and column breaks", () => {
    const item = (str, y) => ({
        str,
        transform: [1, 0, 0, 1, 20, y],
        height: 10,
    });
    assert.deepEqual(
        reconstructParagraphs([
            item("First line", 100),
            item("second line.", 88),
            item("New paragraph.", 55),
            item("New column.", 100),
        ]),
        ["First line second line.", "New paragraph.", "New column."],
    );
});

test("PDF.js extraction caches current pages and gracefully falls back without a bridge", async () => {
    const pages = [];
    const reader = {
        _internalReader: {
            _primaryView: {
                _iframeWindow: {
                    PDFViewerApplication: {
                        pdfDocument: {
                            getPage: async (page) => {
                                pages.push(page);
                                return {
                                    getTextContent: async () => ({
                                        items: [
                                            { str: "We use physical devices." },
                                        ],
                                    }),
                                };
                            },
                        },
                    },
                },
            },
        },
    };
    assert.equal(
        await getSelectionContext(reader, 5, "physical devices"),
        "We use physical devices.",
    );
    await getSelectionContext(reader, 5, "physical devices");
    assert.deepEqual(pages, [5]);
    assert.equal(await getSelectionContext({}, 5, "selected"), "selected");
});

function pdfFixture(pageTexts) {
    const calls = [];
    const failures = new Set();
    const pdf = {
        numPages: pageTexts.length,
        async getPage(number) {
            calls.push(number);
            if (failures.has(number)) throw new Error("unavailable page");
            assert.ok(number >= 1 && number <= pageTexts.length);
            return {
                getTextContent: async () => ({
                    items: pageTexts[number - 1].map((str, i) => ({
                        str,
                        transform: [1, 0, 0, 1, 0, 1000 - i * 50],
                        height: 10,
                    })),
                }),
            };
        },
    };
    return {
        reader: {
            _internalReader: {
                _primaryView: {
                    _iframeWindow: {
                        PDFViewerApplication: { pdfDocument: pdf },
                    },
                },
            },
        },
        calls,
        failures,
        pdf,
    };
}

test("page-edge context adds bounded neighboring snippets and shares extraction cache", async () => {
    const f = pdfFixture([
        ["Earlier.", "Previous page ending."],
        ["The selected sentence is here."],
        ["Next page opening.", "Later."],
    ]);
    const result = await getSelectionContext(
        f.reader,
        2,
        "The selected sentence is here.",
    );
    assert.match(result, /Previous page ending/);
    assert.match(result, /The selected sentence is here/);
    assert.match(result, /Next page opening/);
    await getSelectionContext(f.reader, 2, "selected sentence");
    assert.deepEqual(f.calls, [2, 1, 3]);
});

test("middle selections do not read adjacent pages and document edges never read out of bounds", async () => {
    const f = pdfFixture([
        ["First page."],
        ["Top.", "Above.", "The target sentence is here.", "Below.", "Bottom."],
        ["Last page."],
    ]);
    assert.equal(
        await getSelectionContext(f.reader, 2, "target sentence"),
        "The target sentence is here.",
    );
    assert.deepEqual(f.calls, [2]);
    await getSelectionContext(f.reader, 1, "First");
    await getSelectionContext(f.reader, 3, "Last");
    assert.ok(f.calls.every((n) => n >= 1 && n <= 3));
    const only = pdfFixture([["Only one page."]]);
    await getSelectionContext(only.reader, 1, "Only");
    assert.deepEqual(only.calls, [1]);
});

test("selection across a page break uses the real neighboring text", async () => {
    const f = pdfFixture([
        ["Introduction.", "Models generalize"],
        ["across document types. We test this.", "Another paragraph."],
        ["Unrelated page."],
    ]);
    const selected = "Models generalize across document types.";
    const result = await getSelectionContext(f.reader, 2, selected);
    assert.ok(result.includes(selected));
    assert.ok(result.includes("We test this."));
    const fromPrevious = await getSelectionContext(f.reader, 1, selected);
    assert.ok(fromPrevious.includes(selected));
});

test("adjacent read failures preserve current context and can be retried", async () => {
    const f = pdfFixture([
        ["Previous text."],
        ["Current selected text."],
        ["Next text."],
    ]);
    f.failures.add(1);
    let result = await getSelectionContext(f.reader, 2, "selected");
    assert.ok(result.includes("Current selected text."));
    assert.ok(result.includes("Next text."));
    assert.ok(!result.includes("Previous text."));
    f.failures.delete(1);
    result = await getSelectionContext(f.reader, 2, "selected");
    assert.ok(result.includes("Previous text."));
    assert.equal(f.calls.filter((n) => n === 1).length, 2);
});

test("snippets stay within 800 characters per side and total context within 12000", async () => {
    const f = pdfFixture([
        ["P".repeat(5000)],
        ["Current selected sentence."],
        ["N".repeat(5000)],
    ]);
    const result = await getSelectionContext(f.reader, 2, "selected");
    assert.equal((result.match(/P/g) || []).length, 800);
    assert.equal((result.match(/N/g) || []).length, 800);
    const long = "Current " + "x".repeat(11980) + " selected.";
    const g = pdfFixture([["P".repeat(1000)], [long], ["N".repeat(1000)]]);
    const bounded = await getSelectionContext(g.reader, 2, long);
    assert.ok(bounded.length <= 12000);
    assert.ok(bounded.includes(long));
});

test("neighboring repeated words never replace the selection's current-page meaning", async () => {
    const f = pdfFixture([
        ["target appears earlier."],
        ["Our target is current."],
        ["target appears later."],
    ]);
    const result = await getSelectionContext(f.reader, 2, "target");
    assert.ok(result.includes("Our target is current."));
});

test("unresponsive adjacent pages time out without blocking the current selection", async () => {
    const f = pdfFixture([["Previous text."], ["Current selected sentence."]]);
    const original = f.pdf.getPage;
    f.pdf.getPage = (n) => (n === 1 ? new Promise(() => {}) : original(n));
    assert.equal(
        await getSelectionContext(f.reader, 2, "selected"),
        "Current selected sentence.",
    );
});

test("missing bridges and invalid pages keep a bounded selection fallback", async () => {
    const selected = "s".repeat(15000);
    assert.equal((await getSelectionContext({}, 2, selected)).length, 12000);
    const f = pdfFixture([["Text."]]);
    for (const page of [0, -1, 1.5, undefined])
        assert.equal(
            await getSelectionContext(f.reader, page, "  selected  "),
            "selected",
        );
    assert.deepEqual(f.calls, []);
});

test("Gecko promise wrappers expose PDF page methods and text only through wrappedJSObject", async () => {
    const f = pdfFixture([
        ["The first sub-layer is a self-attention mechanism."],
    ]);
    const getPage = f.pdf.getPage.bind(f.pdf);
    const wrap = (value) => ({
        wrappedJSObject: value,
        get then() {
            throw new Error('Permission denied to access property "then"');
        },
    });
    f.pdf.getPage = (number) =>
        wrap(
            getPage(number).then((page) => ({
                wrappedJSObject: {
                    getTextContent: () =>
                        wrap(
                            page.getTextContent().then((content) => ({
                                wrappedJSObject: content,
                            })),
                        ),
                },
            })),
        );
    assert.equal(
        await getSelectionContext(f.reader, 1, "mechanism"),
        "The first sub-layer is a self-attention mechanism.",
    );
    await getSelectionContext(f.reader, 1, "mechanism");
    assert.deepEqual(f.calls, [1]);
});
