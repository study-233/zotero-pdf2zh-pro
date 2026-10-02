import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { Buffer } from "node:buffer";
import test from "node:test";
import { URL } from "node:url";
import { TextDecoder, TextEncoder } from "node:util";
import ts from "typescript";
const compiled = ts
    .transpileModule(
        fs.readFileSync(
            new URL("../src/modules/diagnostics.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.ESNext,
                target: ts.ScriptTarget.ES2022,
            },
        },
    )
    .outputText.replace(/^import .*;$/gm, "");
const module = await import(
    "data:text/javascript;base64," +
        Buffer.from(
            'const config={addonRef:"test",addonName:"test"}; const version="1.0";\n' +
                compiled,
        ).toString("base64")
);
const { safeDiagnostic, diagnosticZip, exportDiagnostics } = module;

function readZip(zip, entry) {
    const result = spawnSync(
        process.env.PYTHON ||
            (process.platform === "win32" ? "python" : "python3"),
        [
            "-c",
            "import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None; sys.stdout.buffer.write(z.read(sys.argv[2]))",
            zip,
            entry,
        ],
        { encoding: "utf8" },
    );
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    return result.stdout;
}

test("diagnostic whitelist excludes document data and credentials", () => {
    const text = JSON.stringify(
        safeDiagnostic({
            taskId: "abc",
            status: "running",
            currentPage: 4,
            fileName: "Secret Paper.pdf",
            apiKey: "sk-12345",
            prompt: "private text",
            apiUrl: "https://secret.invalid",
            records: [
                {
                    event: "exception",
                    message: "Bearer abc",
                    frames: [
                        {
                            file: "/Users/private/main.py",
                            function: "parse",
                            line: 4,
                        },
                    ],
                },
            ],
        }),
    );
    for (const secret of [
        "Secret Paper",
        "sk-12345",
        "private text",
        "secret.invalid",
        "Bearer",
        "/Users/private",
    ])
        assert.ok(!text.includes(secret));
    assert.match(text, /currentPage/);
});

test("diagnostic ZIP is readable with intact Chinese text and CRC", () => {
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), "diagnostic-zip-"));
    try {
        const zip = path.join(temp, "test.zip");
        fs.writeFileSync(
            zip,
            diagnosticZip({
                "summary.txt": "解析第 4 页\n",
                "server.json": '{"schemaVersion":1}',
            }),
        );
        assert.equal(readZip(zip, "summary.txt"), "解析第 4 页\n");
    } finally {
        fs.rmSync(temp, { recursive: true, force: true });
    }
});

test("ZIP refuses paths and packages larger than limit", () => {
    assert.throws(() => diagnosticZip({ "../secret": "x" }));
    assert.throws(() =>
        diagnosticZip({ "large.txt": "x".repeat(20 * 1024 * 1024 + 1) }),
    );
});

test("offline export saves a partial ZIP and picker cancellation writes nothing", async () => {
    const writes = [];
    let chosen = "/tmp/diagnostic.zip";
    globalThis.PathUtils = { join: (...parts) => parts.join("/") };
    globalThis.Zotero = { DataDirectory: { dir: "/tmp" }, version: "8.0" };
    globalThis.IOUtils = {
        exists: async () => false,
        read: async () => new Uint8Array(),
        write: async (p, b) => writes.push([p, b]),
        remove: async () => {},
    };
    globalThis.ztoolkit = {
        FilePicker: class {
            async open() {
                return chosen;
            }
        },
    };
    const original = globalThis.fetch;
    globalThis.fetch = async () => {
        throw new Error("sk-private-key");
    };
    try {
        await exportDiagnostics("http://offline", {
            taskId: "abc",
            fileName: "private.pdf",
        });
        const bytes = writes.find(([p]) => p === "/tmp/diagnostic.zip")?.[1];
        assert.ok(bytes);
        const text = new TextDecoder().decode(bytes);
        assert.match(text, /服务端诊断不可用/);
        assert.ok(!text.includes("sk-private-key"));
        assert.ok(!text.includes("private.pdf"));
        chosen = "";
        const count = writes.filter(
            ([p]) => p === "/tmp/diagnostic.zip",
        ).length;
        await exportDiagnostics("http://offline");
        assert.equal(
            writes.filter(([p]) => p === "/tmp/diagnostic.zip").length,
            count,
        );
    } finally {
        globalThis.fetch = original;
    }
});

