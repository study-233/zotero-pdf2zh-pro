import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";
import { noticeModules } from "./noticeFixture.mjs";

const { errorNotice, NoticeError, renderNotice } =
    noticeModules()("./userNotice");
const t = (key) => key;

test("structured failures take priority and unknown raw text never reaches the UI", () => {
    const original = {
        code: "llm_auth",
        message: "InternalServerError secret-key",
        diagnostics: [{ code: "network_connectivity" }],
    };
    const before = JSON.stringify(original);
    assert.equal(errorNotice(original, t).text, "notice-error-auth");
    assert.equal(
        errorNotice(new NoticeError("no_profile"), t).action,
        "profiles",
    );
    assert.equal(
        errorNotice(new Error("secret document body and credentials"), t).text,
        "notice-error-unknown",
    );
    assert.equal(
        errorNotice({ diagnostics: "bad" }, t).text,
        "notice-error-unknown",
    );
    assert.equal(JSON.stringify(original), before);
});

test("older service text distinguishes transient network failure from expired login", () => {
    for (const [message, key] of [
        ["暂时无法续期 ChatGPT 授权，请检查网络", "network"],
        ["ChatGPT 登录已失效", "login"],
        ["APIConnectionError: failed", "network"],
        ["请升级服务端后重试", "upgrade"],
        ["TimeoutError", "timeout"],
    ])
        assert.equal(
            errorNotice(new Error(message), t).text,
            `notice-error-${key}`,
        );
});

function surface() {
    const dom = new JSDOM('<div id="notice"></div>');
    const { window } = dom;
    const callbacks = new Map();
    let next = 0;
    window.setTimeout = (fn) => {
        callbacks.set(++next, fn);
        return next;
    };
    window.clearTimeout = (id) => callbacks.delete(id);
    return {
        window,
        node: window.document.getElementById("notice"),
        callbacks,
    };
}

test("a success restores its surface after four seconds; newer errors cannot be erased", () => {
    const f = surface();
    let restored = 0;
    renderNotice(
        f.node,
        { id: "copy", level: "success", text: "Copied" },
        undefined,
        () => restored++,
    );
    const stale = [...f.callbacks.values()][0];
    assert.equal(f.callbacks.size, 1);
    renderNotice(f.node, { id: "failure", level: "error", text: "Retry" });
    assert.equal(f.callbacks.size, 0);
    stale();
    assert.equal(f.node.textContent, "Retry");
    assert.equal(f.node.getAttribute("role"), "alert");
    assert.equal(restored, 0);
    renderNotice(
        f.node,
        { id: "copy", level: "success", text: "Copied" },
        undefined,
        () => restored++,
    );
    [...f.callbacks.values()][0]();
    assert.equal(restored, 1);
    assert.equal(f.node.hidden, true);
    f.window.close();
});

test("unchanged polling preserves buttons, focus and text; shutdown cancels timers", () => {
    const f = surface();
    const actions = [];
    const notice = {
        id: "profile",
        level: "error",
        text: "Choose a profile",
        action: "profiles",
        actionLabel: "Configure",
    };
    renderNotice(f.node, notice, (action) => actions.push(action));
    const button = f.node.querySelector("button");
    button.focus();
    renderNotice(f.node, { ...notice }, () => {});
    assert.equal(f.node.querySelector("button"), button);
    assert.equal(f.window.document.activeElement, button);
    button.click();
    assert.deepEqual(actions, ["profiles"]);
    renderNotice(f.node, { id: "saved", level: "success", text: "Saved" });
    f.window.dispatchEvent(new f.window.Event("unload"));
    assert.equal(f.callbacks.size, 0);
    f.window.close();
});
