import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate } from "node:timers";
import fs from "node:fs";
import { URL } from "node:url";
import ts from "typescript";
const code = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/windowsBackend.ts", import.meta.url),
        "utf8",
    ),
    {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    },
).outputText;
function fixture() {
    const root = "D:/Backend",
        host = "http://localhost:8890",
        target = "1.10.0",
        version = "1.9.8";
    const info = { root, version, kind: "windows" },
        flags = { url: host, latest: target },
        files = new Map(),
        calls = [];
    const file = (n) => root + "/updates/" + n;
    files.set(
        file("plugin-management.json"),
        JSON.stringify({ schema: 1, version }),
    );
    const modules = {
        "../../package.json": { version: target },
        "../utils/prefs": { getPref: () => flags.url },
        "./diagnostics": { recordDiagnostic: () => {} },
        "./backendInstallation": {
            localEngineAddress: /^http:\/\/localhost:8890$/,
            windowsInstallation: async () => info,
            identifyBackend: async () => info,
        },
    };
    const io = {
        readUTF8: async (p) => files.get(p) || "null",
        makeDirectory: async () => {},
        writeUTF8: async (p, s) => files.set(p, s),
        move: async (a, b) => {
            if (files.has(b)) throw Error("exists");
            files.set(b, files.get(a));
            files.delete(a);
        },
        remove: async (p) => files.delete(p),
    };
    const api = {};
    new Function(
        "require",
        "exports",
        "Zotero",
        "Services",
        "PathUtils",
        "IOUtils",
        "Cc",
        "Ci",
        code,
    )(
        (k) => modules[k],
        api,
        {
            isWin: true,
            HTTP: {
                request: async (_m, url) => ({
                    response: url.endsWith("/health")
                        ? { status: "ok" }
                        : { version: flags.latest },
                }),
            },
            File: { pathToFile: (p) => p },
        },
        {
            vc: {
                compare: (a, b) => a.localeCompare(b, "en", { numeric: true }),
            },
            uuid: { generateUUID: () => String(Math.random()) },
        },
        {
            join: (...xs) => xs.join("/"),
            parent: (s) => s.slice(0, s.lastIndexOf("/")),
        },
        io,
        {
            "@mozilla.org/process/util;1": {
                createInstance: () => ({
                    init: (p) => calls.push(p),
                    runw: (_block, args) => calls.push(args),
                }),
            },
        },
        { nsIProcess: {} },
    );
    return { api, info, flags, files, file, calls };
}
test("Windows checks are read-only; mismatched releases and other URLs never enqueue", async () => {
    const f = fixture();
    assert.equal((await f.api.windowsState(f.info)).supported, true);
    f.flags.latest = "1.11.0";
    assert.equal(await f.api.windowsAction(f.info, "update"), "unpublished");
    f.flags.url = "http://remote";
    await assert.rejects(f.api.windowsAction(f.info, "update"));
    assert.equal(f.files.size, 1);
    assert.deepEqual(f.calls, []);
});
test("automatic follow queues a matching stable release only for opted-in installations", async () => {
    const f = fixture();
    try {
        f.api.startWindowsUpdates();
        await new Promise((resolve) => setImmediate(resolve));
        assert.equal(f.files.has(f.file("plugin-request.json")), false);
        await f.api.setWindowsPolicy(f.info.root, true);
        await new Promise((resolve) => setImmediate(resolve));
        assert.deepEqual(
            JSON.parse(f.files.get(f.file("plugin-request.json"))),
            {
                action: "update",
                target: "1.10.0",
            },
        );
    } finally {
        f.api.stopWindowsUpdates();
    }
});

test("several plugin requests share one matching native update and preserve stopped state", async () => {
    const f = fixture();
    await Promise.all([
        f.api.windowsAction(f.info, "update"),
        f.api.windowsAction(f.info, "update"),
    ]);
    assert.deepEqual(JSON.parse(f.files.get(f.file("plugin-request.json"))), {
        target: "1.10.0",
        action: "update",
    });
    assert.ok(
        f.calls.some(
            (args) => Array.isArray(args) && args.includes("--keep-stopped"),
        ),
    );
    f.files.delete(f.file("plugin-request.json"));
    f.files.set(
        f.file("state.json"),
        JSON.stringify({ phase: "ready", version: "1.10.0" }),
    );
    assert.equal((await f.api.windowsState(f.info)).waiting, true);
    await f.api.windowsAction(f.info, "cancel");
    assert.equal(
        JSON.parse(f.files.get(f.file("plugin-policy.json"))).skip,
        "1.10.0",
    );
    assert.equal(
        JSON.parse(f.files.get(f.file("plugin-request.json"))).action,
        "cancel",
    );
});
