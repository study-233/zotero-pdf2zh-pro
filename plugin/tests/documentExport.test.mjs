import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { Blob, Buffer } from "node:buffer";
import { URL } from "node:url";
import ts from "typescript";

const { Response, FormData } = globalThis;

const source = fs.readFileSync(
    new URL("../src/modules/documentExport.ts", import.meta.url),
    "utf8",
);
const compiled = ts
    .transpileModule(source, {
        compilerOptions: {
            module: ts.ModuleKind.ESNext,
            target: ts.ScriptTarget.ES2022,
        },
    })
    .outputText.replace(/^import .*;$/gm, "");
let sequence = 0;
async function fixture(options = {}) {
    const requests = [],
        writes = [],
        notices = [],
        reads = [],
        pickers = [];
    let unload, shown, readyResolve;
    const ready = new Promise((resolve) => {
        readyResolve = resolve;
    });
    globalThis.getString = (key) => key;
    globalThis.getPref = (key) => {
        assert.equal(key, "new_serverip");
        return "http://127.0.0.1:8891";
    };
    globalThis.Zotero = {
        getMainWindow: () => ({}),
        Utilities: { randomString: () => "request-export-123456789" },
        alert: (_win, _title, text) => notices.push(text),
    };
    globalThis.ztoolkit = {
        getGlobal: () => ({
            getSelectedItems: () =>
                options.items || [{ id: options.id || "original" }],
        }),
        FilePicker: class {
            constructor(...args) {
                pickers.push(args);
            }
            async open() {
                return options.picker === false
                    ? false
                    : `/export/${pickers.at(-1)[3]}`;
            }
        },
        Dialog: class {
            constructor() {
                this.window = {
                    close: () => {
                        unload?.();
                    },
                };
            }
            addCell() {
                return this;
            }
            addButton(_label, _id, opts) {
                shown = opts.callback;
                return this;
            }
            setDialogData(data) {
                unload = data.unloadCallback;
                return this;
            }
            open() {
                return this;
            }
        },
    };
    globalThis.PDF2zhHelperFactory = {
        prepareFileData: async (item, binary) => {
            assert.equal(binary, true);
            reads.push(item.id);
            return {
                fileName: "paper.pdf",
                file: new Blob(["test PDF"], { type: "application/pdf" }),
            };
        },
    };
    globalThis.IOUtils = {
        write: async (path, bytes) => {
            if (options.diskError) throw new Error("private path");
            writes.push({ path, bytes });
        },
        remove: async () => {},
    };
    globalThis.diagnosticFetch = async (url, init = {}) => {
        requests.push({ url, init });
        if (url.endsWith("/health"))
            return Response.json({
                capabilities: {
                    structuredExport: options.supported !== false,
                },
            });
        if (url.endsWith("/cancel")) return Response.json({ status: "ok" });
        assert.equal(init.headers["X-PDF2ZH-Client"], "zotero");
        assert.ok(init.body instanceof FormData);
        assert.deepEqual([...init.body.keys()], ["file", "format"]);
        if (options.wait) {
            readyResolve();
            return new Promise((_resolve, reject) =>
                init.signal.addEventListener(
                    "abort",
                    () => reject(new Error("aborted")),
                    { once: true },
                ),
            );
        }
        return options.failure
            ? Response.json({ code: options.failure }, { status: 422 })
            : new Response(`exported ${init.body.get("format")}`);
    };
    const module = await import(
        "data:text/javascript;base64," +
            Buffer.from(compiled).toString("base64") +
            `#${sequence++}`
    );
    return {
        module,
        requests,
        writes,
        notices,
        reads,
        pickers,
        ready,
        cancel: () => shown(),
    };
}

