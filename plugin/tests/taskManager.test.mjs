import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { URL } from "node:url";

const compile = (name) =>
    ts
        .transpileModule(
            fs.readFileSync(
                new URL(`../src/modules/${name}.ts`, import.meta.url),
                "utf8",
            ),
            {
                compilerOptions: {
                    module: ts.ModuleKind.ESNext,
                    target: ts.ScriptTarget.ES2022,
                },
            },
        )
        .outputText.replace(/^import[\s\S]*?;\r?\n/gm, "");
const moduleFrom = (source) =>
    import(
        `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
    );
const { TaskEventStream } = await moduleFrom(compile("taskEventStream"));
let sequence = 0;
const serverUrl = "http://localhost:8890";
const deferred = () => {
    let resolve;
    const promise = new Promise((done) => {
        resolve = done;
    });
    return { promise, resolve };
};
const snapshot = (changes = {}) => ({
    taskId: "one",
    fileName: "paper.pdf",
    service: "openai",
    outputModes: ["dual"],
    status: "running",
    attempt: 1,
    resultFiles: {},
    stage: null,
    stageCurrent: 0,
    stageTotal: 0,
    stageProgress: 0,
    overallProgress: 0,
    error: null,
    canCancel: true,
    cancelRequested: false,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-01T00:00:00Z",
    ...changes,
});
const local = (changes = {}) => ({
    ...snapshot(),
    itemID: 7,
    serverUrl,
    source: "local",
    importState: "pending",
    ...changes,
});

const versioned = (revision, changes = {}) =>
    snapshot({ serverInstanceId: "server-a", revision, ...changes });
const taskEvent = (task) => ({
    type: "task",
    task,
    serverInstanceId: task.serverInstanceId,
    revision: task.revision,
});
const taskList = (revision, tasks, serverInstanceId = "server-a") => ({
    serverInstanceId,
    revision,
    tasks,
});
const qualitySummary = (changes = {}) => ({
    selected: 10,
    checked: 6,
    passed: 4,
    corrected: 1,
    unchecked: 4,
    failed: 1,
    notSelected: 30,
    requestsUsed: 8,
    requestLimit: 8,
    paragraphLimit: 20,
    ...changes,
});

async function fixture(saved = []) {
    const prefs = new Map([
        ["extensions.test.taskBindings", JSON.stringify(saved)],
    ]);
    const writes = [];
    const sources = [];
    const imports = [];
    globalThis.EventSource = class {
        constructor(url) {
            this.url = url;
            sources.push(this);
        }
        close() {
            this.closed = true;
        }
    };
    const dialog = { addEventListener() {}, focus() {}, closed: false };
    globalThis.Zotero = {
        Prefs: {
            get: (key) => prefs.get(key),
            set: (key, value) => {
                prefs.set(key, value);
                writes.push(JSON.parse(value));
            },
        },
        Promise: { defer: () => ({}) },
        getMainWindow: () => ({ openDialog: () => dialog }),
    };
    globalThis.ztoolkit = { log() {} };
    const client = {
        listTasks: async () => ({ tasks: [] }),
        createTask: async () => snapshot(),
    };
    globalThis.__taskManagerTests = {
        config: { prefsPrefix: "extensions.test", addonRef: "test" },
        getString: (s) => s,
        getPref: (key) => (key === "new_serverip" ? serverUrl : false),
        PDF2zhHelperFactory: {
            prepareFileData: async () => ({}),
            buildTaskRequestBody: () => ({}),
            isTrue: () => false,
        },
        ServerTaskClient: client,
        TaskEventStream,
        ZoteroTaskImporter: class {
            async importTaskOutputs(id) {
                imports.push(id);
            }
        },
    };
    const { PDF2zhTaskManager: manager } = await moduleFrom(
        "const {config,getString,getPref,PDF2zhHelperFactory,ServerTaskClient,TaskEventStream,ZoteroTaskImporter} = globalThis.__taskManagerTests;\n" +
            compile("pdf2zhTaskManager") +
            `\n// instance ${sequence++}`,
    );
    return { manager, prefs, writes, sources, client, imports };
}

