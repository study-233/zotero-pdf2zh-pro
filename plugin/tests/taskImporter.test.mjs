import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const state = { downloads: [], imports: [], failMode: null };
globalThis.Zotero = { Items: { get: () => ({ id: 1 }) } };
globalThis.__importTest = {
    ServerTaskClient: {
        fetchResult: async (_server, _id, mode) => {
            state.downloads.push(mode);
            if (mode === state.failMode) throw new Error("download failed");
            return new Uint8Array([1]);
        },
    },
    PDF2zhHelperFactory: {
        getServerConfig: () => ({}),
        handleOutputResponse: async (output) =>
            state.imports.push(output.outputMode),
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
    const importer = new ZoteroTaskImporter({
        getTask: () => task,
        updateTask: (_id, patch) => Object.assign(task, patch),
        onTaskImported: () => {},
    });
    return { task, importer };
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
