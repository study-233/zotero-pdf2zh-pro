import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { Buffer } from "node:buffer";
import test from "node:test";
import { URL, fileURLToPath } from "node:url";
import { TextDecoder, TextEncoder } from "node:util";
import ts from "typescript";
import { diagnosticContract } from "./diagnosticContractFixture.mjs";
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
                `const diagnosticContract=${JSON.stringify(diagnosticContract)};\n` +
                compiled,
        ).toString("base64")
);
const { safeDiagnostic, diagnosticZip, exportDiagnostics } = module;

async function withBundle(respond, task, inspect, setup) {
    const writes = [];
    const original = globalThis.fetch;
    globalThis.PathUtils = { join: (...parts) => parts.join("/") };
    globalThis.Zotero = { DataDirectory: { dir: "/tmp" }, version: "10.0" };
    globalThis.IOUtils = {
        exists: async () => false,
        read: async () => new Uint8Array(),
        write: async (name, data) => writes.push([name, data]),
        remove: async () => {},
    };
    globalThis.ztoolkit = {
        FilePicker: class {
            async open() {
                return "/tmp/compat.zip";
            }
        },
    };
    globalThis.fetch = respond;
    const directory = fs.mkdtempSync(
        path.join(os.tmpdir(), "diagnostic-contract-"),
    );
    try {
        setup?.();
        await exportDiagnostics("http://localhost", task);
        const zip = path.join(directory, "bundle.zip");
        fs.writeFileSync(
            zip,
            writes.find(([name]) => name === "/tmp/compat.zip")[1],
        );
        await inspect((name) => readZip(zip, name));
    } finally {
        globalThis.fetch = original;
        fs.rmSync(directory, { recursive: true, force: true });
    }
}

test("every registered provider error and protocol survives the plugin boundary", () => {
    for (const providerCode of Object.keys(diagnosticContract.providerErrors))
        assert.equal(
            safeDiagnostic({ failedParagraphs: [{ providerCode }] })
                .failedParagraphs[0].providerCode,
            providerCode,
        );
    for (const protocol of diagnosticContract.enums.protocol)
        assert.equal(
            safeDiagnostic({
                protocol,
                metrics: { requests: { protocols: { [protocol]: 1 } } },
            }).protocol,
            protocol,
        );
});

test("payload cannot override the plugin collector's event or timestamp", async () => {
    module.recordDiagnostic("http_response", {
        event: "misleading_event",
        time: "1999-01-01T00:00:00Z",
    });
    await withBundle(
        async () => new globalThis.Response('{"schemaVersion":1}'),
        undefined,
        (read) => {
            const rows = JSON.parse(read("plugin.json")).records;
            assert.ok(
                rows.some(
                    (row) =>
                        row.event === "http_response" &&
                        row.time !== "1999-01-01T00:00:00Z",
                ),
            );
            assert.ok(!rows.some((row) => row.event === "misleading_event"));
        },
    );
});