test("opening the task window restores bindings before stream state callbacks", async () => {
    const saved = local({
        importState: "importing",
        importedOutputs: ["1:mono"],
    });
    const { manager, writes, sources } = await fixture([saved]);
    manager.openWindow();
    assert.equal(manager.getTasks()[0]?.itemID, 7);
    assert.equal(manager.getTasks()[0]?.importState, "pending");
    assert.deepEqual(manager.getTasks()[0]?.importedOutputs, ["1:mono"]);
    assert.equal(sources.length, 1);
    sources[0].onerror();
    assert.deepEqual(writes, [], "connection state must not persist bindings");
});

test("bindings cannot be saved before they have been loaded", async () => {
    const { manager, writes } = await fixture([local()]);
    manager.saveLocalTasks();
    assert.deepEqual(writes, []);
});

test("a delayed list cannot erase a newly submitted task or its attachment binding", async () => {
    const { manager, client } = await fixture();
    const response = deferred();
    client.listTasks = () => response.promise;
    const refresh = manager.refreshTasks();
    await manager.submitTask({ id: 7 }, { serverUrl });
    response.resolve({ tasks: [] });
    await refresh;
    assert.equal(manager.getTasks()[0]?.itemID, 7);
    manager.handleServerTaskEvent(serverUrl, {
        type: "task",
        task: snapshot({
            status: "completed",
            updatedAt: "2026-01-01T00:01:00Z",
        }),
    });
    assert.equal(manager.getTasks()[0]?.source, "local");
    assert.equal(manager.getTasks()[0]?.itemID, 7);
});

test("a list cannot resurrect a task deleted while the request was pending", async () => {
    const { manager, client } = await fixture([local()]);
    const response = deferred();
    client.listTasks = () => response.promise;
    const refresh = manager.refreshTasks();
    manager.handleServerTaskEvent(serverUrl, {
        type: "deleted",
        taskId: "one",
    });
    response.resolve({ tasks: [snapshot()] });
    await refresh;
    assert.deepEqual(manager.getTasks(), []);
});

test("older attempts and older snapshots cannot roll back tasks or import checkpoints", async () => {
    const { manager } = await fixture([
        local({
            attempt: 2,
            status: "completed",
            importState: "imported",
            importedOutputs: ["2:dual"],
            updatedAt: "2026-01-01T00:02:00Z",
        }),
    ]);
    manager.openWindow();
    for (const stale of [
        snapshot({ status: "failed" }),
        snapshot({ attempt: 2 }),
    ]) {
        manager.handleServerTaskEvent(serverUrl, { type: "task", task: stale });
    }
    const task = manager.getTasks()[0];
    assert.equal(task.attempt, 2);
    assert.equal(task.status, "completed");
    assert.equal(task.importState, "imported");
    assert.deepEqual(task.importedOutputs, ["2:dual"]);
});

test("a delayed creation response still attaches a local binding to a newer completion", async () => {
    const { manager, client } = await fixture();
    const response = deferred();
    client.createTask = () => response.promise;
    const submission = manager.submitTask({ id: 7 }, { serverUrl });
    await Promise.resolve();
    manager.handleServerTaskEvent(serverUrl, {
        type: "task",
        task: snapshot({
            status: "completed",
            updatedAt: "2026-01-01T00:02:00Z",
        }),
    });
    response.resolve(snapshot());
    await submission;
    const task = manager.getTasks()[0];
    assert.equal(task.status, "completed");
    assert.equal(task.itemID, 7);
    assert.equal(task.importState, "pending");
});

