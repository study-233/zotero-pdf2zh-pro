import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { URL } from "node:url";

const source = fs
    .readFileSync(
        new URL("../addon/content/taskManager.xhtml", import.meta.url),
        "utf8",
    )
    .match(/<script>([\s\S]*?)<\/script>/)[1];

function fixture() {
    const node = () => ({
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
        document: { addEventListener() {}, createElement: node },
        window: { addEventListener() {} },
    });
    vm.runInContext(source, context);
    vm.runInContext("api = {repairTask() {}};", context);
    return context;
}

const qualitySummary = (changes = {}) => ({
    selected: 10,
    checked: 6,
    passed: 4,
    corrected: 1,
    unchecked: 4,
    failed: 1,
    notSelected: 30,
    requestsUsed: 8,
    requestLimit: 8,
    paragraphLimit: 20,
    ...changes,
});
const task = (changes = {}) => ({
    taskId: "one",
    fileName: "paper.pdf",
    service: "openai",
    outputModes: ["dual"],
    status: "completed",
    source: "local",
    importState: "imported",
    ...changes,
});

test("task cards distinguish checked conclusions, unchecked selections and uncovered body paragraphs", () => {
    const ui = fixture();
    const view = ui.createTaskCardView(
        task({ qualitySummary: qualitySummary() }),
    );
    assert.equal(view.qualitySummary.hidden, false);
    const text = view.qualitySummary.textContent;
    assert.match(text, /已检查 6 段.*通过 4、已修正 1、确认失败 1/);
    assert.match(text, /入选但未核对 4 段/);
    assert.match(text, /已检查 6\/40 段正文，入选 10 段，未入选 30 段/);
    assert.match(text, /最多 20 段，请求 8\/8 次/);
    assert.match(text, /未核对与未入选段落没有语义结论/);
});

test("missing or null summaries hide previous conclusions for old services and retried tasks", () => {
    const ui = fixture();
    for (const quality of [undefined, null]) {
        const view = ui.createTaskCardView(
            task({ qualitySummary: qualitySummary() }),
        );
        ui.updateTaskCardView(view, task({ qualitySummary: quality }));
        assert.equal(view.qualitySummary.hidden, true);
        assert.equal(view.qualitySummary.textContent, "");
        assert.equal(view.status.textContent, "已完成");
    }
});

test("unknown review results keep completed PDF status without claiming semantic success", () => {
    const ui = fixture();
    const view = ui.createTaskCardView(
        task({
            qualitySummary: qualitySummary({
                checked: 0,
                passed: 0,
                corrected: 0,
                failed: 0,
                unchecked: 10,
            }),
        }),
    );
    assert.equal(view.status.textContent, "已完成");
    assert.match(view.qualitySummary.textContent, /入选但未核对 10 段/);
    assert.match(view.qualitySummary.textContent, /已检查 0\/40 段正文/);
    assert.doesNotMatch(
        view.qualitySummary.textContent,
        /全部正确|全部通过|全文通过/,
    );
    ui.updateTaskCardView(
        view,
        task({ status: "incomplete", qualitySummary: qualitySummary() }),
    );
    assert.equal(view.status.textContent, "未完成，校对确认 1 段有问题");
});

test("quality-only updates invalidate the task card render signature", () => {
    const ui = fixture();
    const initial = task({ qualitySummary: qualitySummary() });
    const updated = task({
        qualitySummary: qualitySummary({
            checked: 7,
            corrected: 2,
            unchecked: 3,
        }),
    });
    assert.notEqual(
        ui.getTaskRenderSignature(initial),
        ui.getTaskRenderSignature(updated),
    );
    const view = ui.createTaskCardView(initial);
    ui.updateTaskCardView(view, updated);
    assert.match(view.qualitySummary.textContent, /已检查 7 段.*已修正 2/);
});

test("unknown token counts remain unknown in task metric details", () => {
    const ui = fixture();
    const view = ui.createTaskCardView(
        task({
            metrics: {
                tokens: { input: null, output: null, total: null },
            },
        }),
    );
    assert.equal(view.detailValues[7].textContent, "- / -");
    assert.equal(ui.formatInteger(undefined), "-");
    assert.equal(ui.formatInteger(0), "0");
});

test("metric details separate initialization and review without adding their totals twice", () => {
    const ui = fixture();
    const view = ui.createTaskCardView(
        task({
            metrics: {
                requests: {
                    attempts: 20,
                    retries: 1,
                    byKind: {
                        translation: { attempts: 15 },
                        review: { attempts: 3 },
                        initialization: { attempts: 2 },
                    },
                },
                tokens: {
                    input: 1000,
                    output: 400,
                    total: 1400,
                    reasoning: 200,
                    reasoningAvailability: "complete",
                    byKind: {
                        initialization: { input: 30, output: 4, total: 34 },
                    },
                },
            },
        }),
    );
    assert.equal(view.summaryMetrics[3].value.textContent, "20 / 1");
    assert.equal(view.detailValues[7].textContent, "1,000 / 400");
    assert.equal(view.metricNote, undefined);
    assert.doesNotMatch(
        source,
        /metric-note|formatUsageNote|初始化探测 Token|输出 Token 的子集/,
    );
});

test("missing metric values remain unknown and activity uses one concise stage label", () => {
    const ui = fixture();
    const view = ui.createTaskCardView(
        task({ metrics: { tokens: { input: null, output: null } } }),
    );
    assert.equal(view.detailValues[7].textContent, "- / -");
    assert.equal(view.metricNote, undefined);
    const running = task({
        status: "running",
        stage: "Translate Paragraphs",
        metrics: { activity: { oldestRequestSeconds: 15 } },
    });
    assert.equal(ui.formatActivityStage(running), "等待响应");
    running.metrics.activity.retrying = 1;
    assert.equal(ui.formatActivityStage(running), "重试等待");
    running.metrics.activity.fallbackPending = 1;
    assert.equal(ui.formatActivityStage(running), "拆段处理");
    delete running.metrics.activity;
    assert.equal(
        ui.formatActivityStage(running),
        ui.formatStage(running.stage),
    );
});

test("partial PDF availability and import failure are separate from repair", () => {
    const ui = fixture();
    const partial = task({
        status: "incomplete",
        canDownloadResult: true,
        canRepair: true,
        translationSummary: { failed: 4, pending: 2 },
    });
    const view = ui.createTaskCardView(partial);
    assert.match(view.status.textContent, /PDF 已导入.*剩余 6 段.*可补译/);
    ui.updateTaskCardView(view, { ...partial, importState: "failed" });
    assert.match(view.status.textContent, /PDF 已生成/);
    assert.equal(view.retryButton.textContent, "重试导入");
    assert.equal(view.retryButton.hidden, false);
    assert.equal(view.repairButton.hidden, false);
});