test("configuration, outcomes and count maps survive both export boundaries", () => {
    const safe = safeDiagnostic({
        requestedConfiguration: {
            provider: "openaicompatible",
            model: "org/model:latest",
            qps: 10,
            poolSize: 50,
            apiKey: "sk-private",
            apiUrl: "https://private.invalid",
        },
        effectiveConfiguration: {
            provider: "openaicompatible",
            model: "org/resolved",
            protocol: "responses",
            qps: 2,
            poolSize: 4,
        },
        translationSummary: {
            succeeded: 1,
            failed: 93,
            pending: 5,
            skipped: 10,
        },
        qualitySummary: { checked: 3, failed: 1 },
        metrics: {
            requests: {
                attempts: 100,
                succeeded: 2,
                failed: 98,
                statusCodes: { 429: 93, 200: 2, unknown: 5, "sk-private": 2 },
                errorTypes: {
                    APITimeoutError: 5,
                    RateLimitError: 93,
                    "private text": 1,
                },
                byKind: {
                    initialization: { succeeded: 1 },
                    translation: { succeeded: 1 },
                },
            },
            tokens: { total: 509, input: 376, output: 133 },
        },
        failedParagraphs: [
            {
                page: 4,
                attempts: 2,
                errorType: "RateLimitError",
                statusCode: 429,
                providerCode: "insufficient_quota",
                reason: "private text",
                paragraphId: "private-hash",
            },
        ],
    });
    assert.equal(safe.requestedConfiguration.model, "org/model:latest");
    assert.equal(safe.effectiveConfiguration.qps, 2);
    assert.equal(safe.metrics.requests.attempts, 100);
    assert.equal(safe.metrics.tokens.total, 509);
    assert.equal(safe.metrics.requests.byKind.translation.succeeded, 1);
    assert.equal(safe.failedParagraphs[0].providerCode, "insufficient_quota");
    for (const secret of [
        "sk-private",
        "private.invalid",
        "private text",
        "private-hash",
    ])
        assert.ok(!JSON.stringify(safe).includes(secret));
    for (const model of [
        "https://private.invalid/model",
        "/Users/private/model",
        "org/sk-private",
        "Bearer-secret",
        "a".repeat(201),
    ])
        assert.deepEqual(safeDiagnostic({ model }), {});
    assert.deepEqual(
        safeDiagnostic({
            metrics: {
                requests: { statusCodes: { 429: -1, 200: true, 500: 2 } },
            },
        }),
        { metrics: { requests: { statusCodes: { 500: 2 } } } },
    );
});

test("summary separates request counts from tokens and marks missing configuration", () => {
    const summary = module.diagnosticSummary({
        taskId: "abc",
        status: "incomplete",
        attempt: 1,
        effectiveConfiguration: {
            provider: "openai",
            model: "org/model:latest",
            qps: 2,
            poolSize: 4,
        },
        translationSummary: {
            succeeded: 1,
            failed: 93,
            pending: 5,
            skipped: 10,
        },
        metrics: {
            requests: {
                attempts: 100,
                succeeded: 2,
                failed: 98,
                retries: 47,
                statusCodes: { 429: 93 },
                errorTypes: { APITimeoutError: 5 },
            },
            tokens: { total: 509 },
        },
    });
    assert.match(summary, /尝试=100/);
    assert.match(summary, /429=93，超时=5/);
    assert.match(summary, /org\/model:latest/);
    assert.match(summary, /提交配置：未记录/);
    assert.match(summary, /均不代表所有段落翻译成功/);
    assert.ok(!summary.includes("509"));
    assert.match(module.diagnosticSummary({}), /实际生效配置：未记录/);
});

test("online ZIP prefers server task metadata and removes duplicate client records", async () => {
    const writes = [];
    const row = {
        time: "2026-09-29T04:00:00Z",
        event: "task_state",
        taskId: "abc",
        status: "incomplete",
    };
    globalThis.PathUtils = { join: (...parts) => parts.join("/") };
    globalThis.Zotero = { DataDirectory: { dir: "/tmp" }, version: "9.0" };
    globalThis.IOUtils = {
        exists: async () => true,
        read: async () => new TextEncoder().encode(JSON.stringify(row) + "\n"),
        write: async (p, b) => writes.push([p, b]),
        remove: async () => {},
    };
    globalThis.ztoolkit = {
        FilePicker: class {
            async open() {
                return "/tmp/online.zip";
            }
        },
    };
    const original = globalThis.fetch;
    globalThis.fetch = async () =>
        new globalThis.Response(
            JSON.stringify({
                schemaVersion: 1,
                records: [],
                tasks: [
                    {
                        taskId: "abc",
                        status: "incomplete",
                        effectiveConfiguration: {
                            provider: "openai",
                            model: "org/runtime",
                            qps: 2,
                            poolSize: 4,
                        },
                        metrics: {
                            requests: {
                                attempts: 100,
                                failed: 98,
                                statusCodes: { 429: 93 },
                                errorTypes: { APITimeoutError: 5 },
                            },
                            tokens: { total: 509 },
                        },
                    },
                ],
            }),
        );
    const temp = fs.mkdtempSync(path.join(os.tmpdir(), "online-diagnostics-"));
    try {
        await exportDiagnostics("http://localhost", {
            taskId: "abc",
            status: "running",
            effectiveConfiguration: { model: "stale" },
        });
        const zip = path.join(temp, "export.zip");
        fs.writeFileSync(zip, writes.find(([p]) => p === "/tmp/online.zip")[1]);
        const read = (name) => readZip(zip, name);
        const task = JSON.parse(read("task.json"));
        assert.equal(task.effectiveConfiguration.model, "org/runtime");
        assert.match(read("summary.txt"), /429=93，超时=5/);
        assert.equal(
            JSON.parse(read("plugin.json")).records.filter(
                (r) => r.taskId === "abc" && r.time === row.time,
            ).length,
            1,
        );
        globalThis.fetch = async () =>
            new globalThis.Response(
                JSON.stringify({
                    schemaVersion: 1,
                    tasks: [{ taskId: "abc", attempt: 2, status: "queued" }],
                    records: [],
                }),
            );
        await exportDiagnostics("http://localhost", {
            taskId: "abc",
            attempt: 1,
            importState: "imported",
            effectiveConfiguration: { model: "previous-attempt" },
            metrics: { requests: { attempts: 100 } },
        });
        fs.writeFileSync(
            zip,
            writes.filter(([p]) => p === "/tmp/online.zip").at(-1)[1],
        );
        const retry = JSON.parse(read("task.json"));
        assert.equal(retry.attempt, 2);
        assert.equal(retry.effectiveConfiguration, undefined);
        assert.equal(retry.metrics, undefined);
        assert.equal(retry.importState, undefined);
    } finally {
        globalThis.fetch = original;
        fs.rmSync(temp, { recursive: true, force: true });
    }
});