test("versions are compared per task, preserving out-of-order events for different tasks", async () => {
    const { manager } = await fixture();
    manager.openWindow();
    manager.handleServerTaskEvent(
        serverUrl,
        taskEvent(versioned(10, { status: "completed" })),
    );
    manager.handleServerTaskEvent(
        serverUrl,
        taskEvent(versioned(2, { taskId: "two" })),
    );
    manager.handleServerTaskEvent(
        serverUrl,
        taskEvent(versioned(9, { status: "running" })),
    );
    assert.equal(manager.tasks.get("one").status, "completed");
    assert.equal(manager.tasks.get("two").revision, 2);
});

test("a versioned list preserves a newer task and removes absent states covered by its watermark", async () => {
    const { manager, client } = await fixture();
    manager.openWindow();
    manager.handleServerTaskEvent(serverUrl, taskEvent(versioned(1)));
    const response = deferred();
    client.listTasks = () => response.promise;
    const refresh = manager.refreshTasks();
    manager.handleServerTaskEvent(serverUrl, taskEvent(versioned(2)));
    manager.handleServerTaskEvent(
        serverUrl,
        taskEvent(versioned(4, { taskId: "new" })),
    );
    response.resolve(taskList(3, []));
    await refresh;
    assert.equal(manager.tasks.has("one"), false);
    assert.equal(manager.tasks.has("new"), true);
});

test("deletion versions and reconciled watermarks prevent stale resurrection", async () => {
    const { manager, client } = await fixture([local()]);
    manager.openWindow();
    manager.handleServerTaskEvent(serverUrl, taskEvent(versioned(10)));
    const response = deferred();
    client.listTasks = () => response.promise;
    const refresh = manager.refreshTasks();
    manager.handleServerTaskEvent(serverUrl, {
        type: "deleted",
        taskId: "one",
        serverInstanceId: "server-a",
        revision: 11,
    });
    response.resolve(taskList(10, [versioned(10)]));
    await refresh;
    manager.handleServerTaskEvent(serverUrl, taskEvent(versioned(10)));
    assert.equal(manager.tasks.has("one"), false);
    client.listTasks = async () => taskList(12, []);
    await manager.refreshTasks();
    assert.equal(manager.syncState(serverUrl).deleted.size, 0);
    manager.handleServerTaskEvent(serverUrl, taskEvent(versioned(10)));
    assert.equal(manager.tasks.has("one"), false);
});

test("every stream open reconciles completion and preserves import checkpoints", async () => {
    const { manager, client, sources, imports } = await fixture([local()]);
    let calls = 0;
    client.listTasks = async () => {
        calls += 1;
        return taskList(calls, [versioned(calls, { status: "completed" })]);
    };
    manager.openWindow();
    sources[0].onopen();
    await manager.pollPromise;
    assert.equal(calls, 1);
    assert.deepEqual(imports, ["one"]);
    manager.updateLocalTask("one", {
        importState: "imported",
        importedOutputs: ["1:dual"],
    });
    sources[0].onerror();
    sources[0].onopen();
    await manager.pollPromise;
    assert.equal(calls, 2);
    assert.equal(manager.tasks.get("one").itemID, 7);
    assert.deepEqual(imports, ["one"]);
    assert.deepEqual(manager.tasks.get("one").importedOutputs, ["1:dual"]);
});

test("a restart retires old responses without losing Zotero bindings", async () => {
    const { manager, client } = await fixture([local()]);
    manager.openWindow();
    manager.handleServerTaskEvent(serverUrl, taskEvent(versioned(10)));
    const response = deferred();
    let calls = 0;
    const restarted = versioned(1, {
        serverInstanceId: "server-b",
        status: "incomplete",
    });
    client.listTasks = () =>
        ++calls === 1
            ? response.promise
            : Promise.resolve(taskList(1, [restarted], "server-b"));
    const refresh = manager.refreshTasks();
    manager.handleServerTaskEvent(serverUrl, taskEvent(restarted));
    response.resolve(taskList(12, [versioned(12, { status: "completed" })]));
    await refresh;
    manager.handleServerTaskEvent(
        serverUrl,
        taskEvent(versioned(99, { status: "completed" })),
    );
    assert.equal(calls, 2);
    assert.equal(manager.tasks.get("one").serverInstanceId, "server-b");
    assert.equal(manager.tasks.get("one").status, "incomplete");
    assert.equal(manager.tasks.get("one").itemID, 7);
});

