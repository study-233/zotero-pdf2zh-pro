import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import { JSDOM } from "jsdom";
import ts from "typescript";

function compileModule(name) {
    const source = fs.readFileSync(
        new URL(`../src/modules/${name}.ts`, import.meta.url),
        "utf8",
    );
    return ts.transpileModule(source, {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    }).outputText;
}
const code = compileModule("managedEngine");
const paneCode = compileModule("backendPane");

function fixture({
    mode,
    live,
    legacy = false,
    windows = false,
    abi = "aarch64-gcc3",
    build = "26100",
} = {}) {
    const prefs = new Map([["engineManagement", mode]]);
    const calls = { probes: 0, prompts: 0, files: [], timers: [], events: [] };
    const exports = {};
    const imports = {
        "./backendInstallation": {
            identifyBackend: async () => ({ kind: "uninstalled" }),
            engineBinding: async () => ({ root: "/ProfD/pdf2zh-engine" }),
            localEngineAddress: /^http:\/\/(localhost|127\.0\.0\.1):8890\/?$/i,
        },
        "../utils/prefs": {
            getPref: (k) => prefs.get(k),
            setPref: (k, v) => prefs.set(k, v),
        },
        "../utils/locale": {
            getString: (key) => key,
            getLocaleID: (key) => key,
        },
        "./diagnostics": { recordDiagnostic: (e) => calls.events.push(e) },
    };
    const zotero = {
        isMac: !windows,
        isWin: windows,
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
        appinfo: { XPCOMABI: abi },
        sysinfo: { getProperty: (key) => (key === "build" ? build : null) },
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
        "Cc",
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
        { nsIFile: 1, nsIWindowsRegKey: {} },
        {
            "@mozilla.org/windows-registry-key;1": {
                createInstance: () => ({
                    open() {},
                    hasChild: () => false,
                    close() {},
                }),
            },
        },
        (_f, delay) => {
            calls.timers.push(delay);
            return 1;
        },
        () => {},
    );
    return { manager: exports, prefs, calls, imports, zotero, services };
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

test("Windows engine setup accepts native ARM64 on Windows 11 and x64 on Windows 10", async () => {
    for (const [abi, build, expected] of [
        ["aarch64-msvc", "22000", "engine-cancelled"],
        ["x86_64-msvc", "19045", "engine-cancelled"],
        ["aarch64-msvc", "19045", "engine-platform"],
        ["x86-msvc", "26100", "engine-platform"],
    ]) {
        const { manager, calls } = fixture({
            windows: true,
            mode: "auto",
            abi,
            build,
        });
        await assert.rejects(manager.ensureEngine(local), {
            message: expected,
        });
        assert.equal(calls.prompts, expected === "engine-cancelled" ? 1 : 0);
    }
});

test("first-install controls follow Windows engine support in the preferences pane", async () => {
    const pkg = JSON.parse(
        fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"),
    );
    const markup = fs
        .readFileSync(
            new URL("../addon/content/preferences.xhtml", import.meta.url),
            "utf8",
        )
        .replaceAll("__addonRef__", pkg.config.addonRef);
    for (const [abi, build, visible] of [
        ["aarch64-msvc", "22000", true],
        ["x86_64-msvc", "19045", true],
        ["aarch64-msvc", "19045", false],
        ["x86-msvc", "26100", false],
    ]) {
        const { manager, prefs, imports, zotero, services } = fixture({
            windows: true,
            abi,
            build,
        });
        prefs.set("new_serverip", local);
        const modules = {
            ...imports,
            "./managedEngine": manager,
            "../../package.json": pkg,
        };
        const pane = {};
        new Function("require", "exports", "Zotero", "Services", paneCode)(
            (name) => modules[name],
            pane,
            zotero,
            services,
        );
        const dom = new JSDOM(
            `<window xmlns:html="http://www.w3.org/1999/xhtml">${markup}</window>`,
            { contentType: "application/xhtml+xml" },
        );
        try {
            const bound = pane.bindBackendPane(dom.window, async () => {});
            await bound.show(local);
            for (const id of ["panel", "install", "choose"]) {
                const node = dom.window.document.getElementById(
                    `zotero-prefpane-${pkg.config.addonRef}-backend-${id}`,
                );
                assert.equal(
                    node.hidden,
                    !visible,
                    `${abi} build ${build}: ${id}`,
                );
            }
        } finally {
            dom.window.close();
        }
    }
});
