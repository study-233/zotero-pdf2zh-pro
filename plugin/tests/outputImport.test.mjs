import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const naming = await import(
    `data:text/javascript;base64,${Buffer.from(ts.transpileModule(fs.readFileSync(new URL("../src/modules/attachmentNaming.ts", import.meta.url), "utf8"), { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText).toString("base64")}`
);
const layoutExports = {};
new Function(
    "require",
    "exports",
    ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/attachmentTitleLayout.ts", import.meta.url),
            "utf8",
        ),
        { compilerOptions: { module: ts.ModuleKind.CommonJS } },
    ).outputText,
)(() => naming, layoutExports);
const state = {};
globalThis.PathUtils = {
    tempDir: "/virtual/temp",
    join: path.posix.join,
    filename: path.posix.basename,
};
globalThis.IOUtils = {
    createUniqueDirectory: async (parent, prefix) => {
        const directory = path.posix.join(parent, `${prefix}${++state.nextId}`);
        state.directories.add(directory);
        return directory;
    },
    write: async (file, bytes) => {
        state.files.set(file, [...bytes]);
        await state.onWrite?.(file);
    },
    remove: async (directory, options) => {
        assert.equal(options.recursive, true);
        assert.equal(options.ignoreAbsent, true);
        assert.equal(
            path.posix.dirname(directory),
            globalThis.PathUtils.tempDir,
        );
        assert.ok(state.directories.has(directory));
        state.removals.push(directory);
        state.directories.delete(directory);
        for (const file of state.files.keys()) {
            if (path.posix.dirname(file) === directory)
                state.files.delete(file);
        }
    },
};
globalThis.Zotero = {
    Attachments: {
        importFromFile: async ({ file, parentItemID, title }) => {
            await state.onImport?.(file, parentItemID);
            assert.ok(state.files.has(file));
            state.imports.push({
                file,
                itemID: parentItemID,
                title,
                bytes: [...state.files.get(file)],
            });
            return { id: parentItemID + 100 };
        },
    },
    Reader: { open: (id) => state.opened.push(id) },
};
globalThis.__outputImportTest = {
    ...naming,
    ...layoutExports,
    getString: (key) => (key.endsWith("mono") ? "译文" : "双语对照"),
    getPref: (key) =>
        key === "openAfterTranslate"
            ? state.openAfterProcess
            : state.prefs[key],
};
const namingSettings = {};
new Function(
    "require",
    "exports",
    ts.transpileModule(
        fs.readFileSync(
            new URL(
                "../src/modules/attachmentNamingSettings.ts",
                import.meta.url,
            ),
            "utf8",
        ),
        { compilerOptions: { module: ts.ModuleKind.CommonJS } },
    ).outputText,
)(
    (name) =>
        ({
            "./attachmentNaming": naming,
            "./attachmentTitleLayout": layoutExports,
            "../utils/prefs": {
                getPref: (key) => state.prefs[key],
                setPref: (key, value) => {
                    state.prefs[key] = value;
                },
            },
        })[name],
    namingSettings,
);
Object.assign(globalThis.__outputImportTest, namingSettings);

const source = fs
    .readFileSync(
        new URL("../src/modules/pdf2zhHelper.ts", import.meta.url),
        "utf8",
    )
    .replace(/^import[\s\S]*?;\r?\n/gm, "");
const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { PDF2zhHelperFactory } = await import(
    `data:text/javascript;base64,${Buffer.from(
        "const {getPref,getString,DEFAULT_TITLE_TEMPLATE,buildTitleMetadata,renderAttachmentTitle,parseTitleLayout,renderTitleLayout,repairAttachmentNamingPreferences} = globalThis.__outputImportTest;\n" +
            compiled,
    ).toString("base64")}`
);

function reset() {
    Object.assign(state, {
        prefs: {},
        nextId: 0,
        directories: new Set(),
        files: new Map(),
        removals: [],
        imports: [],
        opened: [],
        openAfterProcess: false,
        onWrite: null,
        onImport: null,
    });
}

test("import options repair corrupted naming before preferences have been opened", () => {
    reset();
    state.prefs.attachmentTitleTemplate = "{title} � {type}";
    state.prefs.attachmentTitleLayout = JSON.stringify({
        ...layoutExports.defaultTitleLayout(),
        separator: " � ",
    });
    const options = PDF2zhHelperFactory.getPDFOptions();
    assert.equal(options.titleTemplate, naming.DEFAULT_TITLE_TEMPLATE);
    assert.equal(options.titleLayout.separator, " · ");
});

