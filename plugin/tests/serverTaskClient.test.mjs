import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { URL } from "node:url";

const compiled = ts
    .transpileModule(
        fs.readFileSync(
            new URL("../src/modules/serverTaskClient.ts", import.meta.url),
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
const { ServerTaskClient } = await import(
    `data:text/javascript;base64,${Buffer.from(
        "const PDF2zhHelperFactory={retryOperation:operation=>operation()};\n" +
            "const prepareApiForServer=api=>({api});\n" +
            compiled,
    ).toString("base64")}`
);

test("task lists preserve the snapshot watermark and support older servers", async () => {
    for (const metadata of [
        {},
        { serverInstanceId: "instance", revision: 8 },
    ]) {
        const tasks = [
            {
                taskId: "one",
                revision: 6,
                qualitySummary: {
                    selected: 3,
                    checked: 2,
                    passed: 1,
                    corrected: 1,
                    unchecked: 1,
                    failed: 0,
                    notSelected: 17,
                    requestsUsed: 3,
                    requestLimit: 8,
                    paragraphLimit: 20,
                },
            },
        ];
        globalThis.fetch = async () => ({
            ok: true,
            json: async () => ({ status: "ok", tasks, ...metadata }),
        });
        const response = await ServerTaskClient.listTasks("http://localhost");
        assert.deepEqual(response.tasks, tasks);
        assert.equal(response.serverInstanceId, metadata.serverInstanceId);
        assert.equal(response.revision, metadata.revision);
    }
});

test("supported servers receive glossary and review flags unchanged after one capability check", async () => {
    const calls = [];
    const request = {
        glossaryEntries: [
            { source: "camera", target: "相机", tgt_lng: "zh-CN" },
        ],
        semanticReview: true,
        llm_api: {
            apiProtocol: "responses",
            requestOptions: { temperature: 0 },
        },
    };
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return {
            ok: true,
            json: async () =>
                url.endsWith("/health")
                    ? {
                          capabilities: {
                              glossaryEntries: true,
                              semanticReview: true,
                          },
                      }
                    : { task: { taskId: "created" } },
        };
    };
    const task = await ServerTaskClient.createTask("http://localhost", request);
    assert.equal(task.taskId, "created");
    assert.deepEqual(
        calls.map(({ url }) => url),
        ["http://localhost/health", "http://localhost/tasks"],
    );
    assert.deepEqual(JSON.parse(calls[1].options.body), request);
});

test("missing or false server capabilities block enabled features before task POST", async () => {
    for (const [request, capabilities, error] of [
        [
            {
                glossaryEntries: [
                    { source: "camera", target: "相机", tgt_lng: "" },
                ],
            },
            undefined,
            /术语表.*不可用.*已保留术语表/,
        ],
        [
            { semanticReview: true },
            { semanticReview: false },
            /定向校对.*不可用.*关闭/,
        ],
        [
            { semanticReview: true },
            { semanticReview: "true" },
            /定向校对.*不可用/,
        ],
    ]) {
        const calls = [];
        globalThis.fetch = async (url) => {
            calls.push(url);
            return { ok: true, json: async () => ({ capabilities }) };
        };
        await assert.rejects(
            ServerTaskClient.createTask("http://localhost", request),
            error,
        );
        assert.deepEqual(calls, ["http://localhost/health"]);
    }
});

test("older servers still accept ordinary translation when optional features are disabled", async () => {
    const request = { glossaryEntries: [], semanticReview: false };
    globalThis.fetch = async (url, options) => {
        assert.equal(url, "http://localhost/tasks");
        assert.deepEqual(JSON.parse(options.body), request);
        return { ok: true, json: async () => ({ task: { taskId: "legacy" } }) };
    };
    assert.equal(
        (await ServerTaskClient.createTask("http://localhost", request)).taskId,
        "legacy",
    );
});

test("a malformed task list is rejected instead of clearing local tasks", async () => {
    for (const payload of [{}, { tasks: null }, { tasks: "invalid" }]) {
        globalThis.fetch = async () => ({
            ok: true,
            json: async () => payload,
        });
        await assert.rejects(
            ServerTaskClient.listTasks("http://localhost"),
            /任务列表格式/,
        );
    }
});

test("delete returns the deletion version for the same merge path as SSE", async () => {
    globalThis.fetch = async (url, options) => {
        assert.equal(url, "http://localhost/tasks/one");
        assert.equal(options.method, "DELETE");
        return {
            ok: true,
            json: async () => ({
                status: "ok",
                serverInstanceId: "instance",
                revision: 9,
                task: { taskId: "one" },
            }),
        };
    };
    assert.deepEqual(
        await ServerTaskClient.deleteTask("http://localhost", "one"),
        {
            type: "deleted",
            taskId: "one",
            serverInstanceId: "instance",
            revision: 9,
        },
    );
});