test("Python persistence and export retain four validation failures through the actual ZIP", async () => {
    const produced = spawnSync(
        process.env.PYTHON ||
            (process.platform === "win32" ? "python" : "python3"),
        [
            fileURLToPath(
                new URL(
                    "../../server/tests/diagnostic_export_fixture.py",
                    import.meta.url,
                ),
            ),
        ],
        { encoding: "utf8" },
    );
    assert.equal(produced.status, 0, produced.stderr);
    const payload = JSON.parse(produced.stdout);
    await withBundle(
        async (url) => {
            assert.ok(url.endsWith("?schemaVersions=1"));
            return new globalThis.Response(JSON.stringify(payload));
        },
        { taskId: "synthetic-four-failures", attempt: 3 },
        (read) => {
            const task = JSON.parse(read("task.json"));
            assert.deepEqual(
                task.failedParagraphs.map((row) => row.validationCode),
                [
                    "placeholder_mismatch",
                    "unchanged_translation",
                    "target_language_missing",
                    "placeholder_mismatch",
                ],
            );
            assert.equal(task.failedParagraphs[0].missingPlaceholderCount, 1);
            assert.equal(
                JSON.parse(read("manifest.json")).sources.server.status,
                "available",
            );
            assert.match(read("summary.txt"), /placeholder_mismatch=2/);
            assert.match(read("summary.txt"), /失败=4/);
            const graph = JSON.parse(read("evidence.json")).taskEvidence[0];
            assert.equal(graph.evidenceStatus, "available");
            assert.equal(graph.executions.length, 1);
            assert.equal(graph.requests.length, 9);
            for (const failed of task.failedParagraphs) {
                assert.match(failed.paragraphRef, /^p-[0-9a-f]{32}$/);
                assert.equal(failed.callRefs.length, 3);
                for (const call of failed.callRefs)
                    assert.ok(
                        graph.requests.some(
                            (request) =>
                                request.callId === call &&
                                request.paragraphRefs.includes(
                                    failed.paragraphRef,
                                ),
                        ),
                    );
            }
            const findings = JSON.parse(read("analysis.json")).findings;
            assert.ok(
                findings.some(
                    (row) =>
                        row.findingCode === "paragraph_unresolved" &&
                        row.linkedFailures === 4,
                ),
            );
            assert.ok(
                !findings.some(
                    (row) => row.findingCode === "provider_request_failed",
                ),
            );
            assert.match(read("summary.txt"), /译文未通过校验/);
            for (const name of [
                "task.json",
                "server.json",
                "plugin.json",
                "manifest.json",
                "summary.txt",
                "evidence.json",
                "analysis.json",
            ])
                assert.ok(
                    !read(name).includes("private diagnostic fixture text"),
                );
        },
    );
});

test("old v1 service remains usable and missing validation evidence is explicit", async () => {
    const payload = {
        schemaVersion: 1,
        tasks: [
            {
                taskId: "legacy",
                status: "incomplete",
                failedParagraphs: [
                    { page: 1, errorType: "InvalidTranslation" },
                ],
            },
        ],
        records: [],
    };
    await withBundle(
        async () => new globalThis.Response(JSON.stringify(payload)),
        { taskId: "legacy" },
        (read) => {
            const manifest = JSON.parse(read("manifest.json"));
            assert.equal(manifest.sources.server.status, "partial");
            assert.equal(
                manifest.sources.server.reasonCode,
                "legacy_validation_details_missing",
            );
            assert.equal(manifest.sources.server.schemaRevision, undefined);
            assert.equal(manifest.sources.evidence.status, "unavailable");
            assert.match(read("summary.txt"), /缺少具体错误码/);
            assert.equal(
                JSON.parse(read("evidence.json")).taskEvidence.length,
                0,
            );
            assert.equal(
                JSON.parse(read("analysis.json")).findings[0].findingCode,
                "evidence_unavailable",
            );
        },
    );
});

test("malformed optional evidence shapes still yield a compatible diagnostic ZIP", async () => {
    await withBundle(
        async () =>
            new globalThis.Response(
                JSON.stringify({
                    schemaVersion: 1,
                    evidence: { taskEvidence: {} },
                    analysis: { findings: {} },
                }),
            ),
        undefined,
        (read) => {
            assert.deepEqual(
                JSON.parse(read("evidence.json")).taskEvidence,
                [],
            );
            assert.equal(
                JSON.parse(read("analysis.json")).findings[0].findingCode,
                "evidence_unavailable",
            );
        },
    );
});

test("opaque links survive filtering while raw paragraph IDs and arbitrary links are rejected", () => {
    const paragraphRef = "p-" + "a".repeat(32);
    const callId = "c-" + "b".repeat(32) + "-2";
    const report = { filteredFields: 0, rejectedValues: 0 };
    const value = safeDiagnostic(
        {
            evidence: {
                taskEvidence: [
                    {
                        taskId: "test",
                        requests: [
                            {
                                callId,
                                paragraphRefs: [
                                    paragraphRef,
                                    "private source hash",
                                ],
                            },
                        ],
                    },
                ],
            },
            failedParagraphs: [
                {
                    paragraphRef,
                    paragraphId: "private document id",
                    callRefs: [callId, "Bearer secret"],
                },
            ],
            operationId: "private prompt",
        },
        0,
        report,
    );
    assert.equal(value.evidence.taskEvidence[0].requests[0].callId, callId);
    assert.deepEqual(value.failedParagraphs[0].callRefs, [callId]);
    assert.ok(!JSON.stringify(value).includes("private"));
    assert.ok(report.rejectedValues >= 3);
});

