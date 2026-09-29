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

for (const language of ["zh-CN", "en-US"]) {
    test(`${language} preferences save only valid changes, preview, reset, reopen, and disable`, () => {
        const prefs = new Map();
        const labels = new Map(
            fs
                .readFileSync(
                    new URL(
                        `../addon/locale/${language}/addon.ftl`,
                        import.meta.url,
                    ),
                    "utf8",
                )
                .split(/\r?\n/)
                .filter((line) => line.startsWith("attachment-"))
                .map((line) => line.split(" = ")),
        );
        const nodes = new Map();
        const node = (id) => {
            if (!nodes.has(id))
                nodes.set(id, {
                    value: "",
                    checked: true,
                    events: {},
                    attrs: {},
                    addEventListener(event, listener) {
                        this.events[event] = listener;
                    },
                    setAttribute(name, value) {
                        this.attrs[name] = value;
                    },
                });
            return nodes.get(id);
        };
        const window = {
            document: {
                getElementById: (id) =>
                    node(id.replace("zotero-prefpane-test-", "")),
            },
        };
        const { registerAttachmentNamingPreferences: register } = load(
            "attachmentNamingPreferences",
            {
                "../../package.json": { config: { addonRef: "test" } },
                "../utils/prefs": {
                    getPref: (key) => prefs.get(key),
                    setPref: (key, value) => prefs.set(key, value),
                },
                "../utils/locale": {
                    getString: (key) => {
                        assert.ok(labels.has(key));
                        return labels.get(key);
                    },
                },
                "./attachmentNaming": naming,
            },
        );
        register(window);
        const input = node("attachmentTitleTemplate");
        const edit = (value) => {
            input.value = value;
            input.events.input();
        };
        const preview = (mode) =>
            node(`attachmentTitlePreview-${mode}`).textContent;
        for (const mode of ["mono", "dual"])
            assert.equal(
                preview(mode),
                renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, {
                    ...context,
                    type: labels.get(`attachment-type-${mode}`),
                }),
            );
        edit("{title} · {model} · {type}");
        assert.equal(
            preview("dual"),
            `Attention Is All You Need · gpt-4.1-mini · ${labels.get("attachment-type-dual")}`,
        );
        assert.equal(
            prefs.get("attachmentTitleTemplate"),
            "{title} · {model} · {type}",
        );
        edit("{author} {year} {type}");
        const saved = prefs.get("attachmentTitleTemplate");
        edit("{notSupported}");
        assert.equal(prefs.get("attachmentTitleTemplate"), saved);
        assert.equal(node("attachmentTitleError").hidden, false);
        assert.equal(input.attrs["aria-invalid"], "true");
        assert.equal(
            preview("dual"),
            `Vaswani et al. 2017 ${labels.get("attachment-type-dual")}`,
        );
        nodes.clear();
        register(window);
        assert.equal(node("attachmentTitleTemplate").value, saved);
        const reopened = node("attachmentTitleTemplate");
        reopened.value = " ";
        reopened.events.input();
        reopened.events.blur();
        assert.equal(
            prefs.get("attachmentTitleTemplate"),
            DEFAULT_TITLE_TEMPLATE,
        );
        assert.equal(reopened.value, DEFAULT_TITLE_TEMPLATE);
        reopened.value = "Literal";
        reopened.events.input();
        assert.equal(preview("dual"), "Literal");
        node("attachmentTitleReset").events.click();
        assert.equal(reopened.value, DEFAULT_TITLE_TEMPLATE);
        node("rename").checked = false;
        node("rename").events.command();
        assert.equal(reopened.disabled, true);
        assert.equal(node("attachmentTitleReset").disabled, true);
        assert.equal(preview("dual"), "paper.zh-CN.dual.pdf");
        node("rename").checked = true;
        node("rename").events.command();
        assert.equal(reopened.disabled, false);
    });
}

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
