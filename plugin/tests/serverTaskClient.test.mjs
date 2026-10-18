import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { URL } from "node:url";
const { File, Response, FormData } = globalThis;

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
        "const diagnosticFetch = (...args) => fetch(...args);\n" +
            "const PDF2zhHelperFactory={retryOperation:operation=>operation(),blobToBase64:async blob=>'data:application/pdf;base64,'+Buffer.from(await blob.arrayBuffer()).toString('base64')};\n" +
            "const prepareApiForServer=api=>({api});\n" +
            compiled,
    ).toString("base64")}`
);

test("lost acceptance replies retry the exact body only with advertised idempotency", async () => {
    for (const supported of [true, false]) {
        const bodies = [];
        globalThis.fetch = async (url, options) => {
            if (url.endsWith("/health"))
                return {
                    ok: true,
                    json: async () => ({
                        capabilities: { taskSubmissionIdempotency: supported },
                    }),
                };
            bodies.push(options.body);
            if (bodies.length === 1)
                throw new TypeError("connection lost after acceptance");
            return {
                ok: true,
                json: async () => ({ task: { taskId: "same-task" } }),
            };
        };
        const request = {
            requestId: "submission-12345678",
            fileContent: "pdf",
        };
        if (supported) {
            assert.equal(
                (await ServerTaskClient.createTask("http://localhost", request))
                    .taskId,
                "same-task",
            );
            assert.equal(bodies.length, 2);
            assert.equal(bodies[0], bodies[1]);
        } else {
            await assert.rejects(
                ServerTaskClient.createTask("http://localhost", request),
                /connection lost/,
            );
            assert.equal(bodies.length, 1);
        }
    }
});

test("malformed success replies are bounded and explicit HTTP conflicts are not retried", async () => {
    for (const code of [200, 409]) {
        let posts = 0;
        globalThis.fetch = async (url) => {
            if (url.endsWith("/health"))
                return {
                    ok: true,
                    json: async () => ({
                        capabilities: { taskSubmissionIdempotency: true },
                    }),
                };
            posts++;
            return {
                ok: code === 200,
                status: code,
                json: async () => {
                    if (code === 200) throw new SyntaxError("partial response");
                    return { message: "submission conflict" };
                },
            };
        };
        await assert.rejects(
            ServerTaskClient.createTask("http://localhost", {
                requestId: "submission-12345678",
            }),
            (error) =>
                code === 200
                    ? error instanceof SyntaxError
                    : error.submissionConflict === true,
        );
        assert.equal(posts, code === 200 ? 2 : 1);
    }
});

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
        [
            {
                glossaryPacks: [
                    { id: "medicine", version: "1", sha256: "a".repeat(64) },
                ],
            },
            { glossaryEntries: true },
            /下载词库.*升级服务端/,
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

test("Codex task submission checks health even without HTTP options", async () => {
    const calls = [];
    const request = {
        service: "codex",
        llm_api: {
            service: "codex",
            model: "gpt-6-luna",
            cliPath: "/opt/codex",
            reasoningEffort: "low",
        },
    };
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return {
            ok: true,
            json: async () =>
                url.endsWith("/health")
                    ? { capabilities: { codexCli: true } }
                    : { task: { taskId: "codex-task" } },
        };
    };
    assert.equal(
        (await ServerTaskClient.createTask("http://localhost", request)).taskId,
        "codex-task",
    );
    assert.deepEqual(
        calls.map((call) => call.url),
        ["http://localhost/health", "http://localhost/tasks"],
    );
    assert.deepEqual(JSON.parse(calls[1].options.body), request);
});

test("binary uploads preserve bytes, metadata and identity across an uncertain replay", async () => {
    const file = new File(
        [new Uint8Array([37, 80, 68, 70, 0, 255])],
        "论文.pdf",
        { type: "application/pdf" },
    );
    const bodies = [];
    globalThis.fetch = async (url, options) => {
        if (url.endsWith("/health"))
            return new Response(
                JSON.stringify({
                    capabilities: {
                        binaryTaskUpload: true,
                        taskSubmissionIdempotency: true,
                    },
                }),
            );
        assert.equal(
            options.headers,
            undefined,
            "browser owns the multipart boundary",
        );
        assert.ok(options.body instanceof FormData);
        bodies.push(options.body);
        if (bodies.length === 1) throw new TypeError("lost acceptance");
        return new Response(JSON.stringify({ task: { taskId: "one" } }));
    };
    const request = {
        fileName: file.name,
        targetLang: "zh-CN",
        requestId: "binary-request-0001",
    };
    assert.equal(
        (
            await ServerTaskClient.createTask(
                "http://local",
                request,
                undefined,
                file,
            )
        ).taskId,
        "one",
    );
    assert.deepEqual(JSON.parse(bodies[0].get("metadata")), request);
    assert.deepEqual(
        new Uint8Array(await bodies[0].get("file").arrayBuffer()),
        new Uint8Array(await file.arrayBuffer()),
    );
    assert.equal(bodies[0], bodies[1]);
});

test("paged task queries retain reconciliation metadata and fall back only for old endpoints", async () => {
    const page = {
        tasks: [],
        scope: "dashboard",
        visibleTaskIds: [],
        missingTaskIds: ["deleted"],
        pagination: {
            nextCursor: "next",
            hasMore: true,
            totalHistory: 40,
            limit: 30,
        },
    };
    let requests = [];
    globalThis.fetch = async (url, options) => {
        requests.push([url, options]);
        return new Response(JSON.stringify(page));
    };
    const result = await ServerTaskClient.listTasks("http://local", {
        cursor: "first",
        tracked: ["deleted"],
    });
    assert.deepEqual(result.pagination, page.pagination);
    assert.deepEqual(result.missingTaskIds, ["deleted"]);
    assert.deepEqual(JSON.parse(requests[0][1].body), {
        cursor: "first",
        tracked: ["deleted"],
        limit: 30,
    });
    requests = [];
    globalThis.fetch = async (url) => {
        requests.push(url);
        return url.endsWith("/query")
            ? new Response("", { status: 404 })
            : new Response('{"tasks":[]}');
    };
    assert.deepEqual(
        (await ServerTaskClient.listTasks("http://local", { tracked: [] }))
            .tasks,
        [],
    );
    assert.deepEqual(requests, [
        "http://local/tasks/query",
        "http://local/tasks",
    ]);
    globalThis.fetch = async () =>
        new Response(JSON.stringify({ ...page, missingTaskIds: undefined }));
    await assert.rejects(
        ServerTaskClient.listTasks("http://local", { tracked: [] }),
        /分页格式/,
    );
});

test("native files fall back to JSON before submission when binary upload is not advertised", async () => {
    const file = new File(["pdf"], "paper.pdf");
    let body,
        count = 0;
    globalThis.fetch = async (url, options) => {
        if (url.endsWith("/health"))
            return new Response(
                '{"capabilities":{"taskSubmissionIdempotency":true}}',
            );
        count++;
        assert.equal(options.headers["Content-Type"], "application/json");
        body = JSON.parse(options.body);
        return new Response('{"task":{"taskId":"old-server"}}');
    };
    const result = await ServerTaskClient.createTask(
        "http://local",
        { requestId: "old-request-0001" },
        undefined,
        file,
    );
    assert.equal(result.taskId, "old-server");
    assert.equal(body.fileContent, "data:application/pdf;base64,cGRm");
    assert.equal(body.requestId, "old-request-0001");
    assert.equal(count, 1);
});