function deferred() {
    let resolve;
    const promise = new Promise((yes) => {
        resolve = yes;
    });
    return { promise, resolve };
}

function importOutput(itemID, content, isCurrent = () => true, titleSuffix) {
    return PDF2zhHelperFactory.handleOutputResponse(
        {
            fileName: "paper.zh-CN.dual.pdf",
            outputMode: "dual",
            bytes: new Uint8Array([content]),
            titleSuffix,
        },
        {
            id: itemID,
            libraryID: 1,
            isAttachment: () => false,
            getField: () => "",
        },
        { service: "OpenAI" },
        isCurrent,
    );
}

test("parallel same-name outputs retain their own PDF content and title", async () => {
    reset();
    const bothImportsStarted = deferred();
    let started = 0;
    state.onImport = async () => {
        if (++started === 2) bothImportsStarted.resolve();
        await bothImportsStarted.promise;
    };

    await Promise.all([importOutput(1, 11), importOutput(2, 22)]);

    assert.deepEqual(
        state.imports.map(({ itemID, bytes }) => ({ itemID, bytes })),
        [
            { itemID: 1, bytes: [11] },
            { itemID: 2, bytes: [22] },
        ],
    );
    assert.equal(new Set(state.imports.map(({ file }) => file)).size, 2);
    assert.ok(
        state.imports.every(({ title }) => title === "paper.zh-CN.dual.pdf"),
    );
    assert.equal(state.files.size, 0);
    assert.equal(state.directories.size, 0);
});

test("failed import cleans only its directory while another import is pending", async () => {
    reset();
    const bothImportsStarted = deferred();
    const finishSecond = deferred();
    let started = 0;
    let secondFile;
    state.onImport = async (file, itemID) => {
        if (++started === 2) bothImportsStarted.resolve();
        if (itemID === 1) {
            await bothImportsStarted.promise;
            throw new Error("first import failed");
        }
        secondFile = file;
        await finishSecond.promise;
    };

    const firstFailed = assert.rejects(
        importOutput(1, 11),
        /first import failed/,
    );
    const second = importOutput(2, 22);
    await firstFailed;
    assert.equal(state.removals.length, 1);
    assert.deepEqual(state.files.get(secondFile), [22]);
    assert.ok(state.directories.has(path.posix.dirname(secondFile)));

    finishSecond.resolve();
    await second;
    assert.deepEqual(
        state.imports.map(({ bytes }) => bytes),
        [[22]],
    );
    assert.equal(state.directories.size, 0);
    assert.equal(state.files.size, 0);
});

test("failed temporary write is cleaned without starting attachment import", async () => {
    reset();
    state.onWrite = async () => {
        throw new Error("write failed");
    };
    await assert.rejects(importOutput(1, 11), /write failed/);
    assert.deepEqual(state.imports, []);
    assert.equal(state.removals.length, 1);
    assert.equal(state.directories.size, 0);
    assert.equal(state.files.size, 0);
});

test("invalidated task during temporary write skips import and cleans its directory", async () => {
    reset();
    let current = true;
    state.onWrite = async () => {
        current = false;
    };
    await importOutput(1, 11, () => current);
    assert.deepEqual(state.imports, []);
    assert.equal(state.removals.length, 1);
    assert.equal(state.directories.size, 0);
});

test("invalidated task during attachment import does not open the reader", async () => {
    reset();
    state.openAfterProcess = true;
    let current = true;
    state.onImport = async () => {
        current = false;
    };
    await importOutput(1, 11, () => current);
    assert.equal(state.imports.length, 1);
    assert.deepEqual(state.opened, []);
    assert.equal(state.directories.size, 0);
});

test("partial and repaired imports retain distinct titles even with renaming disabled", async () => {
    reset();
    await importOutput(1, 11, () => true, "未完成·剩余 6 段·第 1 次");
    await importOutput(1, 22, () => true, "完整·第 2 次");
    assert.equal(state.imports.length, 2);
    assert.match(state.imports[0].title, /未完成·剩余 6 段·第 1 次/);
    assert.match(state.imports[1].title, /完整·第 2 次/);
    assert.deepEqual(
        state.imports.map((i) => i.bytes),
        [[11], [22]],
    );
});

