import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const state = {
    downloads: [],
    imports: [],
    failMode: null,
    onDownload: null,
    onImport: null,
    notifications: [],
    updates: [],
};
globalThis.Zotero = { Items: { get: () => ({ id: 1 }) } };
globalThis.__importTest = {
    ServerTaskClient: {
        fetchResult: async (_server, _id, mode) => {
            state.downloads.push(mode);
            await state.onDownload?.(mode);
            if (mode === state.failMode) throw new Error("download failed");
            return new Uint8Array([1]);
        },
    },
    PDF2zhHelperFactory: {
        getServerConfig: () => ({}),
        handleOutputResponse: async (output) => {
            state.imports.push(output.outputMode);
            await state.onImport?.(output.outputMode);
        },
    },
};
const source = fs
    .readFileSync(
        new URL("../src/modules/zoteroTaskImporter.ts", import.meta.url),
        "utf8",
    )
    .replace(/^import .*;$/gm, "");
const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { ZoteroTaskImporter } = await import(
    `data:text/javascript;base64,${Buffer.from(
        "const {ServerTaskClient, PDF2zhHelperFactory} = globalThis.__importTest;\n" +
            compiled,
    ).toString("base64")}`
);

function fixture(status = "completed", failed = 0) {
    state.downloads = [];
    state.imports = [];
    state.failMode = null;
    state.onDownload = null;
    state.onImport = null;
    state.notifications = [];
    state.updates = [];
    const task = {
        taskId: "one",
        itemID: 1,
        status,
        importState: "pending",
        attempt: 2,
        outputModes: ["mono", "dual"],
        resultFiles: {},
        translationSummary: { failed, pending: 0 },
    };
    let currentTask = task;
    const importer = new ZoteroTaskImporter({
        getTask: () => currentTask,
        updateTask: (_id, patch) => {
            state.updates.push(patch);
            if (currentTask) Object.assign(currentTask, patch);
        },
        onTaskImported: (id) => state.notifications.push(id),
    });
    return {
        task,
        importer,
        replaceTask: (replacement) => {
            currentTask = replacement;
        },
    };
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => {
        resolve = yes;
        reject = no;
    });
    return { promise, resolve, reject };
}

test("incomplete or failed paragraph tasks never download or import", async () => {
    for (const [status, failed] of [
        ["incomplete", 1],
        ["completed", 1],
    ]) {
        const { importer } = fixture(status, failed);
        await importer.importTaskOutputs("one");
        assert.deepEqual(state.downloads, []);
        assert.deepEqual(state.imports, []);
    }
});

test("repeated completion events import each repaired output once", async () => {
    const { task, importer } = fixture();
    await Promise.all([
        importer.importTaskOutputs("one"),
        importer.importTaskOutputs("one"),
    ]);
    await importer.importTaskOutputs("one");
    assert.deepEqual(state.imports, ["mono", "dual"]);
    assert.equal(task.importState, "imported");
    assert.deepEqual(state.notifications, ["one"]);
});

test("import retry preserves output already imported before a download failure", async () => {
    const { task, importer } = fixture();
    state.failMode = "dual";
    await importer.importTaskOutputs("one");
    assert.equal(task.importState, "failed");
    task.importState = "pending";
    state.failMode = null;
    await importer.importTaskOutputs("one");
    assert.deepEqual(state.imports, ["mono", "dual"]);
    assert.equal(task.importState, "imported");
});

for (const phase of ["download", "import"]) {
    for (const change of ["new attempt", "deleted", "incomplete"]) {
        for (const outcome of ["success", "failure"]) {
            test(`${phase} ${outcome} after task is ${change} cannot update or notify it`, async () => {
                const { task, importer, replaceTask } = fixture();
                const started = deferred();
                const finish = deferred();
                const hook = async () => {
                    started.resolve();
                    await finish.promise;
                };
                if (phase === "download") state.onDownload = hook;
                else state.onImport = hook;

                const importing = importer.importTaskOutputs("one");
                await started.promise;
                const replacement =
                    change === "deleted"
                        ? undefined
                        : {
                              ...task,
                              attempt:
                                  change === "new attempt" ? 3 : task.attempt,
                              status:
                                  change === "incomplete"
                                      ? "incomplete"
                                      : "completed",
                              importState: "pending",
                              importedOutputs: [],
                              importError: "current attempt marker",
                          };
                replaceTask(replacement);
                const expectedTask = globalThis.structuredClone(replacement);
                const updatesBefore = state.updates.length;
                if (outcome === "success") finish.resolve();
                else finish.reject(new Error("old operation failed"));
                await importing;

                assert.deepEqual(replacement, expectedTask);
                assert.equal(state.updates.length, updatesBefore);
                assert.deepEqual(state.notifications, []);
                assert.deepEqual(state.downloads, ["mono"]);
                assert.deepEqual(
                    state.imports,
                    phase === "download" ? [] : ["mono"],
                );
            });
        }
    }
}
