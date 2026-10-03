import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";
import { webcrypto } from "node:crypto";

function fixture() {
    const logs = [],
        timers = new Map(),
        calls = [];
    const state = {
        fail: false,
        pending: false,
        mainMissing: false,
        lateResolve: undefined,
        ignoreAbort: false,
        cancelFail: false,
    };
    const scope = {
        AbortController: globalThis.AbortController,
        crypto: webcrypto,
        async fetch(url, options) {
            assert.equal(this, scope);
            assert.ok(options.signal instanceof globalThis.AbortSignal);
            calls.push({ url, options });
            if (url.endsWith("/cancel-text")) {
                if (state.cancelFail) throw new Error("cancel unavailable");
                return { ok: true, json: async () => ({ status: "ok" }) };
            }
            if (state.fail) throw new Error("secret-response-or-key");
            if (state.pending)
                return new Promise((resolve, reject) => {
                    state.lateResolve = resolve;
                    if (!state.ignoreAbort)
                        options.signal.addEventListener("abort", () =>
                            reject(new Error("aborted")),
                        );
                });
            return {
                ok: true,
                status: 200,
                json: async () => ({ translation: "译文" }),
            };
        },
    };
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/selectionRequest.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    const exports = {};
    new Function(
        "require",
        "exports",
        "Zotero",
        "globalThis",
        "setTimeout",
        "clearTimeout",
        code,
    )(
        () => ({ recordDiagnostic: (...args) => logs.push(args) }),
        exports,
        {
            getMainWindow: () => {
                if (state.mainMissing) throw new Error("window gone");
                return scope;
            },
        },
        {},
        (callback, delay) => {
            const id = {};
            timers.set(id, { callback, delay });
            return id;
        },
        (id) => timers.delete(id),
    );
    return { ...exports, state, logs, timers, calls, scope };
}

test("selection fetch uses matching runtime scope and signal; timer is cleared", async () => {
    const f = fixture();
    const request = f.createSelectionRequest();
    const response = await request.post(
        "http://localhost/translate-text",
        { text: "private-paper" },
        50000,
    );
    assert.equal(response.data.translation, "译文");
    assert.equal(f.timers.size, 0);
    assert.equal(f.calls.length, 1);
    assert.doesNotMatch(JSON.stringify(f.logs), /private-paper/);
});

test("explicit cancellation propagates to in-flight fetch", async () => {
    const f = fixture();
    f.state.pending = true;
    const request = f.createSelectionRequest();
    const pending = request.post("http://localhost", {}, 10000);
    request.abort();
    await assert.rejects(pending, /请求未完成/);
    assert.ok(f.calls[0].options.signal.aborted);
    assert.equal(f.timers.size, 0);
    assert.equal(f.logs.at(-1)[0], "selection_http_cancelled");
});

test("deadline reports timeout and doesn't leak provider details", async () => {
    const f = fixture();
    f.state.pending = true;
    const request = f.createSelectionRequest();
    const pending = request.post("http://localhost", {}, 50000);
    assert.equal([...f.timers.values()][0].delay, 50000);
    [...f.timers.values()][0].callback();
    await assert.rejects(pending, /超时/);
    assert.equal(f.timers.size, 0);
    const other = fixture();
    other.state.fail = true;
    await assert.rejects(
        other.createSelectionRequest().post("http://localhost", {}, 10000),
        (error) => !error.message.includes("secret"),
    );
    assert.doesNotMatch(JSON.stringify(other.logs), /secret/);
});

test("missing Zotero APIs produce a clear local error instead of using a mismatched signal", () => {
    const f = fixture();
    f.state.mainMissing = true;
    assert.throws(() => f.createSelectionRequest(), /当前 Zotero 环境/);
});

test("Codex cancellation sends one independent request with a secure matching identifier", async () => {
    const f = fixture();
    f.state.pending = true;
    const request = f.createSelectionRequest();
    const pending = request.post(
        "http://localhost/translate-text",
        { text: "private-paper" },
        50000,
        { cancelUrl: "http://localhost/cancel-text" },
    );
    const original = JSON.parse(f.calls[0].options.body);
    assert.match(original.requestId, /^[A-Za-z0-9_-]{16,128}$/);
    request.abort();
    request.abort();
    await assert.rejects(pending, /请求未完成/);
    assert.equal(f.calls.length, 2);
    assert.equal(f.calls[1].url, "http://localhost/cancel-text");
    assert.deepEqual(JSON.parse(f.calls[1].options.body), {
        requestId: original.requestId,
    });
    assert.notEqual(f.calls[0].options.signal, f.calls[1].options.signal);
    assert.equal(f.calls[0].options.signal.aborted, true);
    assert.equal(f.calls[1].options.signal.aborted, false);
    assert.equal(f.timers.size, 0);
    assert.doesNotMatch(JSON.stringify(f.logs), /private-paper|requestId/);
});

test("Codex HTTP deadline notifies cancellation and notification failure is best effort", async () => {
    const f = fixture();
    f.state.pending = true;
    f.state.cancelFail = true;
    const request = f.createSelectionRequest();
    const pending = request.post("http://localhost/translate-text", {}, 50000, {
        cancelUrl: "http://localhost/cancel-text",
    });
    [...f.timers.values()].find((timer) => timer.delay === 50000).callback();
    await assert.rejects(pending, /超时/);
    assert.equal(f.calls.length, 2);
    assert.equal(f.timers.size, 0);
});

test("completed requests detach cancellation and legacy requests never acquire identifiers", async () => {
    const f = fixture();
    const request = f.createSelectionRequest();
    await request.post("http://localhost/translate-text", {}, 50000, {
        cancelUrl: "http://localhost/cancel-text",
    });
    await request.post("http://localhost/translation-lookup", {}, 10000);
    request.abort();
    assert.equal(f.calls.length, 2);
    assert.equal("requestId" in JSON.parse(f.calls[1].options.body), false);
    assert.equal(f.timers.size, 0);
});

test("late successful replies cannot turn a cancelled Codex request into success", async () => {
    const f = fixture();
    f.state.pending = true;
    f.state.ignoreAbort = true;
    const request = f.createSelectionRequest();
    const pending = request.post("http://localhost/translate-text", {}, 50000, {
        cancelUrl: "http://localhost/cancel-text",
    });
    request.abort();
    f.state.lateResolve({
        ok: true,
        status: 200,
        json: async () => ({ translation: "late" }),
    });
    await assert.rejects(pending, /请求未完成/);
    assert.equal(f.calls.length, 2);
    assert.equal(f.timers.size, 0);
});

test("Codex request identifiers use getRandomValues when randomUUID is unavailable", async () => {
    const f = fixture();
    f.scope.crypto = {
        getRandomValues: (bytes) => webcrypto.getRandomValues(bytes),
    };
    await f
        .createSelectionRequest()
        .post("http://localhost/translate-text", {}, 50000, {
            cancelUrl: "http://localhost/cancel-text",
        });
    assert.match(
        JSON.parse(f.calls[0].options.body).requestId,
        /^[a-f0-9]{32}$/,
    );
});
