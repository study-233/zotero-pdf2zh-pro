import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { URL } from "node:url";

function load(name, imports = {}) {
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
    new Function("require", "exports", code)((name) => imports[name], exports);
    return exports;
}
const naming = load("attachmentNaming");
const {
    buildTitleMetadata,
    renderAttachmentTitle,
    validateTitleTemplate,
    DEFAULT_TITLE_TEMPLATE,
} = naming;
const context = {
    title: "Attention Is All You Need",
    fullTitle: "Attention Is All You Need",
    author: "Vaswani et al.",
    year: "2017",
    sourceLang: "en",
    targetLang: "zh-CN",
    service: "openai",
    model: "gpt-4.1-mini",
    type: "双语对照",
};

test("metadata prefers short title then full title then source filename", () => {
    assert.equal(
        buildTitleMetadata({
            fileName: "original.PDF",
            shortTitle: "  Short  ",
            fullTitle: "Full",
        }).title,
        "Short",
    );
    assert.equal(
        buildTitleMetadata({
            fileName: "original.PDF",
            shortTitle: " ",
            fullTitle: "Full",
        }).title,
        "Full",
    );
    assert.deepEqual(buildTitleMetadata({ fileName: "original.PDF" }), {
        title: "original",
        fullTitle: "original",
        author: "",
        year: "",
    });
    assert.equal(
        buildTitleMetadata({ fileName: "a.pdf", date: "2024-09-29" }).year,
        "2024",
    );
});

test("all variables and literal templates render without forcing a type", () => {
    assert.equal(
        renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, context),
        "Attention Is All You Need · 双语对照",
    );
    assert.equal(
        renderAttachmentTitle(
            "{author} ({year}) {fullTitle} {sourceLang} → {targetLang} {service} {type}",
            context,
        ),
        "Vaswani et al. (2017) Attention Is All You Need en → zh-CN openai 双语对照",
    );
    assert.equal(renderAttachmentTitle(" My PDF ", context), "My PDF");
    assert.equal(
        renderAttachmentTitle("{title}", {
            ...context,
            title: "A {type} title",
        }),
        "A {type} title",
    );
    assert.equal(
        renderAttachmentTitle("{author} \n  {year}", {
            ...context,
            author: "",
            year: "",
        }),
        renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, context),
    );
    assert.equal(
        renderAttachmentTitle("{author}   {title}", { ...context, author: "" }),
        context.title,
    );
});

test("invalid templates and empty values fall back safely", () => {
    for (const template of [
        "{unknown}",
        "{title",
        "title}",
        "{{title}}",
        "{}",
        "{ title }",
    ]) {
        assert.equal(validateTitleTemplate(template), false, template);
        assert.equal(
            renderAttachmentTitle(template, context),
            renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, context),
        );
    }
    for (const template of ["", " ", "{title} · {type}", "plain text"])
        assert.equal(validateTitleTemplate(template), true);
    assert.equal(
        renderAttachmentTitle(" ", context),
        renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, context),
    );
});

const layout = load("attachmentTitleLayout", { "./attachmentNaming": naming });
test("visual layouts preserve every field and literal text, skip missing fields, and fall back from empty results", () => {
    const value = {
        version: 1,
        blocks: [
            { kind: "text", text: "精读版" },
            { kind: "field", field: "author" },
            { kind: "field", field: "year" },
            { kind: "field", field: "model" },
            { kind: "field", field: "type" },
        ],
        separator: " · ",
    };
    assert.equal(
        layout.renderTitleLayout(value, { ...context, author: "", year: "" }),
        "精读版 · gpt-4.1-mini · 双语对照",
    );
    assert.equal(
        layout.renderTitleLayout(
            { ...value, blocks: [{ kind: "field", field: "model" }] },
            { ...context, model: "" },
        ),
        renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, context),
    );
    assert.equal(
        layout.hasTitleContent({
            ...value,
            blocks: [{ kind: "text", text: "  " }],
        }),
        false,
    );
    assert.equal(
        layout.parseTitleLayout(JSON.stringify(value)).blocks.length,
        5,
    );
});
test("legacy templates migrate without losing literal punctuation or repeated fields", () => {
    for (const template of [
        "",
        "plain text",
        "【{title}】 ({year}) · {model} / {type}",
        "{title} · {type}",
        "{title}_{year}_{type}",
        "{title}{title}",
        "prefix  {author}  suffix",
    ]) {
        const migrated = layout.migrateTitleTemplate(template);
        assert.ok(migrated, template);
        assert.equal(
            layout.renderTitleLayout(migrated, context),
            renderAttachmentTitle(template, context),
            template,
        );
        if (migrated.separator === "")
            assert.equal(
                layout.renderTitleLayout(migrated, {
                    ...context,
                    author: "",
                    year: "",
                    model: "",
                }),
                renderAttachmentTitle(template, {
                    ...context,
                    author: "",
                    year: "",
                    model: "",
                }),
            );
    }
    assert.equal(layout.migrateTitleTemplate("{unknown}"), undefined);
    assert.equal(layout.migrateTitleTemplate("{{title}}"), undefined);
});
test("malformed visual layouts fail closed instead of replacing existing preferences", () => {
    for (const raw of [
        "bad",
        "null",
        "{}",
        JSON.stringify({ version: 1, separator: 1, blocks: [] }),
        JSON.stringify({
            version: 1,
            separator: "",
            blocks: [{ kind: "field", field: "unknown" }],
        }),
    ])
        assert.equal(layout.parseTitleLayout(raw), undefined);
    assert.notEqual(
        layout.defaultTitleLayout().blocks,
        layout.defaultTitleLayout().blocks,
    );
});

test("model templates accept model IDs verbatim and leave missing model records empty", () => {
    assert.equal(validateTitleTemplate("{title} · {model} · {type}"), true);
    assert.equal(
        renderAttachmentTitle("{title} · {model} · {type}", context),
        "Attention Is All You Need · gpt-4.1-mini · 双语对照",
    );
    assert.equal(
        renderAttachmentTitle("{model}", {
            ...context,
            model: "provider/model-v2:latest",
        }),
        "provider/model-v2:latest",
    );
    assert.equal(
        renderAttachmentTitle("{model} {type}", { ...context, model: "" }),
        "双语对照",
    );
    assert.equal(
        renderAttachmentTitle("{model}", { ...context, model: "" }),
        "Attention Is All You Need · 双语对照",
    );
});

test("blank literal blocks do not add separators and oversized legacy layouts stay unchanged", () => {
    const value = layout.defaultTitleLayout();
    value.blocks.splice(1, 0, { kind: "text", text: "   " });
    assert.equal(
        layout.renderTitleLayout(value, context),
        renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, context),
    );
    assert.equal(
        layout.migrateTitleTemplate(Array(101).fill("{title}").join(" · ")),
        undefined,
    );
});
