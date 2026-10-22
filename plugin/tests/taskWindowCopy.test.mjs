import assert from "node:assert/strict";
import fs from "node:fs";
import { URL } from "node:url";
import test from "node:test";
import { JSDOM } from "jsdom";
import { taskWindowMessage } from "./helpers/taskWindowLocale.mjs";

const markup = fs.readFileSync(
    new URL("../addon/content/taskManager.xhtml", import.meta.url),
    "utf8",
);

const task = (changes = {}) => ({
    taskId: "preview",
    fileName: "A long paper title.pdf",
    service: "openai",
    outputModes: ["dual"],
    source: "local",
    status: "running",
    stage: "Translate Paragraphs",
    importState: "pending",
    ...changes,
});

async function fixture(locale, tasks = []) {
    let changed;
    const calls = [];
    const api = {
        locale,
        message: taskWindowMessage(locale),
        getTasks: () => tasks,
        getHistoryPage: () => ({ supported: true, total: 0, page: 1 }),
        getEventStreamState: () => ({ total: 2, connected: 1 }),
        onTasksChanged: (listener) => {
            changed = listener;
            return () => {};
        },
        refreshTasks: async () => {},
        retryFailedTasks: async () => calls.push("retry-page"),
        clearFailedTasks: async () => calls.push("clear-failed"),
    };
    const dom = new JSDOM(markup, {
        contentType: "application/xhtml+xml",
        runScripts: "dangerously",
        pretendToBeVisual: true,
        beforeParse(window) {
            window.arguments = [api];
        },
    });
    await new Promise((resolve) =>
        dom.window.addEventListener("DOMContentLoaded", resolve, {
            once: true,
        }),
    );
    await new Promise((resolve) => dom.window.requestAnimationFrame(resolve));
    return { dom, document: dom.window.document, calls, update: changed };
}

for (const locale of ["zh-CN", "en-US"]) {
    test(`${locale}: real XHTML renders concise localized copy and preserves operation scope`, async () => {
        const { dom, document, calls } = await fixture(locale, [
            task({ status: "failed" }),
        ]);
        try {
            assert.equal(document.documentElement.lang, locale);
            assert.equal(document.querySelectorAll(".card").length, 1);
            assert.equal(
                document.getElementById("filter-count-failed").textContent,
                "1",
            );
            assert.equal(document.getElementById("history-pager").hidden, true);
            assert.equal(
                document.getElementById("connection-label").textContent,
                locale === "zh-CN" ? "连接中" : "Connecting",
            );
            assert.match(
                document.getElementById("connection-status").title,
                /1\/2/,
            );
            assert.doesNotMatch(
                document.querySelector(".page").textContent,
                /task-ui-|已显示任务|跟踪翻译进度/,
            );
            if (locale === "en-US")
                assert.doesNotMatch(
                    document.querySelector(".page").textContent,
                    /[\p{Script=Han}]/u,
                );
            document.getElementById("retry-all-failed-btn").click();
            document.getElementById("clear-failed-btn").click();
            await new Promise((resolve) => dom.window.setTimeout(resolve, 40));
            assert.deepEqual(calls, ["retry-page", "clear-failed"]);
        } finally {
            dom.window.close();
        }
    });

    test(`${locale}: review details remain available without request metrics`, async () => {
        const { dom, document, update } = await fixture(locale, [
            task({
                qualitySummary: {
                    selected: 3,
                    notSelected: 7,
                    checked: 2,
                    passed: 1,
                    corrected: 1,
                    failed: 0,
                    unchecked: 1,
                    paragraphLimit: 3,
                    requestsUsed: 2,
                    requestLimit: 4,
                },
            }),
        ]);
        try {
            const toggle = document.querySelector(".metrics-toggle");
            const rail = document.querySelector(".metric-rail");
            assert.equal(rail.tabIndex, 0);
            assert.equal(toggle.hidden, false);
            assert.equal(toggle.getAttribute("aria-expanded"), "false");
            toggle.click();
            assert.equal(
                document.querySelector(".metrics-panel").hidden,
                false,
            );
            assert.equal(
                document.querySelector(".observability-detail-grid").hidden,
                true,
            );
            assert.match(
                document.querySelector(".review-detail").textContent,
                /2\/10/,
            );
            update();
            await new Promise((resolve) =>
                dom.window.requestAnimationFrame(resolve),
            );
            assert.equal(toggle.getAttribute("aria-expanded"), "true");
            toggle.click();
            assert.equal(document.querySelector(".metrics-panel").hidden, true);
        } finally {
            dom.window.close();
        }
    });
}

test("cleanup uses all loaded service records rather than the active page or failure filter", async () => {
    const { dom, document } = await fixture("zh-CN", [
        task({ status: "incomplete" }),
    ]);
    try {
        assert.equal(
            document.getElementById("clear-failed-btn").disabled,
            true,
        );
        dom.window.arguments[0].hasFailedTasks = () => true;
        dom.window.renderFilterCounts([]);
        assert.equal(
            document.getElementById("clear-failed-btn").disabled,
            false,
        );
        dom.window.arguments[0].hasFailedTasks = () => false;
        dom.window.renderFilterCounts([]);
        assert.equal(
            document.getElementById("clear-failed-btn").disabled,
            true,
        );
    } finally {
        dom.window.close();
    }
});

for (const locale of ["zh-CN", "en-US"]) {
    test(`${locale}: timing and speed stay in the rail, details preserve breakdown`, async () => {
        const { dom, document } = await fixture(locale, [
            task({
                timing: {
                    totalSeconds: 66,
                    queueSeconds: 0.007,
                    executionSeconds: 65.993,
                    complete: true,
                },
                metrics: { throughput: { outputTokensPerSecond: 123.4 } },
            }),
        ]);
        try {
            const rail = document.querySelector(".metric-rail");
            assert.match(rail.textContent, /1.1 min/);
            assert.match(rail.textContent, /123.4 tokens\/s/);
            assert.doesNotMatch(rail.textContent, /排队|执行|Queue|Execution/);
            assert.equal(document.querySelector(".metrics-panel").hidden, true);
            document.querySelector(".metrics-toggle").click();
            assert.match(
                document.querySelector(".observability-detail-grid")
                    .textContent,
                /7 ms/,
            );
            assert.equal(
                document.querySelector(".metric-rail").getAttribute("tabindex"),
                "0",
            );
        } finally {
            dom.window.close();
        }
    });
}