test("additive future revisions retain known evidence and report filtered data without values", async () => {
    const payload = {
        schemaVersion: 1,
        schemaRevision: 999,
        tasks: [
            {
                taskId: "newer",
                protocol: "codex_app_server",
                providerCode: "chatgpt_usage_limit",
            },
        ],
        futureField: "private future content",
        records: [],
    };
    await withBundle(
        async () => new globalThis.Response(JSON.stringify(payload)),
        { taskId: "newer" },
        (read) => {
            const manifest = JSON.parse(read("manifest.json"));
            assert.equal(manifest.sources.server.schemaRevision, 999);
            assert.equal(manifest.sources.server.filteredFields, 1);
            assert.equal(
                JSON.parse(read("task.json")).providerCode,
                "chatgpt_usage_limit",
            );
            assert.ok(!read("manifest.json").includes("futureField"));
            assert.ok(!read("server.json").includes("private future content"));
        },
    );
});

test("collection failures have specific reasons and still produce a readable fallback bundle", async () => {
    const cases = [
        [
            "unsupported_schema",
            async () => new globalThis.Response('{"schemaVersion":2}'),
        ],
        [
            "unsupported_schema",
            async () =>
                new globalThis.Response("private error", { status: 406 }),
        ],
        [
            "interface_unavailable",
            async () =>
                new globalThis.Response("private error", { status: 404 }),
        ],
        [
            "http_error",
            async () =>
                new globalThis.Response("private error", { status: 503 }),
        ],
        ["invalid_payload", async () => new globalThis.Response("{")],
        [
            "invalid_payload",
            async () =>
                new globalThis.Response('{"schemaVersion":1,"tasks":{}}'),
        ],
        [
            "unreachable",
            async () => {
                throw new TypeError("sk-private error");
            },
        ],
        [
            "size_limit",
            async () => new globalThis.Response("x".repeat(13 * 1024 * 1024)),
        ],
    ];
    for (const [code, respond] of cases) {
        await withBundle(
            respond,
            { taskId: "fallback", attempt: 1 },
            (read) => {
                assert.equal(
                    JSON.parse(read("manifest.json")).sources.server.reasonCode,
                    code,
                );
                assert.equal(JSON.parse(read("task.json")).taskId, "fallback");
                assert.equal(JSON.parse(read("plugin.json")).schemaVersion, 1);
                assert.ok(!read("manifest.json").includes("private error"));
                assert.ok(!read("summary.txt").includes("sk-private"));
            },
        );
    }
});

test("network timeout and stalled disk collection are bounded and explained", async () => {
    const nativeTimer = globalThis.setTimeout;
    globalThis.setTimeout = (callback, delay, ...args) =>
        nativeTimer(callback, delay === 10000 ? 1 : delay, ...args);
    try {
        await withBundle(
            (_url, options) =>
                new Promise((_resolve, reject) => {
                    options.signal.addEventListener("abort", () =>
                        reject(
                            new globalThis.DOMException(
                                "aborted",
                                "AbortError",
                            ),
                        ),
                    );
                }),
            { taskId: "timeout" },
            (read) => {
                assert.ok(
                    ["timeout", "collection_deadline"].includes(
                        JSON.parse(read("manifest.json")).sources.server
                            .reasonCode,
                    ),
                );
            },
        );
        await withBundle(
            async () =>
                new globalThis.Response(
                    '{"schemaVersion":1,"tasks":[],"records":[]}',
                ),
            { taskId: "disk-stall" },
            (read) => {
                assert.equal(
                    JSON.parse(read("manifest.json")).sources.server.reasonCode,
                    "collection_deadline",
                );
            },
            () => {
                globalThis.IOUtils.exists = () => new Promise(() => {});
            },
        );
    } finally {
        globalThis.setTimeout = nativeTimer;
    }
});

