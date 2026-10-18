import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const compiled = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/taskOutputStore.ts", import.meta.url),
        "utf8",
    ),
    {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    },
).outputText;
function loadStore() {
    const exports = {};
    new Function("exports", compiled)(exports);
    return exports;
}

function fixture(t) {
    const state = {
        items: new Map(),
        receipts: new Map(),
        files: new Set(),
        removed: [],
        indexed: [],
        failReceipt: false,
        afterCommit: false,
        onCopy: null,
        sequence: 0,
        transactions: 0,
    };
    let transactionTail = Promise.resolve();
    const zotero = {
        DB: {
            async executeTransaction(callback) {
                const pending = transactionTail.then(async () => {
                    const items = new Map(state.items),
                        receipts = new Map(state.receipts);
                    state.transactions++;
                    let result;
                    try {
                        result = await callback();
                    } catch (error) {
                        state.items = items;
                        state.receipts = receipts;
                        throw error;
                    }
                    if (state.afterCommit)
                        throw new Error("interrupted after commit");
                    return result;
                });
                transactionTail = pending.catch(() => {});
                return pending;
            },
            async queryAsync(sql, params) {
                if (sql.startsWith("CREATE TABLE")) return;
                assert.match(sql, /^INSERT INTO pdf2zhOutputImports/);
                if (state.failReceipt) throw new Error("receipt write failed");
                const [outputKey, libraryID, itemKey] = params;
                assert.equal(state.receipts.has(outputKey), false);
                state.receipts.set(outputKey, { libraryID, itemKey });
            },
            async rowQueryAsync(_sql, [outputKey]) {
                return state.receipts.get(outputKey) || false;
            },
        },
        Item: class {
            constructor(kind) {
                assert.equal(kind, "attachment");
                this.id = ++state.sequence;
                this.key = `KEY${this.id}`;
            }
            setField(key, value) {
                this[key] = value;
            }
            setCollections(collections) {
                this.collections = collections;
            }
            async save() {
                state.items.set(this.key, this);
            }
            async getFilePathAsync() {
                return this.attachmentPath;
            }
        },
        Items: {
            async getByLibraryAndKeyAsync(libraryID, key) {
                const item = state.items.get(key);
                return item?.libraryID === libraryID ? item : false;
            },
        },
        Attachments: {
            LINK_MODE_IMPORTED_FILE: 0,
            async createDirectoryForItem(item) {
                return `/storage/${item.key}`;
            },
            async importFromFile() {
                throw new Error("must not nest the public import transaction");
            },
        },
        File: {
            getValidFileName: (name) => name,
            async copyToUnique(_source, destination) {
                state.files.add(destination);
                await state.onCopy?.();
                return { path: destination };
            },
            async setNormalFilePermissions() {},
        },
        Fulltext: {
            async indexItems(ids) {
                state.indexed.push(...ids);
            },
        },
    };
    const globals = {
        Zotero: zotero,
        PathUtils: { join: path.posix.join, filename: path.posix.basename },
        IOUtils: {
            async exists(file) {
                return state.files.has(file);
            },
            async remove(directory) {
                state.removed.push(directory);
                for (const file of state.files)
                    if (file.startsWith(directory + "/"))
                        state.files.delete(file);
            },
        },
        ztoolkit: { log() {} },
    };
    for (const [key, value] of Object.entries(globals)) {
        const original = globalThis[key];
        globalThis[key] = value;
        t.after(() => {
            globalThis[key] = original;
        });
    }
    const options = {
        outputKey: "same-output",
        file: "/temp/paper.pdf",
        libraryID: 7,
        parentItemID: 12,
        title: "Translated paper",
        isCurrent: () => true,
    };
    return { state, options, ...loadStore() };
}

test("concurrent imports and a reloaded module reconcile one durable attachment", async (t) => {
    const { state, options, importTaskOutput } = fixture(t);
    const [first, second] = await Promise.all([
        importTaskOutput(options),
        importTaskOutput(options),
    ]);
    assert.equal(first.key, second.key);
    first.annotation = "preserved annotation";
    const restarted = loadStore();
    assert.equal(
        (await restarted.findTaskOutput(options.outputKey)).key,
        first.key,
    );
    assert.equal(
        (
            await restarted.importTaskOutput({
                ...options,
                title: "new naming preference",
            })
        ).title,
        "Translated paper",
    );
    assert.equal(first.annotation, "preserved annotation");
    assert.equal(first.libraryID, 7);
    assert.equal(first.parentID, 12);
    assert.equal(state.items.size, 1);
    assert.equal(state.receipts.size, 1);
    assert.equal(state.files.size, 1);
    assert.equal(state.indexed.length, 1);
});

test("receipt failure rolls back the attachment and cleans only its own files", async (t) => {
    const { state, options, importTaskOutput } = fixture(t);
    state.files.add("/storage/OLD/annotated.pdf");
    state.failReceipt = true;
    await assert.rejects(importTaskOutput(options), /receipt write failed/);
    assert.equal(state.items.size, 0);
    assert.equal(state.receipts.size, 0);
    assert.deepEqual([...state.files], ["/storage/OLD/annotated.pdf"]);
    state.failReceipt = false;
    await importTaskOutput(options);
    assert.equal(state.items.size, 1);
    assert.equal(state.receipts.size, 1);
});

test("copy failure and invalidation roll back without a completed receipt", async (t) => {
    const { state, options, importTaskOutput } = fixture(t);
    state.onCopy = () => {
        throw new Error("disk full");
    };
    await assert.rejects(importTaskOutput(options), /disk full/);
    assert.equal(state.items.size, 0);
    assert.equal(state.files.size, 0);
    let current = true;
    state.onCopy = () => {
        current = false;
    };
    await assert.rejects(
        importTaskOutput({ ...options, isCurrent: () => current }),
        { name: "AbortError" },
    );
    assert.equal(state.items.size, 0);
    assert.equal(state.files.size, 0);
    assert.equal(state.receipts.size, 0);
});

test("post-commit interruption preserves the file and retry uses its receipt", async (t) => {
    const { state, options, importTaskOutput } = fixture(t);
    state.afterCommit = true;
    await assert.rejects(importTaskOutput(options), /after commit/);
    assert.equal(state.items.size, 1);
    assert.equal(state.files.size, 1);
    assert.deepEqual(state.removed, []);
    state.afterCommit = false;
    const result = await loadStore().importTaskOutput(options);
    assert.equal(result.key, "KEY1");
    assert.equal(state.items.size, 1);
});

test("deleted or unavailable attachments require recovery instead of silent duplication", async (t) => {
    const { state, options, importTaskOutput } = fixture(t);
    const item = await importTaskOutput(options);
    item.deleted = true;
    await assert.rejects(loadStore().importTaskOutput(options), /附件已删除/);
    item.deleted = false;
    state.files.clear();
    await assert.rejects(loadStore().importTaskOutput(options), /文件不可用/);
    assert.equal(state.items.size, 1);
    assert.equal(state.sequence, 1);
});

test("distinct outputs remain distinct and standalone collections survive", async (t) => {
    const { state, options, importTaskOutput } = fixture(t);
    const first = await importTaskOutput({
        ...options,
        parentItemID: undefined,
        collections: [9],
    });
    const second = await importTaskOutput({
        ...options,
        outputKey: "second-attempt",
    });
    assert.deepEqual(first.collections, [9]);
    assert.equal(first.parentID, undefined);
    assert.notEqual(first.key, second.key);
    assert.equal(state.items.size, 2);
});