test("title metadata uses the parent, while standalone attachments use the source filename", () => {
    reset();
    const fields = {
        shortTitle: "Short",
        title: "Complete title",
        firstCreator: "Author et al.",
        date: "2024-02-01",
    };
    const parent = {
        getField: (key) => fields[key],
        isAttachment: () => false,
    };
    globalThis.Zotero.Items = { get: () => parent };
    const child = { isAttachment: () => true, parentItemID: 10 };
    assert.deepEqual(
        PDF2zhHelperFactory.getTitleMetadata(child, "original.pdf"),
        {
            title: "Short",
            fullTitle: "Complete title",
            author: "Author et al.",
            year: "2024",
        },
    );
    fields.shortTitle = "";
    assert.equal(
        PDF2zhHelperFactory.getTitleMetadata(parent, "original.pdf").title,
        "Complete title",
    );
    assert.equal(
        PDF2zhHelperFactory.getTitleMetadata(
            { isAttachment: () => true },
            "original.pdf",
        ).title,
        "original",
    );
});

test("custom naming changes only imported titles and preserves both output status suffixes", async () => {
    reset();
    const item = { id: 1, libraryID: 1, isAttachment: () => false };
    const settings = {
        options: {
            rename: true,
            titleTemplate: "{author} {year} · {title} · {targetLang} · {type}",
            openAfterProcess: false,
        },
        metadata: {
            title: "Paper",
            fullTitle: "Paper full",
            author: "Writer",
            year: "2024",
        },
    };
    for (const outputMode of ["mono", "dual"]) {
        await PDF2zhHelperFactory.handleOutputResponse(
            {
                fileName: `unchanged.${outputMode}.pdf`,
                outputMode,
                bytes: new Uint8Array([1]),
                titleSuffix: "未完成·剩余 6 段·第 1 次",
            },
            item,
            { service: "openai", sourceLang: "en", targetLang: "zh-CN" },
            () => true,
            settings,
        );
    }
    assert.equal(
        state.imports[0].title,
        "Writer 2024 · Paper · zh-CN · 译文（未完成·剩余 6 段·第 1 次）",
    );
    assert.equal(
        state.imports[1].title,
        "Writer 2024 · Paper · zh-CN · 双语对照（未完成·剩余 6 段·第 1 次）",
    );
    assert.equal(
        path.posix.basename(state.imports[0].file),
        "unchanged.mono.pdf",
    );
    assert.equal(
        path.posix.basename(state.imports[1].file),
        "unchanged.dual.pdf",
    );
});

test("imported attachment titles use output model, never the current profile model", async () => {
    reset();
    const settings = {
        options: {
            rename: true,
            titleTemplate: "{title} {model} {type}",
            openAfterProcess: false,
        },
        metadata: { title: "Paper", fullTitle: "Paper", author: "", year: "" },
    };
    for (const model of ["original-model", undefined]) {
        await PDF2zhHelperFactory.handleOutputResponse(
            {
                fileName: "paper.dual.pdf",
                outputMode: "dual",
                model,
                bytes: new Uint8Array([1]),
                titleSuffix: "完整·第 2 次",
            },
            { id: 1, libraryID: 1, isAttachment: () => false },
            {
                service: "openai",
                apiConfig: { model: "current-model" },
            },
            () => true,
            settings,
        );
    }
    assert.deepEqual(
        state.imports.map(({ title }) => title),
        [
            "Paper original-model 双语对照（完整·第 2 次）",
            "Paper 双语对照（完整·第 2 次）",
        ],
    );
    assert.ok(
        state.imports.every(
            ({ file }) => path.posix.basename(file) === "paper.dual.pdf",
        ),
    );
});

test("visual attachment title uses task model, ignores missing fields, and remains isolated from later layout edits", async () => {
    reset();
    state.prefs.rename = true;
    const layout = {
        version: 1,
        separator: " · ",
        blocks: [
            { kind: "text", text: "精读" },
            { kind: "field", field: "model" },
            { kind: "field", field: "year" },
            { kind: "field", field: "type" },
        ],
    };
    state.prefs.attachmentTitleLayout = JSON.stringify(layout);
    const options = PDF2zhHelperFactory.getPDFOptions();
    layout.blocks[0].text = "changed";
    state.prefs.attachmentTitleLayout = JSON.stringify(layout);
    state.files.set("/virtual/test.pdf", [1]);
    await PDF2zhHelperFactory.addAttachment({
        item: { id: 1, libraryID: 1, isAttachment: () => false },
        filePath: "/virtual/test.pdf",
        options,
        model: "task-model",
        service: "openai",
        outputMode: "dual",
        metadata: { title: "Paper", fullTitle: "Paper", author: "", year: "" },
    });
    assert.equal(state.imports[0].title, "精读 · task-model · 双语对照");
});
