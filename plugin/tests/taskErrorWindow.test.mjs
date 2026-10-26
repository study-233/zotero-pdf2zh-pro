import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { taskWindowMessage } from "./helpers/taskWindowLocale.mjs";
import { taskWindowScript } from "./helpers/taskWindowScript.mjs";

const source = taskWindowScript();

function fixture() {
    const node = (tagName) => ({
        tagName,
        textContent: "",
        className: "",
        hidden: false,
        dataset: {},
        style: {},
        attributes: new Map(),
        children: [],
        appendChild(child) {
            this.children.push(child);
        },
        addEventListener() {},
        setAttribute(key, value) {
            this.attributes.set(key, value);
        },
        getAttribute(key) {
            return this.attributes.get(key);
        },
    });
    const context = vm.createContext({
        message: taskWindowMessage(),
        document: { addEventListener() {}, createElement: node },
        window: { addEventListener() {} },
    });
    vm.runInContext(source, context);
    vm.runInContext("api = {repairTask() {}, message};", context);
    return context;
}

const task = (changes = {}) => ({
    taskId: "one",
    fileName: "paper.pdf",
    service: "openai",
    outputModes: ["dual"],
    status: "incomplete",
    source: "local",
    importState: "none",
    ...changes,
});
const paragraphs = Array.from({ length: 27 }, (_, i) => ({
    page: i < 2 ? 3 : 4 + (i % 3),
    paragraphId: `paragraph-${i}`,
    attempts: i < 2 ? 2 : 3,
    errorType: i < 2 ? "InternalServerError" : "APIConnectionError",
    reason:
        i < 2
            ? "InternalServerError → HTTPStatusError"
            : "APIConnectionError → ConnectError → ConnectError → OSError",
}));

const visibleText = (node) =>
    [node.textContent, ...node.children.map(visibleText)].join(" ");

test("failure summary contains no details control or technical records and preserves source data", () => {
    const ui = fixture();
    const initial = task({
        failedParagraphs: paragraphs,
        error: "InternalServerError",
        errorDiagnostics: [{ message: "Technical diagnostic", code: "E_TEST" }],
        canRepair: true,
    });
    const original = JSON.stringify(initial);
    const view = ui.createTaskCardView(initial);
    assert.equal(
        view.error.textContent,
        "翻译失败：无法连接翻译服务；翻译服务异常。",
    );
    assert.equal(view.error.hidden, false);
    assert.equal(view.error.tagName, "div");
    assert.equal(view.error.children.length, 0);
    const text = visibleText(view.card);
    assert.doesNotMatch(
        text,
        /查看详情|错误详情|paragraph-0|InternalServerError|Technical diagnostic|E_TEST/,
    );
    assert.match(text, /导出诊断包/);
    assert.equal(view.repairButton.hidden, false);
    assert.equal(JSON.stringify(initial), original);
});

test("refresh updates the reason and completed repair removes the notice", () => {
    const ui = fixture();
    const initial = task({ failedParagraphs: paragraphs });
    const view = ui.createTaskCardView(initial);
    const notice = view.error;
    ui.updateTaskCardView(view, {
        ...initial,
        failedParagraphs: paragraphs.slice(0, 1),
        updatedAt: "2026-09-29T12:00:00Z",
    });
    assert.equal(view.error, notice);
    assert.equal(view.error.textContent, "翻译失败：翻译服务异常。");
    ui.updateTaskCardView(view, task({ status: "completed" }));
    assert.equal(view.error.hidden, true);
    assert.equal(view.error.textContent, "");
    ui.updateTaskCardView(view, initial);
    assert.equal(view.error.hidden, false);
    assert.equal(
        view.error.textContent,
        "翻译失败：无法连接翻译服务；翻译服务异常。",
    );
});

test("task and import failures show only a short message", () => {
    const ui = fixture();
    assert.equal(
        ui.getErrorSummary(task({ error: "UnknownProviderFailure" })),
        "翻译失败，原因未知。",
    );
    assert.equal(
        ui.getErrorSummary(task({ status: "failed" })),
        "翻译失败，原因未知。",
    );
    const view = ui.createTaskCardView(
        task({
            importState: "failed",
            importError: "Import failure details",
            error: "Translation failure details",
            failedParagraphs: paragraphs,
        }),
    );
    assert.equal(view.error.textContent, "译文导入失败，原因未知。");
    assert.doesNotMatch(
        visibleText(view.card),
        /Import failure details|Translation failure details|paragraph-/,
    );
});

