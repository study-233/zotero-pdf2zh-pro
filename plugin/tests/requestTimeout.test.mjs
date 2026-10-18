import assert from "node:assert/strict";
import test from "node:test";
import { setImmediate } from "node:timers/promises";
import { diagnosticFetch } from "./maintenanceFixture.mjs";

const { Response, ReadableStream, TextEncoder, AbortController } = globalThis;
const encode = (text) => new TextEncoder().encode(text);

function transport(t, status = 200) {
    let stream,
        signal,
        cancelled = false;
    t.mock.timers.enable({ apis: ["setTimeout"] });
    t.mock.method(globalThis, "fetch", async (_url, options) => {
        signal = options.signal;
        return new Response(
            new ReadableStream({
                start(controller) {
                    stream = controller;
                },
                cancel() {
                    cancelled = true;
                },
            }),
            { status, headers: { "X-Test": "retained" } },
        );
    });
    return {
        push: (text) => stream.enqueue(encode(text)),
        close: () => stream.close(),
        get aborted() {
            return signal?.aborted;
        },
        get cancelled() {
            return cancelled;
        },
    };
}

for (const status of [200, 503]) {
    test(`stalled ${status} response body times out and releases the stream`, async (t) => {
        const stream = transport(t, status);
        const result = diagnosticFetch(
            "http://local/tasks",
            {},
            { totalMs: 100, idleMs: 20 },
        );
        const failed = assert.rejects(result, { name: "TimeoutError" });
        await setImmediate();
        t.mock.timers.tick(21);
        await failed;
        assert.equal(stream.aborted, true);
        assert.equal(stream.cancelled, true);
    });
}

test("body progress resets idle timeout without extending the total deadline", async (t) => {
    const stream = transport(t);
    const result = diagnosticFetch(
        "http://local/result",
        {},
        { totalMs: 50, idleMs: 20 },
    );
    const failed = assert.rejects(result, { name: "TimeoutError" });
    await setImmediate();
    for (let i = 0; i < 3; i++) {
        t.mock.timers.tick(15);
        stream.push("pdf");
        await setImmediate();
        assert.equal(stream.aborted, false);
    }
    t.mock.timers.tick(6);
    await failed;
    assert.equal(stream.cancelled, true);
});

test("a progressing PDF may exceed the JSON deadline and retains exact bytes", async (t) => {
    const stream = transport(t);
    const result = diagnosticFetch(
        "http://local/result",
        {},
        { totalMs: 600000, idleMs: 30000 },
    );
    await setImmediate();
    for (const chunk of ["%PDF", "-content", "-EOF"]) {
        t.mock.timers.tick(20000);
        stream.push(chunk);
        await setImmediate();
        assert.equal(stream.aborted, false);
    }
    stream.close();
    const response = await result;
    assert.equal(response.headers.get("X-Test"), "retained");
    assert.equal(await response.clone().text(), "%PDF-content-EOF");
    assert.deepEqual(
        new Uint8Array(await response.arrayBuffer()),
        encode("%PDF-content-EOF"),
    );
    t.mock.timers.tick(700000);
    assert.equal(
        stream.aborted,
        false,
        "completed requests clean their timers",
    );
});

test("external cancellation remains connected after response headers", async (t) => {
    const stream = transport(t);
    const controller = new AbortController();
    const result = diagnosticFetch("http://local/tasks", {
        signal: controller.signal,
    });
    const failed = assert.rejects(result, { name: "AbortError" });
    await setImmediate();
    controller.abort();
    await failed;
    assert.equal(stream.aborted, true);
    assert.equal(stream.cancelled, true);
});

test("header timeout is bounded even when a transport fails to reject on abort", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    let signal;
    t.mock.method(globalThis, "fetch", async (_url, options) => {
        signal = options.signal;
        return new Promise(() => {});
    });
    const result = diagnosticFetch(
        "http://local/result",
        {},
        { totalMs: 600000, idleMs: 30000 },
    );
    const failed = assert.rejects(result, { name: "TimeoutError" });
    t.mock.timers.tick(10001);
    await failed;
    assert.equal(signal.aborted, true);
});

test("pre-aborted requests never fetch and empty responses remain valid", async (t) => {
    const fetch = t.mock.method(
        globalThis,
        "fetch",
        async () => new Response(null, { status: 204 }),
    );
    const controller = new AbortController();
    controller.abort();
    await assert.rejects(
        diagnosticFetch("http://local", { signal: controller.signal }),
        { name: "AbortError" },
    );
    assert.equal(fetch.mock.callCount(), 0);
    assert.equal((await diagnosticFetch("http://local")).status, 204);
});

test("completed JSON bodies retain HTTP errors and malformed JSON is not hidden", async (t) => {
    t.mock.method(
        globalThis,
        "fetch",
        async () => new Response('{"message":"blocked"}', { status: 503 }),
    );
    const response = await diagnosticFetch("http://local/tasks");
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { message: "blocked" });
    t.mock.method(globalThis, "fetch", async () => new Response("{partial"));
    await assert.rejects(
        (await diagnosticFetch("http://local/tasks")).json(),
        SyntaxError,
    );
});

test("upload header budget permits a slow upload but still enforces the total deadline", async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    let signal;
    t.mock.method(globalThis, "fetch", async (_url, options) => {
        signal = options.signal;
        return new Promise(() => {});
    });
    const pending = diagnosticFetch(
        "http://local/tasks",
        { method: "POST" },
        { totalMs: 600000, idleMs: 30000, headersMs: 600000 },
    );
    const failed = assert.rejects(pending, { name: "TimeoutError" });
    await setImmediate();
    t.mock.timers.tick(30001);
    assert.equal(signal.aborted, false);
    t.mock.timers.tick(570000);
    await failed;
    assert.equal(signal.aborted, true);
});
