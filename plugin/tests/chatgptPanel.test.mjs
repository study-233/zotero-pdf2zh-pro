import { URL } from "node:url";
import { setImmediate } from "node:timers";
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { JSDOM } from "jsdom";
const markup = fs.readFileSync(
    new URL("../addon/content/llmApiManager.xhtml", import.meta.url),
    "utf8",
);
const source = fs.readFileSync(
    new URL("../addon/content/chatgptPanel.js", import.meta.url),
    "utf8",
);
const tick = () => new Promise((resolve) => setImmediate(resolve));
function fixture(request) {
    const dom = new JSDOM(markup, {
        runScripts: "outside-only",
        contentType: "application/xhtml+xml",
    });
    const { window } = dom;
    window.eval(source);
    const links = [];
    const panel = window.createChatGPTPanel(
        {
            message: (k) => k,
            errorMessage: () => "notice-error-network",
            chatgptAccount: request,
            openChatGPTPage: (u) => links.push(u),
        },
        () => {},
    );
    return {
        window,
        panel,
        links,
        node: (id) => window.document.getElementById("chatgpt-" + id),
    };
}
const account = {
    authenticated: true,
    plan_usage_authorized: true,
    account_label: "reader",
    account_fingerprint: "a".repeat(64),
};
test("panel switches reject late login responses", async () => {
    let resolve;
    const f = fixture(
        () =>
            new Promise((r) => {
                resolve = r;
            }),
    );
    f.panel.load(true);
    f.panel.load(false);
    resolve({
        account,
        login: { status: "pending", loginUrl: "http://127.0.0.1/start/unused" },
    });
    await tick();
    assert.equal(f.links.length, 0);
    assert.equal(f.panel.accountId(), undefined);
    f.window.close();
});
test("account status and usage link work and logout notice survives refresh", async () => {
    let signedIn = true;
    const f = fixture(async (kind) => {
        if (kind === "logout") {
            signedIn = false;
            return { revocationConfirmed: false };
        }
        if (kind === "status")
            return {
                account: signedIn ? account : { authenticated: false },
                login: { status: "idle" },
            };
        assert.fail(`Unexpected account request: ${kind}`);
    });
    f.panel.load(true);
    await tick();
    assert.equal(f.panel.accountId(), account.account_fingerprint);
    assert.match(f.node("account-status").textContent, /reader/);
    f.node("manage-usage").click();
    assert.deepEqual(f.links, ["https://chatgpt.com/settings/usage"]);
    f.node("logout").click();
    await tick();
    assert.equal(f.panel.accountId(), undefined);
    assert.equal(f.node("notice").textContent, "chatgpt-revocation-unknown");
    f.window.close();
});

test("cancel shows progress, waits for confirmation and enables a new login", async () => {
    let status = "pending",
        poll;
    const f = fixture(async (kind) => {
        if (kind === "login/cancel") {
            status = "cancelling";
            return { login: { status } };
        }
        assert.equal(kind, "status");
        return { account: { authenticated: false }, login: { status } };
    });
    f.window.setTimeout = (callback) => {
        poll = callback;
        return 1;
    };
    f.window.clearTimeout = () => {
        poll = undefined;
    };
    f.panel.load(true);
    await tick();
    f.node("cancel").click();
    await tick();
    assert.equal(f.node("account-status").textContent, "chatgpt-cancelling");
    assert.equal(f.node("cancel").disabled, true);
    assert.equal(f.node("login").disabled, true);
    assert.equal(typeof poll, "function");
    status = "cancelled";
    await poll();
    assert.equal(f.node("account-status").textContent, "chatgpt-cancelled");
    assert.equal(f.node("cancel").hidden, true);
    assert.equal(f.node("login").disabled, false);
    assert.equal(poll, undefined);
    f.window.close();
});

test("failed cancel can be retried without leaving the buttons disabled", async () => {
    const f = fixture(async (kind) => {
        if (kind === "login/cancel") throw new Error("connection interrupted");
        return {
            account: { authenticated: false },
            login: { status: "pending" },
        };
    });
    f.panel.load(true);
    await tick();
    f.node("cancel").click();
    await tick();
    assert.equal(f.node("account-status").textContent, "notice-error-network");
    assert.equal(f.node("cancel").disabled, false);
    assert.equal(f.node("refresh").disabled, false);
    assert.equal(f.node("login").disabled, true);
    f.window.close();
});

test("expired sign-in is explicit, relogin opens authorization and completion clears it", async () => {
    let status = "expired";
    const calls = [];
    const f = fixture(async (kind) => {
        calls.push(kind);
        if (kind === "login") {
            status = "pending";
            return { login: { status } };
        }
        return {
            account: {
                ...account,
                authenticated: status === "completed",
                requires_login: status !== "completed",
            },
            login:
                status === "pending"
                    ? { status, loginUrl: "http://127.0.0.1:1234/start/reauth" }
                    : { status: "idle" },
        };
    });
    f.panel.load(true);
    await tick();
    assert.equal(f.node("account-status").textContent, "chatgpt-expired");
    assert.equal(f.node("account-status").dataset.error, "true");
    assert.equal(f.node("login").textContent, "chatgpt-relogin");
    assert.equal(f.panel.accountId(), undefined);
    f.node("login").click();
    await tick();
    assert.ok(calls.includes("login"));
    assert.equal(f.links[0], "http://127.0.0.1:1234/start/reauth");
    assert.equal(f.node("account-status").textContent, "chatgpt-pending");
    status = "completed";
    await f.panel.refresh();
    assert.match(f.node("account-status").textContent, /reader/);
    assert.equal(f.node("account-status").dataset.error, "false");
    assert.equal(f.node("login").textContent, "chatgpt-login");
    f.window.close();
});

test("expired access with refresh remains quiet; focus picks up a confirmed revocation", async () => {
    let expired = false;
    const f = fixture(async () => ({
        account: {
            ...account,
            expires_in_seconds: 0,
            refresh_available: true,
            authenticated: !expired,
            requires_login: expired,
        },
        login: { status: "idle" },
    }));
    f.panel.load(true);
    await tick();
    assert.match(f.node("account-status").textContent, /reader/);
    assert.equal(f.node("login").textContent, "chatgpt-login");
    assert.equal(f.links.length, 0);
    expired = true;
    f.window.dispatchEvent(new f.window.Event("focus"));
    await tick();
    assert.equal(f.node("account-status").textContent, "chatgpt-expired");
    f.window.close();
});
