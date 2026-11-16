import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";
import { JSDOM } from "jsdom";

const compile = (name) =>
    ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/" + name + ".ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;

function setup() {
    const submitted = [],
        dialogs = [];
    let action;
    const item = {
        id: 91,
        attachmentFilename: "read.pdf",
        isPDFAttachment: () => true,
    };
    const win = {
        ZoteroPane: { getSelectedItems: () => [item] },
    };
    const imports = {
        "../../package.json": { config: { addonRef: "test", addonID: "test" } },
        "../utils/locale": { getString: (k) => k },
        "./pageTrialDialog": {
            selectTrialPages(_win, _title, total, initial, parse) {
                const args = { total, initial, parse };
                dialogs.push(args);
                return action?.(args);
            },
        },
        "./diagnostics": {},
        "./pdf2zhHelper": {
            PDF2zhHelperFactory: { getAttachmentItem: async (i) => i },
        },
        "./pdf2zhTaskManager": {
            PDF2zhTaskManager: {
                processWorker: async (trial) => submitted.push(trial),
            },
        },
    };
    const exports = {};
    new Function("require", "exports", "Zotero", compile("pageTrial"))(
        (n) => imports[n],
        exports,
        {
            getMainWindow: () => win,
            Items: { get: () => [item] },
            Fulltext: { getPages: async () => ({ total: 12 }) },
        },
    );
    return {
        ...exports,
        dialogs,
        submitted,
        item,
        win,
        setAction: (fn) => {
            action = fn;
        },
    };
}

test("noncontiguous trial ranges merge duplicate, adjacent and overlapping physical pages", () => {
    const { parseTrialPages } = setup();
    assert.deepEqual(parseTrialPages(" 08，3–5,1,4-6,8 ", 12), {
        pages: "1,3-6,8",
        count: 6,
    });
    for (const str of [
        "",
        "3,",
        "0",
        "4-3",
        "1,13",
        "2.3",
        "-3",
        "1-",
        "1,,3",
        "3/5",
    ])
        assert.equal(parseTrialPages(str, 12), undefined);
});

test("the reader uses the clicked or active split page, and cancel never submits", async () => {
    const f = setup(),
        reader = {
            itemID: 91,
            _window: f.win,
            _internalReader: {
                _lastViewPrimary: false,
                _state: {
                    primaryViewStats: { pagesCount: 12, pageIndex: 0 },
                    secondaryViewStats: { pagesCount: 12, pageIndex: 8 },
                },
            },
        };
    await f.openPageTrial(reader);
    assert.equal(f.dialogs[0].initial, "9");
    await f.openPageTrial(reader, 3);
    assert.equal(f.dialogs[1].initial, "4");
    assert.deepEqual(f.submitted, []);
    f.setAction((args) => {
        return args.parse("2,2-4", args.total).pages;
    });
    await f.openPageTrial();
    assert.equal(f.dialogs[2].initial, "");
    assert.deepEqual(f.submitted, [{ item: f.item, pages: "2-4" }]);
});

test("in-window page picker validates edits and presets, and cleans up every dismissal path", async () => {
    const template = fs.readFileSync(
        new URL("../addon/content/pageTrial.xhtml", import.meta.url),
        "utf8",
    );
    const dom = new JSDOM("<main></main>");
    const win = dom.window;
    const doc = win.document;
    win.HTMLDialogElement.prototype.showModal = function () {
        this.open = true;
    };
    win.HTMLDialogElement.prototype.close = function () {
        this.open = false;
    };
    const exports = {};
    const imports = {
        "../../package.json": { config: { addonRef: "test" } },
        "../utils/locale": {
            getString: (key, options) =>
                [key, ...Object.values(options?.args ?? {})].join(" "),
        },
    };
    new Function("require", "exports", "Zotero", compile("pageTrialDialog"))(
        (n) => imports[n],
        exports,
        {
            File: { getResourceAsync: async () => template },
        },
    );
    const open = (initial = "", total = 12) =>
        exports.selectTrialPages(
            win,
            "<paper>.pdf",
            total,
            initial,
            setup().parseTrialPages,
        );
    const first = open("7");
    await Promise.resolve();
    const root = doc.querySelector("dialog");
    const input = root.querySelector("input");
    const start = root.querySelector('[type="submit"]');
    assert.equal(
        root.querySelector("#pdf2zh-trial-file").textContent,
        "<paper>.pdf",
    );
    assert.equal(doc.activeElement, input);
    assert.equal(input.value, "7");
    assert.equal(await open(), undefined);
    const edit = (value) => {
        input.value = value;
        input.dispatchEvent(new win.Event("input"));
    };
    edit("13");
    assert.ok(start.disabled);
    assert.equal(input.getAttribute("aria-invalid"), "true");
    assert.equal(root.querySelector(".map").childElementCount, 0);
    root.querySelector(".presets button:last-child").click();
    assert.equal(input.value, "1-3");
    root.querySelector(".presets button").click();
    assert.equal(input.value, "7");
    edit("4,2-4,8");
    assert.equal(root.querySelector(".map").childElementCount, 2);
    assert.equal(
        root.querySelector('[role="status"]').textContent,
        "trial-summary 2–4, 8 4",
    );
    start.click();
    assert.equal(await first, "2-4,8");
    assert.equal(doc.querySelector("dialog"), null);
    for (const close of [
        (root) => root.querySelector(".cancel").click(),
        (root) =>
            root.dispatchEvent(new win.Event("cancel", { cancelable: true })),
        () => exports.closePageTrialDialogs(),
        () => win.dispatchEvent(new win.Event("unload")),
    ]) {
        const result = open("", 1);
        await Promise.resolve();
        const root = doc.querySelector("dialog");
        assert.ok(root.querySelector('[type="submit"]').disabled);
        assert.equal(root.querySelectorAll(".presets button").length, 1);
        close(root);
        assert.equal(await result, undefined);
        assert.equal(doc.querySelector("dialog"), null);
    }
    dom.window.close();
});
