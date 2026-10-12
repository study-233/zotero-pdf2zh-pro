import assert from "node:assert/strict";
import test from "node:test";
import { maintenanceFetch } from "./maintenanceFixture.mjs";

const { Response, AbortController, setTimeout } = globalThis;

test("only explicitly unaccepted maintenance requests can be retried", async () => {
    let count = 0;
    const perform = async (url) => {
        if (url.endsWith("/health"))
            return new Response(
                JSON.stringify({ status: "ok", maintenance: { held: false } }),
            );
        count++;
        if (count === 1)
            return new Response(
                JSON.stringify({ code: "server_maintenance", accepted: false }),
                {
                    status: 503,
                    headers: {
                        "X-PDF2ZH-Maintenance": "not-accepted",
                        "Retry-After": "1",
                    },
                },
            );
        return new Response(JSON.stringify({ accepted: true }), {
            status: 200,
        });
    };
    assert.equal(
        (
            await maintenanceFetch(
                "http://local/tasks",
                { method: "POST", body: "fixed" },
                perform,
            )
        ).status,
        200,
    );
    assert.equal(count, 2);
    for (const response of [
        new Response("failure", { status: 503 }),
        new Response(JSON.stringify({ accepted: true }), {
            status: 503,
            headers: { "X-PDF2ZH-Maintenance": "not-accepted" },
        }),
    ]) {
        let called = 0;
        assert.equal(
            await maintenanceFetch("http://local/tasks", {}, async () => {
                called++;
                return response;
            }),
            response,
        );
        assert.equal(called, 1);
    }
    let called = 0;
    await assert.rejects(
        maintenanceFetch("http://local/tasks", {}, async () => {
            called++;
            throw new Error("lost reply");
        }),
    );
    assert.equal(called, 1);
});

test("port replacement is polled safely without replaying an uncertain submission", async () => {
    let submissions = 0,
        probes = 0;
    const result = await maintenanceFetch(
        "http://local/tasks",
        { method: "POST", body: "same" },
        async (url, options) => {
            if (url.endsWith("/health")) {
                assert.equal(options.method, "GET");
                assert.equal(options.body, undefined);
                probes++;
                if (probes === 1) throw new TypeError("connection refused");
                return new Response(
                    JSON.stringify({
                        status: "ok",
                        maintenance: { held: probes === 2 },
                    }),
                );
            }
            submissions++;
            if (submissions === 1)
                return new Response(
                    JSON.stringify({
                        code: "server_maintenance",
                        accepted: false,
                    }),
                    {
                        status: 503,
                        headers: {
                            "X-PDF2ZH-Maintenance": "not-accepted",
                            "Retry-After": "1",
                        },
                    },
                );
            return new Response(JSON.stringify({ accepted: true }));
        },
    );
    assert.equal(result.status, 200);
    assert.equal(submissions, 2);
    assert.equal(probes, 3);
});

test("cancellation interrupts a maintenance wait without another submission", async () => {
    const controller = new AbortController();
    let count = 0;
    const request = maintenanceFetch(
        "http://local/tasks",
        { signal: controller.signal },
        async () => {
            count++;
            return new Response(
                JSON.stringify({ code: "server_maintenance", accepted: false }),
                {
                    status: 503,
                    headers: { "X-PDF2ZH-Maintenance": "not-accepted" },
                },
            );
        },
    );
    setTimeout(() => controller.abort(), 10);
    await assert.rejects(request, { name: "AbortError" });
    assert.equal(count, 1);
});
