import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import { setImmediate } from "node:timers";
import ts from "typescript";
import { JSDOM } from "jsdom";
const read = (name) => fs.readFileSync(new URL(name, import.meta.url), "utf8");
const tick = () => new Promise((resolve) => setImmediate(resolve));
function load(name, imports = {}) {
    const exports = {};
    const code = ts.transpileModule(read(`../src/modules/${name}.ts`), {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    }).outputText;
    new Function("require", "exports", code)((key) => imports[key], exports);
    return exports;
}
const catalog = load("llmApiManager");

test("relogin opens the existing ChatGPT draft, keeps unrelated edits and never saves automatically", async () => {
    const dom = new JSDOM(read("../addon/content/llmApiManager.xhtml"), {
        runScripts: "outside-only",
        contentType: "application/xhtml+xml",
    });
    const { window } = dom;
    const profiles = [
        {
            key: "api",
            name: "API",
            service: "openai",
            providerPreset: "custom",
            apiUrl: "https://example.invalid/v1",
            apiKey: "",
            model: "m",
        },
        {
            key: "plan",
            name: "Plan",
            service: "chatgpt",
            providerPreset: "chatgpt",
            apiUrl: "",
            apiKey: "",
            model: "m",
        },
    ];
    let calls = 0,
        saves = 0,
        expired = false;
    const links = [];
    window.arguments = [
        {
            presets: catalog.PROVIDER_PRESETS,
            resolvePreset: (api) => catalog.resolveProviderPreset(api)?.id,
            message: (key) => key,
            errorMessage: () => "登录已失效",
            list: () => profiles,
            current: () => "api",
            save: () => {
                saves++;
            },
            test: async () => {
                expired = true;
                throw new Error("ChatGPT 登录已失效，请重新登录。");
            },
            chatgptAccount: async (kind) => {
                if (kind === "login") {
                    calls++;
                    return { login: { status: "starting" } };
                }
                return {
                    account: {
                        authenticated: !expired,
                        requires_login: expired,
                        plan_usage_authorized: true,
                        account_fingerprint: "a".repeat(64),
                        account_label: "reader",
                    },
                    login: calls
                        ? {
                              status: "pending",
                              loginUrl: "http://127.0.0.1:1234/start/reauth",
                          }
                        : { status: "idle" },
                };
            },
            openChatGPTPage: (url) => links.push(url),
        },
    ];
    for (const file of ["chatgptPanel", "profileEditor", "profileManager"])
        window.eval(read(`../addon/content/${file}.js`));
    const $ = (id) => window.document.getElementById(id);
    $("name").value = "Unsaved API name";
    $("name").dispatchEvent(new window.Event("input"));
    window.document.querySelector('[data-key="plan"]').click();
    await tick();
    $("test").click();
    await tick();
    assert.equal($("chatgpt-account-status").textContent, "chatgpt-expired");
    assert.equal($("chatgpt-login").textContent, "chatgpt-relogin");
    assert.match($("status").textContent, /登录已失效/);
    window.document.querySelector('[data-key="api"]').click();
    window.dispatchEvent(new window.Event("chatgpt-relogin"));
    await tick();
    assert.equal($("name").value, "Plan");
    assert.equal(calls, 1);
    assert.equal(links.length, 1);
    assert.equal($("chatgpt-account-status").textContent, "chatgpt-pending");
    window.document.querySelector('[data-key="api"]').click();
    assert.equal($("name").value, "Unsaved API name");
    assert.equal(saves, 0);
    dom.window.close();
});

test("selection auth errors and partial results offer relogin; success and network errors do not", () => {
    const dom = new JSDOM(
        '<body><button id="anchor">selection</button></body>',
        { pretendToBeVisual: true },
    );
    const imports = {
        "../utils/locale": { getString: (key) => key },
        "./selectionAudio": {
            stopSelectionAudio() {},
            preloadSelectionAudio: () => () => {},
            playSelectionAudio() {},
        },
        "./userNotice": load("userNotice"),
        "./selectionFormatting": load("selectionFormatting"),
        "./selectionUI": load("selectionUI"),
    };
    const { createSelectionPopup } = load("selectionPopup", imports);
    const doc = dom.window.document;
    const popup = createSelectionPopup(
        doc,
        doc.getElementById("anchor"),
        "source",
        "translate",
        () => {},
    );
    let clicked = 0;
    popup.setControls({
        original: "source",
        dictionary: "offline",
        translation: "profile",
        models: [],
        onDictionary() {},
        onTranslation() {},
        onSubmit() {},
        onReloginChatGPT: () => {
            clicked++;
        },
    });
    const login = () =>
        [...doc.querySelectorAll("button")].find(
            (node) => node.textContent === "profile-chatgpt-relogin",
        );
    popup.render({ kind: "error", text: "ChatGPT 登录已失效，请重新登录。" });
    assert.ok(login());
    login().click();
    assert.equal(clicked, 1);
    popup.render({
        kind: "translation",
        text: "部分译文",
        origin: "AI",
        notice: "ChatGPT 登录已失效，请重新登录。",
        incomplete: true,
    });
    assert.ok(login());
    assert.match(popup.card.textContent, /部分译文/);
    popup.render({
        kind: "error",
        text: "暂时无法续期 ChatGPT 授权，请检查网络后重试。",
    });
    assert.equal(login(), undefined);
    popup.render({ kind: "translation", text: "译文", origin: "AI" });
    assert.equal(login(), undefined);
    popup.setLearning({
        refreshLabel: "重试",
        onRefresh() {},
        error: "ChatGPT 登录已失效，请重新登录。",
        context: { title: "语境", onAction() {} },
    });
    assert.ok(login());
    popup.destroy();
    dom.window.close();
});

test("stream auth errors retain an actionable login message", () => {
    const { createSelectionStreamParser } = load("selectionStream");
    const parser = createSelectionStreamParser("current", () => {});
    parser.feed('event: start\ndata: {"requestId":"current","seq":1}\n\n');
    assert.throws(
        () =>
            parser.feed(
                'event: error\ndata: {"requestId":"current","seq":2,"code":"chatgpt_sign_in_required","message":"旧服务提示"}\n\n',
            ),
        /ChatGPT 登录已失效，请重新登录。/,
    );
});
