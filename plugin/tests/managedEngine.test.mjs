import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const code = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/managedEngine.ts", import.meta.url),
        "utf8",
    ),
    {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    },
).outputText;

function fixture({ mode, live, legacy = false } = {}) {
    const prefs = new Map([["engineManagement", mode]]);
    const calls = { probes: 0, prompts: 0, files: [], timers: [], events: [] };
    const exports = {};
    const imports = {
        "./backendInstallation": {
            engineBinding: async () => ({ root: "/ProfD/pdf2zh-engine" }),
            localEngineAddress: /^http:\/\/(localhost|127\.0\.0\.1):8890\/?$/i,
        },
        "../utils/prefs": {
            getPref: (k) => prefs.get(k),
            setPref: (k, v) => prefs.set(k, v),
        },
        "../utils/locale": { getString: (key) => key },
        "./diagnostics": { recordDiagnostic: (e) => calls.events.push(e) },
    };
    const zotero = {
        isMac: true,
        HTTP: {
            request: async () => {
                calls.probes++;
                if (!live) throw Error("offline");
                return { response: live };
            },
        },
        getMainWindow: () => ({
            confirm() {
                calls.prompts++;
                return false;
            },
        }),
        File: { getContentsFromURLAsync: async () => '{"id":"candidate"}' },
    };
    const services = {
        appinfo: { XPCOMABI: "aarch64-gcc3" },
        dirsvc: { get: (key) => ({ path: "/" + key }) },
    };
    const io = {
        exists: async (path) => {
            calls.files.push(path);
            return (
                path.endsWith("/owner") ||
                (legacy && path.startsWith("/opt/homebrew/"))
            );
        },
        readUTF8: async () => "self",
    };
    new Function(
        "require",
        "exports",
        "Zotero",
        "Services",
        "IOUtils",
        "PathUtils",
        "Ci",
        "setTimeout",
        "clearTimeout",
        code,
    )(
        (key) => imports[key],
        exports,
        zotero,
        services,
        io,
        { join: (...parts) => parts.join("/") },
        { nsIFile: 1 },
        (_f, delay) => {
            calls.timers.push(delay);
            return 1;
        },
        () => {},
    );
    return { manager: exports, prefs, calls };
}
const local = "http://127.0.0.1:8890";

test("upgrading a previous installation preserves even a stopped external endpoint", async () => {
    const { manager, prefs, calls } = fixture();
    manager.initializeEngine(true);
    await manager.ensureEngine(local);
    assert.equal(prefs.get("engineManagement"), "external");
    assert.equal(calls.probes, 0);
    assert.deepEqual(calls.files, []);
});
test("custom service URLs, ports and paths never access the installer", async () => {
    const { manager, calls } = fixture({ mode: "managed" });
    for (const url of [
        "https://remote.test",
        "http://127.0.0.1:18890",
        "http://localhost:8890/proxy",
    ])
        await manager.ensureEngine(url);
    assert.equal(calls.probes, 0);
});
test("fresh profiles adopt running services and preserve inactive legacy data", async () => {
    const first = fixture({
        mode: "auto",
        live: { status: "ok", version: "1" },
    });
    await first.manager.ensureEngine(local);
    assert.equal(first.prefs.get("engineManagement"), "external");
    const second = fixture({ mode: "auto", legacy: true });
    await assert.rejects(second.manager.ensureEngine(local), /engine-legacy/);
    assert.equal(second.prefs.get("engineManagement"), "external");
    assert.equal(second.calls.prompts, 0);
});
test("simultaneous first-use requests share one confirmation and cancellation is retryable", async () => {
    const { manager, prefs, calls } = fixture({ mode: "auto" });
    const first = manager.ensureEngine(local);
    assert.equal(manager.ensureEngine(local), first);
    await assert.rejects(first, /engine-cancelled/);
    assert.equal(calls.prompts, 1);
    assert.equal(prefs.get("engineManagement"), "auto");
    await assert.rejects(manager.ensureEngine(local), /engine-cancelled/);
    assert.equal(calls.prompts, 2);
});
test("running work defers the upgrade and schedules another check", async () => {
    const { manager, calls } = fixture({
        mode: "managed",
        live: {
            version: "1",
            status: "ok",
            managedOwner: "self",
            managedRevision: "previous",
            maintenance: { busy: true },
        },
    });
    await manager.ensureEngine(local);
    assert.deepEqual(calls.timers, [60000]);
    assert.equal(calls.prompts, 0);
    assert.equal(manager.engineStatusMessage(), "engine-deferred");
});
