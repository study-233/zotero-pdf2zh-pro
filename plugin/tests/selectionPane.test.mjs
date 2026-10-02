import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

function fixture() {
    let options;
    const readers = new Map();
    const details = [];
    const calls = [];
    const pane = { mode: "notes" };
    const context = { collapsed: true, context: pane };
    const win = {
        ZoteroContextPane: context,
        document: {
            querySelectorAll: () => details,
            getElementById: () => pane,
        },
    };
    const exports = {};
    const imports = {
        "../../package.json": { config: { addonID: "test", addonRef: "test" } },
        "../utils/locale": {
            getString: () => "Select text",
            getLocaleID: (id) => id,
        },
        "./diagnostics": { recordDiagnostic: (event) => calls.push(event) },
    };
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/selectionPane.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    new Function("require", "exports", "Zotero", code)(
        (name) => imports[name],
        exports,
        {
            Reader: { getByTabID: (id) => readers.get(id) },
            ItemPaneManager: {
                registerSection(value) {
                    options = value;
                    return "translation-pane";
                },
                unregisterSection(id) {
                    calls.push(id);
                },
            },
            getMainWindow: () => win,
        },
    );
    exports.registerSelectionPane();
    function add(tabID) {
        const reader = { type: "pdf", tabID, itemID: 42, _window: win };
        readers.set(tabID, reader);
        const detail = {
            tabID,
            getAttribute: () => tabID,
            render: async () => options.onRender(args),
            scrollToPane: async (id) => calls.push([tabID, id]),
        };
        details.push(detail);
        const body = {
            closest: () => detail,
            childNodes: [],
            isConnected: true,
        };
        const args = {
            body,
            tabType: "reader",
            paneID: "translation-pane",
            setEnabled(value) {
                body.enabled = value;
            },
        };
        return { reader, body, args };
    }
    return {
        ...exports,
        add,
        calls,
        context,
        pane,
        get options() {
            return options;
        },
    };
}

test("pane routes by Reader tab even when two tabs share the same item", async () => {
    const f = fixture();
    const a = f.add("tab-a"),
        b = f.add("tab-b");
    const updates = [];
    f.watchSelectionPane(a.reader, (body) => updates.push(["a", body]));
    f.watchSelectionPane(b.reader, (body) => updates.push(["b", body]));
    assert.equal(await f.openSelectionPane(a.reader), a.body);
    assert.equal(await f.openSelectionPane(b.reader), b.body);
    assert.deepEqual(updates, [
        ["a", a.body],
        ["b", b.body],
    ]);
    assert.equal(f.context.collapsed, false);
    assert.equal(f.pane.mode, "item");
    a.body.childNodes = ["translation A"];
    a.body.textContent = "translation A";
    f.options.onRender(a.args);
    assert.equal(a.body.textContent, "translation A");
    assert.equal(updates.length, 2);
    f.options.onDestroy({ body: b.body });
    assert.deepEqual(updates.at(-1), ["b", undefined]);
});

test("unsupported Reader host leaves pane collapsed and disposal stops callbacks", async () => {
    const f = fixture();
    assert.equal(await f.openSelectionPane({ tabID: "missing" }), undefined);
    assert.equal(f.context.collapsed, true);
    const a = f.add("a");
    let notifications = 0;
    const stop = f.watchSelectionPane(a.reader, () => notifications++);
    await f.openSelectionPane(a.reader);
    stop();
    f.options.onDestroy({ body: a.body });
    assert.equal(notifications, 1);
    f.unregisterSelectionPane();
    assert.equal(f.calls.at(-1), "translation-pane");
    assert.equal(await f.openSelectionPane(a.reader), undefined);
});

test("library and non-PDF panes are not enabled", () => {
    const f = fixture();
    const a = f.add("a");
    f.options.onItemChange({ ...a.args, tabType: "library" });
    assert.equal(a.body.enabled, false);
    a.reader.type = "epub";
    f.options.onItemChange(a.args);
    assert.equal(a.body.enabled, false);
});