test("summary supports old services and omits diagnostic-only noise", () => {
    const ui = fixture();
    assert.equal(ui.getErrorSummary(task()), "");
    assert.equal(
        ui.getErrorSummary(
            task({
                translationSummary: { failed: 30 },
                failedParagraphs: paragraphs,
            }),
        ),
        "翻译失败：无法连接翻译服务；翻译服务异常。",
    );
    const view = ui.createTaskCardView(
        task({
            status: "completed",
            errorDiagnostics: [{ severity: "warning", message: "Warning" }],
        }),
    );
    assert.equal(view.error.hidden, true);
    assert.doesNotMatch(visibleText(view.card), /Warning|任务提示/);
});

test("common failure causes use plain Chinese and specific causes override wrapper errors", () => {
    const ui = fixture();
    for (const [error, reason] of [
        ["APIConnectionError → ConnectTimeout", "翻译服务响应超时"],
        ["InternalServerError → HTTPStatusError", "翻译服务异常"],
        ["AuthenticationError", "翻译服务的密钥验证失败"],
        ["PermissionDeniedError", "没有调用翻译服务的权限"],
        ["RateLimitError", "翻译请求受限，请检查频率或额度"],
        ["insufficient_quota RateLimitError", "翻译服务额度不足"],
        ["InvalidTranslation semantic_error_unresolved", "部分译文校对未通过"],
        ["InvalidTranslation", "翻译服务返回的译文不符合要求"],
    ]) {
        assert.equal(
            ui.getErrorSummary(task({ error })),
            `翻译失败：${reason}。`,
        );
    }
    assert.equal(
        ui.getErrorSummary(task({ error: "OSError: unknown" })),
        "翻译失败，原因未知。",
    );
    assert.equal(
        ui.getErrorSummary(task({ error: "HTTPStatusError" })),
        "翻译失败，原因未知。",
    );
});

test("structured diagnostics clarify failures without showing raw messages", () => {
    const ui = fixture();
    for (const [code, message, reason] of [
        ["llm_model", "model details", "所选模型不可用"],
        [
            "network_connectivity",
            "API 端点未在超时期限内响应。",
            "翻译服务响应超时",
        ],
        ["network_connectivity", "connection details", "无法连接翻译服务"],
        ["font_asset_download", "font details", "所需字体下载失败"],
    ]) {
        assert.equal(
            ui.getErrorSummary(
                task({
                    status: "failed",
                    errorDiagnostics: [{ code, message }],
                }),
            ),
            `翻译失败：${reason}。`,
        );
    }
    const result = ui.getErrorSummary(
        task({
            failedParagraphs: [
                { errorType: "APIConnectionError" },
                { errorType: "InternalServerError" },
                { errorType: "AuthenticationError" },
            ],
        }),
    );
    assert.equal(result, "翻译失败：无法连接翻译服务；翻译服务异常等问题。");
});

test("ChatGPT failures show actionable reasons and relogin only for invalid authorization", () => {
    const ui = fixture();
    for (const failure of [
        { error: "ChatGPT 登录已失效，请重新登录。" },
        { error: "ChatGPT 授权无法续期，请重新登录。" },
        { errorDiagnostics: [{ code: "chatgpt_sign_in_required" }] },
        { failedParagraphs: [{ providerCode: "chatgpt_sign_in_required" }] },
    ]) {
        const view = ui.createTaskCardView(
            task({ service: "chatgpt", status: "failed", ...failure }),
        );
        assert.equal(
            view.error.textContent,
            "ChatGPT 登录已失效，请重新登录。",
        );
        assert.equal(view.reloginButton.hidden, false);
        assert.equal(view.reloginButton.textContent, "重新登录");
        ui.updateTaskCardView(view, task({ status: "completed" }));
        assert.equal(view.reloginButton.hidden, true);
    }
    for (const message of [
        "暂时无法续期 ChatGPT 授权，请检查网络后重试。",
        "ChatGPT 套餐或此应用的用量已达上限，请在 ChatGPT 用量设置中查看。",
    ]) {
        const view = ui.createTaskCardView(
            task({ service: "chatgpt", status: "failed", error: message }),
        );
        assert.equal(
            view.error.textContent,
            message.includes("套餐")
                ? "ChatGPT 用量已达上限，请查看账户用量。"
                : "ChatGPT 授权续期失败，请检查网络后重试。",
        );
        assert.equal(view.reloginButton.hidden, true);
    }
});
