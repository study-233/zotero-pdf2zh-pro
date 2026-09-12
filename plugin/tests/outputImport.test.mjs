import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

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
    getPref: (key) =>
        key === "openAfterTranslate" ? state.openAfterProcess : false,
};

const source = fs
    .readFileSync(
        new URL("../src/modules/pdf2zhHelper.ts", import.meta.url),
        "utf8",
    )
    .replace(/^import .*;$/gm, "");
const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { PDF2zhHelperFactory } = await import(
    `data:text/javascript;base64,${Buffer.from(
        "const {getPref} = globalThis.__outputImportTest;\n" + compiled,
    ).toString("base64")}`
);

function reset() {
    Object.assign(state, {
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

function deferred() {
    let resolve;
    const promise = new Promise((yes) => {
        resolve = yes;
    });
    return { promise, resolve };
}

function importOutput(itemID, content, isCurrent = () => true) {
    return PDF2zhHelperFactory.handleOutputResponse(
        {
            fileName: "paper.zh-CN.dual.pdf",
            outputMode: "dual",
            bytes: new Uint8Array([content]),
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
