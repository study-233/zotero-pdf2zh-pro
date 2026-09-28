import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { Buffer } from "node:buffer";
import test from "node:test";
import { URL } from "node:url";
import { TextDecoder } from "node:util";
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
        const result = spawnSync("/usr/bin/unzip", ["-t", zip], {
            encoding: "utf8",
        });
        assert.equal(result.status, 0, result.stdout + result.stderr);
        const content = spawnSync(
            "/usr/bin/unzip",
            ["-p", zip, "summary.txt"],
            { encoding: "utf8" },
        );
        assert.equal(content.stdout, "解析第 4 页\n");
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
