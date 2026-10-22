// Offline preview of the real task window. No service, model or Zotero writes.
// node scripts/preview_task_ui.cjs [port]
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const root = path.resolve(__dirname, "..");
const report = require("./workspace.cjs").newReport("task-ui");
const port = Number(process.argv[2] || 8892);

function preview(locale, scenario) {
    const messages = Object.fromEntries(
        [...fs.readFileSync(path.join(root, `plugin/addon/locale/${locale}/addon.ftl`), "utf8")
            .matchAll(/^([\w-]+) = (.+)$/gm)].map(match => [match[1], match[2]]),
    );
    const base = {
        taskId: "preview-running", source: "local", service: "openai", outputModes: ["dual"],
        fileName: "2026_A surface-adaptive aerial projection interface for human–drone interaction.pdf",
        status: "running", canCancel: true, stage: "Translate Paragraphs", stageProgress: 54, stageCurrent: 93,
        stageTotal: 171, overallProgress: 64, importState: "pending", attempt: 1,
        createdAt: "2026-10-21T15:26:22Z", updatedAt: "2026-10-21T15:26:53Z",
        timing: { totalSeconds: 144, queueSeconds: 0.005, executionSeconds: 144, complete: true },
        metrics: {
            tokens: { input: 17600, output: 11400 }, requests: { active: 8, attempts: 25,
                retries: 0, averageLatencyMs: 5900, p95LatencyMs: 7700, qps10s: 2.5 },
            providerCache: { hitRate: 0 }, localCache: { hitRate: 0 }, referencesSkipped: 4,
            activity: { oldestRequestSeconds: 5 },
            throughput: { paragraphsPerMinute: 177.1, etaSeconds: 8, outputTokensPerSecond: 123.4 },
        },
    };
    let tasks = [base];
    if (scenario === "empty") tasks = [];
    if (scenario === "missing") base.metrics = { tokens: { input: null, output: null } };
    if (scenario === "review") {
        delete base.metrics;
        Object.assign(base, { status: "incomplete", importState: "imported", canDownloadResult: true,
            canRepair: true, stage: "Semantic Review", qualitySummary: {
                selected: 10, notSelected: 30, checked: 6, passed: 4, corrected: 1, failed: 1,
                unchecked: 4, paragraphLimit: 20, requestsUsed: 8, requestLimit: 8,
            } });
    }
    if (["mixed", "history"].includes(scenario)) tasks = [base,
        { ...base, taskId: "preview-failed", fileName: "A second paper.pdf", status: "failed",
            error: "APIConnectionError", canCancel: false },
        { ...base, taskId: "preview-completed", fileName: "A completed paper.pdf", status: "completed",
            stage: "completed", overallProgress: 100, stageProgress: 100, importState: "imported",
            canDownloadResult: true, canRepair: true },
        { ...base, taskId: "preview-paused", fileName: "An interrupted paper.pdf", status: "paused",
            canResume: true, recoveryState: "interrupted", canCancel: true },
    ];
    return { locale, scenario, tasks, messages };
}

function bootstrap(data) {
    let tasks = data.tasks;
    let changed = () => {};
    let historyPage = 1;
    const update = (id, patch) => {
        tasks = tasks.map(task => task.taskId === id ? { ...task, ...patch } : task);
        changed();
    };
    window.arguments = [{
        locale: data.locale,
        message: (key, options) => (data.messages[key] || key).replace(
            /\{\s*\$(\w+)\s*\}/g, (_, name) => options?.args?.[name] ?? "?"),
        getTasks: () => tasks,
        hasFailedTasks: () => tasks.some(task => task.status === "failed"),
        refreshTasks: async () => {},
        getEventStreamState: () => ({ total: 1, connected: 1 }),
        getHistoryPage: () => ({ supported: true, total: data.scenario === "history" ? 62 : 0,
            page: historyPage, hasMore: historyPage < 3, loading: false }),
        changeHistoryPage: async direction => { historyPage += direction; },
        onTasksChanged: listener => { changed = listener; return () => { changed = () => {}; }; },
        resumeTasks: async () => tasks.filter(task => task.canResume)
            .forEach(task => update(task.taskId, { status: "queued", canResume: false })),
        resumeTask: async id => update(id, { status: "queued", canResume: false }),
        retryFailedTasks: async () => tasks.filter(task => task.status === "failed")
            .forEach(task => update(task.taskId, { status: "queued", error: "" })),
        retryTask: async id => update(id, { status: "queued", error: "" }),
        repairTask: async id => update(id, { status: "running", canCancel: true, stage: "Translate Paragraphs" }),
        cancelTask: async id => update(id, { status: "cancelled", importState: "none", canCancel: false }),
        clearFailedTasks: async () => { tasks = tasks.filter(task => task.status !== "failed"); changed(); },
        deleteTask: async id => { tasks = tasks.filter(task => task.taskId !== id); changed(); },
        exportDiagnostics: async () => {},
    }];
}

const server = http.createServer((request, response) => {
    const url = new URL(request.url, `http://127.0.0.1:${port}`);
    if (url.pathname === "/favicon.ico") { response.writeHead(204); return response.end(); }
    if (url.pathname !== "/task.xhtml") { response.writeHead(404); return response.end(); }
    const locale = url.searchParams.get("locale") === "en-US" ? "en-US" : "zh-CN";
    const data = preview(locale, url.searchParams.get("scenario") || "active");
    const theme = url.searchParams.get("theme") === "light" ? "not all" : "all";
    const script = `<script><![CDATA[(${bootstrap.toString()})(${JSON.stringify(data)});]]></script>`;
    const markup = fs.readFileSync(path.join(root, "plugin/addon/content/taskManager.xhtml"), "utf8")
        .replace(/<\?xml-stylesheet[\s\S]*?\?>/g, "")
        .replace("@media (prefers-color-scheme: dark)", `@media ${theme}`)
        .replace("<head>", "<head>" + script);
    response.writeHead(200, { "Content-Type": "application/xhtml+xml; charset=utf-8", "Cache-Control": "no-store" });
    response.end(markup);
});
server.listen(port, "127.0.0.1", () => console.log(`Offline task preview: http://127.0.0.1:${port}/task.xhtml`));
for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, () => server.close(() => process.exit(0)));
server.on("error", error => { console.error(error); process.exitCode = 1; });