test("fixed validation codes survive old task reasons and new server exports", () => {
    const safe = safeDiagnostic({
        failedParagraphs: [
            {
                page: 1,
                errorType: "InvalidTranslation",
                reason: "placeholder_mismatch → InvalidTranslation → private text",
            },
            {
                page: 6,
                errorType: "InvalidTranslation",
                reason: "unchanged_translation → InvalidTranslation",
            },
            {
                page: 10,
                errorType: "InvalidTranslation",
                validationCode: "target_language_missing",
            },
            {
                page: 11,
                errorType: "InvalidTranslation",
                reason: "private text",
                validationCode: "sk-secret",
            },
            {
                page: 12,
                errorType: "RuntimeError",
                reason: "placeholder_mismatch → private text",
            },
        ],
    });
    assert.deepEqual(
        safe.failedParagraphs.map((row) => row.validationCode),
        [
            "placeholder_mismatch",
            "unchanged_translation",
            "target_language_missing",
            undefined,
            undefined,
        ],
    );
    assert.deepEqual(safeDiagnostic(safe), safe);
    assert.deepEqual(safeDiagnostic({ validationCode: [] }), {});
    for (const privateValue of ["private text", "sk-secret", "reason"])
        assert.ok(!JSON.stringify(safe).includes(privateValue));
    const summary = module.diagnosticSummary(safe);
    assert.match(summary, /placeholder_mismatch=1/);
    assert.match(summary, /unchanged_translation=1/);
    assert.match(summary, /target_language_missing=1/);
    assert.ok(!module.diagnosticSummary({}).includes("校验失败原因"));
});

test("recovery and timing survive diagnostic export without submission identities", () => {
    const timing = {
        queuedAt: "2026-01-01T00:00:00Z",
        queueSeconds: 5,
        executionSeconds: 12,
        totalSeconds: 17,
        complete: false,
    };
    const exported = safeDiagnostic({
        status: "paused",
        recoveryState: "queued",
        canResume: true,
        timing: { ...timing, requestId: "private-id" },
        submission_fingerprint: "private-hash",
    });
    assert.deepEqual(exported, {
        status: "paused",
        recoveryState: "queued",
        canResume: true,
        timing,
    });
});

test("cancelled requests remain separate in diagnostic export and summary", () => {
    const payload = {
        metrics: {
            requests: { attempts: 1, succeeded: 0, failed: 0, cancelled: 1 },
        },
    };
    assert.equal(safeDiagnostic(payload).metrics.requests.cancelled, 1);
    assert.match(module.diagnosticSummary(payload), /取消=1/);
});

test("batch repair calls remain separate from provider retries", () => {
    const payload = { metrics: { requests: { retries: 1, repairs: 7 } } };
    assert.equal(safeDiagnostic(payload).metrics.requests.repairs, 7);
    assert.match(module.diagnosticSummary(payload), /批次修复=7/);
});

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
                        failedParagraphs: [
                            {
                                page: 6,
                                errorType: "InvalidTranslation",
                                validationCode: "placeholder_mismatch",
                                reason: "private text",
                            },
                        ],
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
        assert.equal(
            task.failedParagraphs[0].validationCode,
            "placeholder_mismatch",
        );
        assert.equal(
            JSON.parse(read("server.json")).tasks[0].failedParagraphs[0]
                .validationCode,
            "placeholder_mismatch",
        );
        assert.ok(!read("task.json").includes("private text"));
        assert.match(read("summary.txt"), /placeholder_mismatch=1/);
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

test("output speed remains available in sanitized diagnostic exports", () => {
    const payload = {
        metrics: { throughput: { outputTokensPerSecond: 12.3 } },
    };
    assert.deepEqual(safeDiagnostic(payload), payload);
});
