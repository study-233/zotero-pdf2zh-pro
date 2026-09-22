import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

function loadModule(name, globals, imports = {}) {
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
    new Function("require", "exports", ...Object.keys(globals), code)(
        (name) => imports[name] || {},
        exports,
        ...Object.values(globals),
    );
    return exports;
}

function menuFixture(registerResult) {
    const registrations = [];
    const commands = [];
    const { PDF2zhUIFactory } = loadModule(
        "pdf2zh",
        {
            Zotero: {
                MenuManager: {
                    registerMenu(options) {
                        registrations.push(options);
                        return registerResult?.(options) ?? options.menuID;
                    },
                },
            },
            addon: {
                data: {
                    config: {
                        addonRef: "pdf2zhpro",
                        addonID: "pdf2zhpro@test",
                    },
                },
                hooks: { onDialogEvents: (command) => commands.push(command) },
            },
        },
        {
            "../utils/locale": {
                getString: (key) =>
                    ({
                        "prefs-menu-translate": "Translate PDF",
                        "prefs-menu-tasks": "Task Manager",
                    })[key],
            },
        },
    );
    return { factory: PDF2zhUIFactory, registrations, commands };
}

test("native item menu registers once across main-window loads and retains both actions", () => {
    const { factory, registrations, commands } = menuFixture();
    factory.registerRightClickMenuItem();
    factory.registerRightClickMenuItem();
    assert.equal(registrations.length, 1);
    const options = registrations[0];
    assert.equal(options.target, "main/library/item");
    assert.equal(options.pluginID, "pdf2zhpro@test");
    const submenu = options.menus[0];
    const labels = [];
    for (const menu of [submenu, ...submenu.menus]) {
        menu.onShowing(
            {},
            {
                menuElem: {
                    setAttribute(name, value) {
                        assert.equal(name, "label");
                        labels.push(value);
                    },
                },
            },
        );
    }
    assert.deepEqual(labels, [
        "zotero-pdf2zh-pro",
        "zotero-pdf2zh-pro: Translate PDF",
        "zotero-pdf2zh-pro: Task Manager",
    ]);
    for (const menu of submenu.menus) menu.onCommand();
    assert.deepEqual(commands, ["translatePDF", "openTaskManager"]);
});

test("failed native registration can retry on a later main-window load", () => {
    let attempts = 0;
    const { factory, registrations } = menuFixture((options) =>
        ++attempts === 1 ? false : options.menuID,
    );
    factory.registerRightClickMenuItem();
    factory.registerRightClickMenuItem();
    factory.registerRightClickMenuItem();
    assert.equal(registrations.length, 2);
});

test("attachment import handles an unavailable parent returned as false", async () => {
    const imports = [];
    const { PDF2zhHelperFactory } = loadModule("pdf2zhHelper", {
        Zotero: {
            Items: { get: () => false },
            Attachments: {
                async importFromFile(options) {
                    imports.push(options);
                    return { id: 99 };
                },
            },
        },
    });
    await PDF2zhHelperFactory.addAttachment({
        item: {
            isAttachment: () => true,
            parentItemID: 42,
            libraryID: 1,
            getField: () => "",
        },
        filePath: "/tmp/paper-dual.pdf",
        options: { rename: true, openAfterProcess: false },
        outputMode: "dual",
        service: "openai",
    });
    assert.equal(imports.length, 1);
    assert.equal(imports[0].parentItemID, 42);
    assert.equal(imports[0].title, "openai-dual");
});
