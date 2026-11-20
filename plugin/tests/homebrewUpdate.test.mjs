import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import { setImmediate } from "node:timers";
import ts from "typescript";

const code = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/homebrewUpdate.ts", import.meta.url),
        "utf8",
    ),
    {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    },
).outputText;
const version = "1.9.8";
function setup({
    follow = false,
    host = "http://127.0.0.1:8890",
    mac = true,
    engine = "external",
    stored = {},
} = {}) {
    const prefs = new Map(
        Object.entries({
            new_serverip: host,
            engineManagement: engine,
            brewFollowPlugin: follow,
            ...stored,
        }),
    );
    const calls = [],
        events = [],
        timers = new Map(),
        observers = new Map();
    let serial = 0,
        status = "available",
        outcome = "installing",
        intercept;
    const api = {};
    const imports = {
        "../../package.json": { config: { prefsPrefix: "test" }, version },
        "../utils/prefs": {
            getPref: (k) => prefs.get(k),
            setPref: (k, v) => prefs.set(k, v),
        },
        "./diagnostics": { recordDiagnostic: (e) => events.push(e) },
    };
    const subprocess = {
        call: async ({ command, arguments: args }) => {
            assert.equal(
                command,
                "/opt/homebrew/opt/python@3.13/bin/python3.13",
            );
            assert.equal(args.at(-1), version);
            const action = args[2];
            calls.push(action);
            if (intercept) await intercept(action);
            const phase =
                action === "auto" || action === "manual" ? outcome : status;
            let text = JSON.stringify({
                state: phase,
                installed: phase === "current" ? version : "1.9.6",
                running: null,
                directory: "/brew/var/data",
            });
            return {
                stdout: {
                    readString: async () => {
                        const value = text;
                        text = "";
                        return value;
                    },
                },
                wait: async () => ({ exitCode: 0 }),
            };
        },
    };
    const zotero = {
        isMac: mac,
        File: { getContentsFromURLAsync: async () => "helper" },
        Prefs: {
            registerObserver: (key, fn) => {
                observers.set(key, fn);
                return key;
            },
            unregisterObserver: (key) => observers.delete(key),
        },
    };
    new Function(
        "require",
        "exports",
        "Zotero",
        "Services",
        "Ci",
        "PathUtils",
        "IOUtils",
        "ChromeUtils",
        "setTimeout",
        "clearTimeout",
        code,
    )(
        (key) => imports[key],
        api,
        zotero,
        { dirsvc: { get: () => ({ path: "/profile" }) } },
        { nsIFile: 1 },
        { join: (...p) => p.join("/") },
        {
            exists: async (p) => p.startsWith("/opt/homebrew/opt/"),
            writeUTF8: async () => {},
        },
        { importESModule: () => ({ Subprocess: subprocess }) },
        (fn, delay) => {
            timers.set(++serial, { fn, delay });
            return serial;
        },
        (k) => timers.delete(k),
    );
    const settle = () => new Promise((resolve) => setImmediate(resolve));
    return {
        api,
        calls,
        events,
        timers,
        prefs,
        observers,
        settle,
        status: (v) => (status = v),
        outcome: (v) => (outcome = v),
        intercept: (v) => (intercept = v),
        tick: async (delay) => {
            const found = [...timers].find(([, v]) => v.delay === delay);
            assert.ok(found, "expected timer " + delay);
            timers.delete(found[0]);
            found[1].fn();
            await settle();
        },
    };
}
test("automatic update is opt-in and never operates on remote, managed or non-Mac installations", async () => {
    for (const opts of [
        {},
        { follow: true, host: "http://192.0.2.1:8890" },
        { follow: true, mac: false },
        { follow: true, engine: "managed" },
    ]) {
        const x = setup(opts);
        x.api.startBrewUpdates();
        assert.equal(x.timers.size, 0);
        x.api.observeBrewUpdates(() => {});
        await x.settle();
        assert.deepEqual(x.calls, opts.follow ? [] : ["status"]);
        x.api.stopBrewUpdates();
        assert.equal(x.observers.size, 0);
    }
});
test("startup waits, resumes the same target and polls without starting a second install", async () => {
    const x = setup({ follow: true });
    x.outcome("waiting");
    x.api.startBrewUpdates();
    await x.tick(0);
    assert.deepEqual(x.calls, ["status", "auto"]);
    assert.equal(x.api.brewControls().state, "waiting");
    x.outcome("installing");
    await x.tick(60000);
    assert.equal(x.prefs.get("brewInFlight"), true);
    x.status("installing");
    x.api.brewUpdateAction();
    await x.tick(2000);
    assert.equal(x.calls.filter((c) => c === "auto").length, 2);
    x.status("current");
    await x.tick(2000);
    assert.equal(x.timers.size, 0);
    assert.equal(x.prefs.get("brewInFlight"), false);
});
test("manual updates keep their own waiting intent across restart with automatic updates off", async () => {
    const x = setup();
    x.api.startBrewUpdates();
    x.api.observeBrewUpdates(() => {});
    await x.settle();
    x.outcome("waiting");
    x.api.brewUpdateAction();
    await x.settle();
    assert.equal(x.prefs.get("brewManualTarget"), version);
    assert.equal(x.prefs.get("brewFollowPlugin"), false);
    x.api.stopBrewUpdates();
    x.api.startBrewUpdates();
    x.outcome("installing");
    await x.tick(0);
    assert.deepEqual(x.calls, [
        "status",
        "status",
        "manual",
        "status",
        "manual",
    ]);
    assert.equal(x.prefs.get("brewFollowPlugin"), false);
});
test("manual request survives contention until it can join the other profile's update", async () => {
    const x = setup();
    x.api.startBrewUpdates();
    x.api.observeBrewUpdates(() => {});
    await x.settle();
    x.outcome("checking");
    x.api.brewUpdateAction();
    await x.settle();
    assert.equal(x.prefs.get("brewManualTarget"), version);
    assert.equal(x.api.brewControls().visible, true);
    x.status("installing");
    await x.tick(2000);
    assert.equal(x.api.brewControls().updating, true);
    assert.equal(x.prefs.get("brewManualTarget"), "");
    x.status("current");
    await x.tick(2000);
    assert.equal(x.timers.size, 0);
    assert.equal(x.calls.filter((action) => action === "manual").length, 1);
});
test("cancel skips this release, turning follow off during a check prevents installation", async () => {
    const x = setup({ follow: true });
    x.outcome("waiting");
    x.api.startBrewUpdates();
    await x.tick(0);
    x.api.brewUpdateAction();
    await x.tick(0);
    assert.equal(x.prefs.get("brewSkipTarget"), version);
    assert.equal(x.timers.size, 0);
    let resume;
    x.intercept(
        () =>
            new Promise((resolve) => {
                resume = resolve;
            }),
    );
    x.api.followBrewUpdates(true);
    await x.tick(0);
    x.api.followBrewUpdates(false);
    x.intercept(null);
    resume();
    await x.settle();
    await x.tick(0);
    assert.equal(x.calls.filter((c) => c === "auto").length, 1);
});
test("changing the server during preflight invalidates the result; a failed target needs manual retry", async () => {
    const x = setup({ follow: true });
    let resume;
    x.intercept(
        () =>
            new Promise((resolve) => {
                resume = resolve;
            }),
    );
    x.api.startBrewUpdates();
    await x.tick(0);
    x.prefs.set("new_serverip", "https://example.org");
    x.observers.get("test.new_serverip")();
    x.intercept(null);
    resume();
    await x.settle();
    assert.deepEqual(x.calls, ["status"]);
    assert.equal(x.api.brewControls().visible, false);
    x.status("restored");
    x.prefs.set("new_serverip", "http://localhost:8890");
    x.observers.get("test.new_serverip")();
    await x.tick(0);
    assert.equal(x.timers.size, 0);
    assert.deepEqual(x.events, ["brew_update_failed"]);
    x.api.brewUpdateAction();
    await x.settle();
    assert.equal(x.calls.at(-1), "manual");
});