for (const id of ["original", "translated"]) {
    for (const [format, name] of [
        ["md", "Markdown"],
        ["json", "JSON"],
        ["txt", "TXT"],
    ]) {
        test(`exports ${format} from the selected ${id} attachment without model settings`, async () => {
            const f = await fixture({ id });
            await f.module.exportSelectedDocument(format);
            assert.deepEqual(f.reads, [id]);
            assert.equal(f.writes.length, 1);
            assert.equal(f.requests.length, 2);
            assert.deepEqual(f.notices, ["export-saved"]);
            assert.deepEqual(f.pickers[0].slice(1), [
                "save",
                [[name, `*.${format}`]],
                `paper.${format}`,
            ]);
            assert.equal(f.requests[1].init.body.get("format"), format);
            assert.equal(f.writes[0].path, `/export/paper.${format}`);
            assert.equal(
                Buffer.from(f.writes[0].bytes).toString("utf8"),
                `exported ${format}`,
            );
        });
    }
}
test("save cancellation and ambiguous selection do not upload", async () => {
    for (const opts of [{ picker: false }, { items: [{}, {}] }]) {
        const f = await fixture(opts);
        await f.module.exportSelectedDocument("md");
        assert.equal(f.requests.length, 0);
        assert.equal(f.writes.length, 0);
    }
});
test("old server is detected before upload", async () => {
    const f = await fixture({ supported: false });
    await f.module.exportSelectedDocument("txt");
    assert.equal(f.requests.length, 1);
    assert.deepEqual(f.notices, ["export-upgrade-server"]);
    assert.equal(f.writes.length, 0);
});
test("cancel and unload abort upload and send only one cancellation", async () => {
    const f = await fixture({ wait: true });
    const pending = f.module.exportSelectedDocument("json");
    await f.ready;
    await f.module.exportSelectedDocument("txt");
    f.cancel();
    f.module.cancelDocumentExport();
    await pending;
    assert.equal(f.writes.length, 0);
    assert.equal(f.notices.length, 0);
    assert.equal(f.requests.filter((x) => x.url.endsWith("/cancel")).length, 1);
    assert.equal(
        f.requests.filter((x) => x.url.endsWith("/document-exports")).length,
        1,
    );
});
test("server error content is never shown and save failures are actionable", async () => {
    for (const [options, expected] of [
        [{ failure: "export_needs_ocr" }, "export-needs-ocr"],
        [{ failure: "SECRET PDF" }, "export-failed"],
        [{ diskError: true }, "export-save-failed"],
    ]) {
        const f = await fixture(options);
        await f.module.exportSelectedDocument("md");
        assert.deepEqual(f.notices, [expected]);
    }
});

test("export submenu commands pass the selected format to the hook", () => {
    const menuSource = fs.readFileSync(
        new URL("../src/modules/pdf2zh.ts", import.meta.url),
        "utf8",
    );
    const menuCode = ts
        .transpileModule(menuSource, {
            compilerOptions: {
                module: ts.ModuleKind.ESNext,
                target: ts.ScriptTarget.ES2022,
            },
        })
        .outputText.replace(/^import .*;$/gm, "")
        .replaceAll("export class", "class");
    let registered;
    const commands = [];
    const addon = {
        data: { config: { addonRef: "test", addonID: "test" } },
        hooks: { onDialogEvents: (...args) => commands.push(args) },
    };
    const factory = new Function(
        "Zotero",
        "addon",
        "getString",
        menuCode + ";return PDF2zhUIFactory;",
    )(
        {
            MenuManager: {
                registerMenu: (menu) => {
                    registered = menu;
                    return "menu";
                },
            },
        },
        addon,
        (key) => key,
    );
    factory.registerRightClickMenuItem();
    const submenu = registered.menus[0].menus[2];
    assert.equal(submenu.menuType, "submenu");
    assert.equal(submenu.menus.length, 3);
    for (const [index, format] of ["md", "json", "txt"].entries()) {
        const menu = submenu.menus[index];
        let label;
        menu.onShowing(null, {
            menuElem: {
                setAttribute: (_key, value) => {
                    label = value;
                },
            },
        });
        assert.equal(label, `export-format-${format}`);
        assert.equal(menu.menuType, "menuitem");
        menu.onCommand();
    }
    assert.deepEqual(commands, [
        ["exportDocument", "md"],
        ["exportDocument", "json"],
        ["exportDocument", "txt"],
    ]);
});