test("resync reconciles the full list, including more than 128 historical tasks", async () => {
    const { manager, client } = await fixture();
    manager.openWindow();
    client.listTasks = async () =>
        taskList(
            130,
            Array.from({ length: 130 }, (_, i) =>
                versioned(i + 1, { taskId: `task-${i}` }),
            ),
        );
    manager.handleServerTaskEvent(serverUrl, {
        type: "resync",
        serverInstanceId: "server-a",
        revision: 130,
    });
    await manager.pollPromise;
    assert.equal(manager.getTasks().length, 130);
});

test("events from a closed EventSource cannot mutate a replacement subscription", async () => {
    const { manager, sources } = await fixture();
    manager.openWindow();
    const old = sources[0];
    manager.eventStream.sync(new Set());
    manager.eventStream.sync(new Set([serverUrl]));
    old.onmessage({ data: JSON.stringify(taskEvent(versioned(99))) });
    assert.equal(manager.getTasks().length, 0);
    sources[1].onmessage({ data: JSON.stringify(taskEvent(versioned(1))) });
    assert.equal(manager.getTasks()[0].revision, 1);
});

test("a late action response from an initially unknown old instance cannot retire a new one", async () => {
    for (const action of [
        "cancelTask",
        "retryTask",
        "repairTask",
        "deleteTask",
    ]) {
        const { manager, client } = await fixture([
            local({ ...versioned(10), status: "failed" }),
        ]);
        manager.openWindow();
        const response = deferred();
        client[action] = () => response.promise;
        const pending = manager[action]("one");
        const restarted = versioned(1, {
            serverInstanceId: "server-b",
            status: "incomplete",
        });
        manager.handleServerTaskEvent(serverUrl, taskEvent(restarted));
        response.resolve(
            action === "deleteTask"
                ? {
                      type: "deleted",
                      taskId: "one",
                      serverInstanceId: "server-a",
                      revision: 11,
                  }
                : versioned(11),
        );
        await pending;
        assert.equal(
            manager.syncState(serverUrl).instanceId,
            "server-b",
            action,
        );
        assert.equal(manager.tasks.get("one").status, "incomplete", action);
    }
});

test("a retired creation response binds the restored task without replacing its state", async () => {
    const { manager, client } = await fixture();
    manager.openWindow();
    const response = deferred();
    const started = deferred();
    client.createTask = () => {
        started.resolve();
        return response.promise;
    };
    const submission = manager.submitTask({ id: 7 }, { serverUrl });
    await started.promise;
    manager.handleServerTaskEvent(serverUrl, taskEvent(versioned(1)));
    manager.handleServerTaskEvent(
        serverUrl,
        taskEvent(
            versioned(1, {
                serverInstanceId: "server-b",
                status: "incomplete",
            }),
        ),
    );
    response.resolve(versioned(1));
    await submission;
    assert.equal(manager.tasks.get("one").serverInstanceId, "server-b");
    assert.equal(manager.tasks.get("one").status, "incomplete");
    assert.equal(manager.tasks.get("one").itemID, 7);
    assert.equal(manager.tasks.get("one").importState, "pending");
});

test("HTTP and SSE quality summaries survive persistence without import state changes", async () => {
    const { manager, client, sources, writes } = await fixture([local()]);
    manager.openWindow();
    const initial = qualitySummary();
    client.listTasks = async () =>
        taskList(1, [versioned(1, { qualitySummary: initial })]);
    await manager.refreshTasks();
    assert.deepEqual(manager.getTasks()[0].qualitySummary, initial);
    assert.deepEqual(writes.at(-1)[0].qualitySummary, initial);
    const beforeQualityChange = writes.length;
    const updated = qualitySummary({ checked: 7, corrected: 2, unchecked: 3 });
    sources[0].onmessage({
        data: JSON.stringify(
            taskEvent(versioned(2, { qualitySummary: updated })),
        ),
    });
    assert.deepEqual(manager.getTasks()[0].qualitySummary, updated);
    assert.equal(writes.length, beforeQualityChange + 1);
    assert.deepEqual(writes.at(-1)[0].qualitySummary, updated);
    const restored = await fixture(writes.at(-1));
    restored.manager.openWindow();
    assert.deepEqual(restored.manager.getTasks()[0].qualitySummary, updated);
});

test("a new attempt or null summary clears previous quality conclusions", async () => {
    for (const incoming of [undefined, null]) {
        const { manager, sources, writes } = await fixture([
            local({ qualitySummary: qualitySummary(), ...versioned(1) }),
        ]);
        manager.openWindow();
        sources[0].onmessage({
            data: JSON.stringify(
                taskEvent(
                    versioned(2, {
                        attempt: 2,
                        qualitySummary: incoming,
                    }),
                ),
            ),
        });
        assert.equal(manager.getTasks()[0].qualitySummary, incoming);
        assert.equal(writes.at(-1)[0].qualitySummary, incoming);
    }
});

test("quality review uncertainty retains completed PDF import while confirmed failures stay incomplete", async () => {
    for (const [status, summary, shouldImport] of [
        [
            "completed",
            qualitySummary({
                checked: 0,
                passed: 0,
                corrected: 0,
                failed: 0,
                unchecked: 10,
            }),
            true,
        ],
        ["incomplete", qualitySummary(), false],
    ]) {
        const { manager, sources, imports } = await fixture([local()]);
        manager.openWindow();
        sources[0].onmessage({
            data: JSON.stringify(
                taskEvent(
                    versioned(1, {
                        status,
                        qualitySummary: summary,
                        resultFiles: { dual: "paper-dual.pdf" },
                    }),
                ),
            ),
        });
        assert.equal(manager.getTasks()[0].status, status);
        assert.deepEqual(imports, shouldImport ? ["one"] : []);
    }
});

test("extended metrics keep grouped diagnostics and timings through HTTP, SSE and saved bindings", async () => {
    const { manager, client, sources, writes } = await fixture([local()]);
    const metrics = {
        requests: {
            attempts: 3,
            byKind: {
                translation: {
                    attempts: 1,
                    statusCodes: { 200: 1 },
                    batchSizes: { 4: 1 },
                },
                review: {
                    attempts: 1,
                    protocols: { responses: 1 },
                    finishReasons: { stop: 1 },
                    visibleOutputChars: 20,
                },
                initialization: { attempts: 1, errorTypes: {} },
            },
        },
        tokens: {
            input: 100,
            output: 40,
            total: 140,
            reasoning: 20,
            reasoningAvailability: "partial",
            byKind: {
                review: { input: 50, output: 20, total: 70, reasoning: null },
            },
        },
        stageDurations: { "Translate Paragraphs": 1.5, "Semantic Review": 0.8 },
    };
    manager.openWindow();
    client.listTasks = async () => taskList(1, [versioned(1, { metrics })]);
    await manager.refreshTasks();
    assert.deepEqual(manager.getTasks()[0].metrics, metrics);
    const updated = {
        ...metrics,
        stageDurations: { ...metrics.stageDurations, "PDF Creation": 2 },
    };
    sources[0].onmessage({
        data: JSON.stringify(taskEvent(versioned(2, { metrics: updated }))),
    });
    assert.deepEqual(manager.getTasks()[0].metrics, updated);
    manager.updateLocalTask("one", { importState: "imported" });
    assert.deepEqual(writes.at(-1)[0].metrics, updated);
    const restored = await fixture(writes.at(-1));
    restored.manager.openWindow();
    assert.deepEqual(restored.manager.getTasks()[0].metrics, updated);
});
