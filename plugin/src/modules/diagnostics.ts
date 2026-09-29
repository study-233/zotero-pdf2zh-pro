import { config, version } from "../../package.json";

const MAX_PACKAGE = 20 * 1024 * 1024;
const MAX_LOG = 2 * 1024 * 1024;
const numeric = new Set(
    "schemaVersion averageLatencyMs p95LatencyMs qps10s stageElapsedSeconds promptTokens completionTokens totalTokens succeeded failed pending translated attempt attempts revision stageCurrent stageTotal stageProgress overallProgress currentPage selectedPage completedPages totalPages fontParseCount fontCacheHits fontObject operations idleSeconds latencyMs statusCode input output total active retries count pid cpuPercent rssBytes parentCpuPercent parentRssBytes exitCode elapsedSeconds droppedLogRecords line qps poolSize page skipped selected checked passed corrected unchecked notSelected requestsUsed requestLimit paragraphLimit hits misses hitRate hitTokens missTokens reasoning visibleOutputChars".split(
        " ",
    ),
);
const strings = new Set(
    "event status operation cancelPhase cancelReason kind taskId serverInstanceId time createdAt updatedAt lastProgressAt heartbeatAt lastRequestAt importState file function stage retryReason finishReason python os architecture serviceVersion babeldocVersion pdf2zhVersion".split(
        " ",
    ),
);
const containers = new Set(
    "tasks records frames environment progress requestedConfiguration effectiveConfiguration translationSummary qualitySummary metrics requests tokens byKind translation review initialization localCache providerCache".split(
        " ",
    ),
);
const labels = new Set(
    "provider service model sourceLang targetLang".split(" "),
);
const errorTypes = new Set(
    "ReadTimeout ConnectTimeout TimeoutError APITimeoutError RateLimitError APIConnectionError APIStatusError AuthenticationError PermissionDeniedError NotFoundError BadRequestError InternalServerError InvalidTranslation UnprocessedParagraph RuntimeError ValueError CancelledError ConnectionError".split(
        " ",
    ),
);
const countMaps: Record<string, Set<string>> = {
    statusCodes: new Set(["unknown"]),
    errorTypes,
    finishReasons: new Set(
        "stop length content_filter tool_calls function_call completed incomplete failed cancelled max_output_tokens other".split(
            " ",
        ),
    ),
    protocols: new Set(["auto", "chat_completions", "responses"]),
};
const providerCodes = new Set(
    "model_not_found invalid_api_key insufficient_quota rate_limit_exceeded permission_denied".split(
        " ",
    ),
);
const flags = new Set([
    "stalled",
    "cancelRequested",
    "truncated",
    "queueBlocked",
    "success",
    "canRepair",
    "canDownloadResult",
    "failedParagraphsTruncated",
]);

/** Independent export boundary: exclude arbitrary server fields, bodies and messages. */
export function safeDiagnostic(value: unknown, depth = 0): unknown {
    if (depth > 10) return undefined;
    if (Array.isArray(value))
        return value
            .slice(-20000)
            .map((item) => safeDiagnostic(item, depth + 1));
    if (!value || typeof value !== "object") return undefined;
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) {
        if (
            numeric.has(key) &&
            typeof item === "number" &&
            Number.isFinite(item)
        )
            out[key] = item;
        else if (flags.has(key) && typeof item === "boolean") out[key] = item;
        else if (
            labels.has(key) &&
            typeof item === "string" &&
            item.length <= 200 &&
            /^[a-zA-Z0-9][a-zA-Z0-9_.-]*(?:[/:][a-zA-Z0-9][a-zA-Z0-9_.-]*)*$/.test(
                item,
            ) &&
            !/(?:sk-|Bearer|api[_-]?key|token=)/i.test(item)
        )
            out[key] = item;
        else if (
            key === "providerCode" &&
            typeof item === "string" &&
            providerCodes.has(item)
        )
            out[key] = item;
        else if (
            key === "errorType" &&
            typeof item === "string" &&
            errorTypes.has(item)
        )
            out[key] = item;
        else if (
            key === "protocol" &&
            typeof item === "string" &&
            countMaps.protocols.has(item)
        )
            out[key] = item;
        else if (
            key === "availability" &&
            typeof item === "string" &&
            ["complete", "partial", "unavailable"].includes(item)
        )
            out[key] = item;
        else if (
            Object.hasOwn(countMaps, key) &&
            item &&
            typeof item === "object" &&
            !Array.isArray(item)
        ) {
            out[key] = Object.fromEntries(
                Object.entries(item).filter(
                    ([name, count]) =>
                        (countMaps[key].has(name) ||
                            (key === "statusCodes" &&
                                /^[1-5][0-9]{2}$/.test(name))) &&
                        typeof count === "number" &&
                        Number.isSafeInteger(count) &&
                        count >= 0,
                ),
            );
        } else if (key === "failedParagraphs" && Array.isArray(item)) {
            out[key] = item
                .slice(0, 2000)
                .filter((row) => row && typeof row === "object")
                .map((row) =>
                    safeDiagnostic(
                        Object.fromEntries(
                            Object.entries(row).filter(([name]) =>
                                [
                                    "page",
                                    "attempts",
                                    "errorType",
                                    "statusCode",
                                    "providerCode",
                                ].includes(name),
                            ),
                        ),
                        depth + 1,
                    ),
                );
            if (item.length > 2000) out.failedParagraphsTruncated = true;
        } else if (
            strings.has(key) &&
            typeof item === "string" &&
            /^[a-zA-Z0-9_. :+-]{1,120}$/.test(item) &&
            !/\b(?:sk-|Bearer|api[_-]?key|token=)/i.test(item)
        )
            out[key] = item;
        else if (containers.has(key))
            out[key] = safeDiagnostic(item, depth + 1);
    }
    return out;
}

/** Summary uses only task snapshots, never the user's currently selected preferences. */
export function diagnosticSummary(task: Record<string, any>): string {
    const safe = safeDiagnostic(task) as Record<string, any>;
    const requested = safe.requestedConfiguration;
    const effective = safe.effectiveConfiguration;
    const lines = [
        `任务：${safe.taskId || "未知"}；状态：${safe.status || "未知"}；尝试：${safe.attempt ?? "未知"}`,
    ];
    for (const [label, values] of [
        ["提交配置", requested],
        ["实际生效配置", effective],
    ] as const) {
        lines.push(
            `${label}：${values ? `服务类型=${values.provider ?? "未记录"}，模型=${values.model ?? "未记录"}，协议=${values.protocol ?? "未记录"}，QPS=${values.qps ?? "未记录"}，并发=${values.poolSize ?? "未记录"}` : "未记录（旧版本或尚未完成初始化）"}`,
        );
    }
    const translation = safe.translationSummary;
    if (translation)
        lines.push(
            `段落：成功=${translation.succeeded ?? "未知"}，失败=${translation.failed ?? "未知"}，待译=${translation.pending ?? "未知"}，跳过=${translation.skipped ?? "未知"}`,
        );
    const requests = safe.metrics?.requests;
    if (requests) {
        const codes = requests.statusCodes || {};
        const errors = requests.errorTypes || {};
        const timeouts = [
            "APITimeoutError",
            "TimeoutError",
            "ReadTimeout",
            "ConnectTimeout",
        ].reduce((sum, key) => sum + (errors[key] || 0), 0);
        lines.push(
            `模型请求（含初始化和重试）：尝试=${requests.attempts ?? "未知"}，成功=${requests.succeeded ?? "未知"}，失败=${requests.failed ?? "未知"}，重试=${requests.retries ?? "未知"}，429=${requests.statusCodes ? (codes["429"] ?? 0) : "未记录"}，超时=${requests.errorTypes ? timeouts : "未记录"}`,
        );
        if (codes["429"])
            lines.push(
                "检测到 429；具体是请求频率、Token 限制还是额度不足，需结合 providerCode 或服务商后台确认。",
            );
    }
    if (safe.status === "incomplete")
        lines.push(
            "任务已结束但仍未完成翻译；页面处理完成、PDF 已保存或已导入均不代表所有段落翻译成功。",
        );
    if (safe.failedParagraphsTruncated)
        lines.push("失败段落明细超过上限，仅保留前 2000 项；总数见段落统计。");
    return lines.join("\n") + "\n";
}

type DiagnosticRow = Record<string, unknown>;
let pending = Promise.resolve();
const recent: DiagnosticRow[] = [];
let persistenceFailed = false;
let queuedWrites = 0;

function logPath(): string {
    return PathUtils.join(
        Zotero.DataDirectory.dir,
        `${config.addonRef}-diagnostics.jsonl`,
    );
}

export function recordDiagnostic(
    event: string,
    fields: Record<string, unknown> = {},
): void {
    // Detailed outcomes belong to task.json; repeating them per progress event fills the log.
    if (event === "task_state")
        fields = Object.fromEntries(
            Object.entries(fields).filter(
                ([key]) => key !== "metrics" && key !== "failedParagraphs",
            ),
        );
    const row = safeDiagnostic({
        time: new Date().toISOString(),
        event,
        ...fields,
    }) as DiagnosticRow;
    recent.push(row);
    if (recent.length > 1000) recent.shift();
    if (queuedWrites >= 100) {
        persistenceFailed = true;
        return;
    }
    queuedWrites++;
    pending = pending
        .then(async () => {
            const path = logPath();
            let bytes: Uint8Array = new Uint8Array(0);
            if (await IOUtils.exists(path)) bytes = await IOUtils.read(path);
            const line = new TextEncoder().encode(JSON.stringify(row) + "\n");
            if (bytes.length + line.length > MAX_LOG) {
                await IOUtils.move(path, path + ".1", { noOverwrite: false });
                bytes = new Uint8Array(0);
            }
            const merged = new Uint8Array(bytes.length + line.length);
            merged.set(bytes);
            merged.set(line, bytes.length);
            await IOUtils.write(path, merged, { tmpPath: path + ".tmp" });
        })
        .catch(() => {
            persistenceFailed = true;
        })
        .finally(() => {
            queuedWrites--;
        });
}

export async function diagnosticFetch(
    url: string,
    options?: RequestInit,
): Promise<Response> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal,
        });
        recordDiagnostic("http_response", { statusCode: response.status });
        return response;
    } catch (error) {
        recordDiagnostic(
            controller.signal.aborted ? "http_timeout" : "http_failure",
        );
        throw error;
    } finally {
        clearTimeout(timer);
    }
}

// ZIP "stored" entries: no privileged archive extraction, external dependency or subprocess.
export function diagnosticZip(files: Record<string, string>): Uint8Array {
    const encoder = new TextEncoder();
    const chunks: Uint8Array[] = [],
        central: Uint8Array[] = [];
    let offset = 0,
        contentSize = 0;
    const crc32 = (bytes: Uint8Array) => {
        let crc = 0xffffffff;
        for (const b of bytes) {
            crc ^= b;
            for (let i = 0; i < 8; i++)
                crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
        }
        return (crc ^ 0xffffffff) >>> 0;
    };
    for (const [name, text] of Object.entries(files)) {
        if (!/^[a-zA-Z0-9_.-]+$/.test(name))
            throw new Error("Invalid diagnostic filename");
        const n = encoder.encode(name),
            data = encoder.encode(text),
            crc = crc32(data);
        contentSize += data.length;
        if (contentSize > MAX_PACKAGE) throw new Error("诊断包超出大小限制");
        const header = new Uint8Array(30 + n.length),
            h = new DataView(header.buffer);
        h.setUint32(0, 0x04034b50, true);
        h.setUint16(4, 20, true);
        h.setUint32(14, crc, true);
        h.setUint32(18, data.length, true);
        h.setUint32(22, data.length, true);
        h.setUint16(26, n.length, true);
        header.set(n, 30);
        const entry = new Uint8Array(46 + n.length),
            c = new DataView(entry.buffer);
        c.setUint32(0, 0x02014b50, true);
        c.setUint16(4, 20, true);
        c.setUint16(6, 20, true);
        c.setUint32(16, crc, true);
        c.setUint32(20, data.length, true);
        c.setUint32(24, data.length, true);
        c.setUint16(28, n.length, true);
        c.setUint32(42, offset, true);
        entry.set(n, 46);
        central.push(entry);
        chunks.push(header, data);
        offset += header.length + data.length;
    }
    const centralSize = central.reduce((n, c) => n + c.length, 0),
        end = new Uint8Array(22),
        e = new DataView(end.buffer);
    e.setUint32(0, 0x06054b50, true);
    e.setUint16(8, central.length, true);
    e.setUint16(10, central.length, true);
    e.setUint32(12, centralSize, true);
    e.setUint32(16, offset, true);
    const result = new Uint8Array(offset + centralSize + 22);
    let pos = 0;
    for (const chunk of [...chunks, ...central, end]) {
        result.set(chunk, pos);
        pos += chunk.length;
    }
    return result;
}

async function collectAvailable(
    serverUrl: string,
    task?: Record<string, unknown>,
): Promise<Record<string, string>> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    let server: unknown = {},
        availability = "服务端诊断不可用；已保留插件侧记录。";
    let records: unknown[] = recent.slice();
    try {
        const endpoint = task
            ? `/tasks/${encodeURIComponent(String(task.taskId))}/diagnostics`
            : "/diagnostics";
        const response = await fetch(serverUrl.replace(/\/$/, "") + endpoint, {
            signal: controller.signal,
        });
        if (response.ok) {
            const reader = response.body?.getReader();
            if (!reader) throw new Error("Response stream unavailable");
            const parts: Uint8Array[] = [];
            let size = 0;
            while (true) {
                const part = await reader.read();
                if (part.done) break;
                size += part.value.length;
                if (size > 12 * 1024 * 1024) {
                    controller.abort();
                    throw new Error("Size limit");
                }
                parts.push(part.value);
            }
            const bytes = new Uint8Array(size);
            let pos = 0;
            for (const part of parts) {
                bytes.set(part, pos);
                pos += part.length;
            }
            const data = JSON.parse(new TextDecoder().decode(bytes));
            if (data.schemaVersion !== 1)
                throw new Error("Unsupported diagnostic schema");
            server = safeDiagnostic(data);
            availability =
                "服务端诊断已采集。堆栈为最近可用快照；原生代码阻塞时可能无法取得实时堆栈。";
            if (data.truncated) availability += " 服务日志已轮转或截断。";
            if (data.queueBlocked)
                availability += " 任务进程回收未确认，队列已暂停。";
        } else if (response.status === 404)
            availability =
                "服务端缺少诊断接口或任务已不存在；无法确认服务端具备超时回收能力，请检查服务版本。";
    } catch {
        recordDiagnostic("export_partial");
    } finally {
        clearTimeout(timer);
    }
    try {
        const path = logPath();
        for (const candidate of [path + ".1", path]) {
            if (await IOUtils.exists(candidate)) {
                const bytes = await IOUtils.read(candidate, {
                    maxBytes: MAX_LOG,
                });
                for (const line of new TextDecoder()
                    .decode(bytes)
                    .split("\n")) {
                    try {
                        records.push(safeDiagnostic(JSON.parse(line)));
                    } catch {
                        /* Incomplete line */
                    }
                }
            }
        }
    } catch {
        persistenceFailed = true;
    }
    if (task)
        records = records.filter(
            (row: any) => !row?.taskId || row.taskId === task.taskId,
        );
    // The in-memory tail also exists on disk. Count each identical event once.
    records = [
        ...new Map(
            records.filter(Boolean).map((row) => [JSON.stringify(row), row]),
        ).values(),
    ].sort((a: any, b: any) =>
        String(a.time || "").localeCompare(String(b.time || "")),
    );
    const truncated = records.length > 5000;
    records = records.slice(-5000);
    const serverTask = (
        server as { tasks?: Record<string, unknown>[] }
    ).tasks?.find((row) => row.taskId === task?.taskId);
    // Missing fields in a new attempt must not inherit a previous attempt's evidence.
    const snapshot = serverTask
        ? {
              ...serverTask,
              importState:
                  serverTask.attempt === task?.attempt
                      ? task?.importState
                      : undefined,
          }
        : task || {};
    return {
        "summary.txt": `PDF2ZH 诊断包\n生成时间：${new Date().toISOString()}\n插件：${config.addonName} ${version}\nZotero：${Zotero.version}\n${availability}\n${task ? diagnosticSummary(snapshot) : "任务配置和统计见 server.json 的 tasks。\n"}${persistenceFailed ? "部分插件持久化日志不可用。\n" : ""}${truncated ? "插件记录已截断。\n" : ""}日志受容量限制，仅保留最近记录。\n包含服务商、模型标识、任务参数及结构化错误代码；不包含原论文、正文、密钥、接口地址或请求响应内容。\n`,
        "task.json": JSON.stringify(safeDiagnostic(snapshot), null, 2),
        "server.json": JSON.stringify(server, null, 2),
        "plugin.json": JSON.stringify(
            { schemaVersion: 1, truncated, records },
            null,
            2,
        ),
    };
}

async function collect(
    serverUrl: string,
    task?: Record<string, unknown>,
): Promise<Record<string, string>> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
        return await Promise.race([
            collectAvailable(serverUrl, task),
            new Promise<Record<string, string>>((resolve) => {
                timer = setTimeout(
                    () =>
                        resolve({
                            "summary.txt": `PDF2ZH 诊断包\n插件版本：${version}\n采集超过 10 秒，服务端或磁盘记录未完整取得；仅包含当前插件内存快照。\n${task ? diagnosticSummary(task) : ""}包含服务商、模型标识和任务参数；不包含正文、密钥、接口地址或请求响应内容。\n`,
                            "task.json": JSON.stringify(
                                safeDiagnostic(task || {}),
                            ),
                            "server.json": "{}",
                            "plugin.json": JSON.stringify({
                                schemaVersion: 1,
                                records: recent.slice(),
                            }),
                        }),
                    10000,
                );
            }),
        ]);
    } finally {
        clearTimeout(timer);
    }
}

export async function exportDiagnostics(
    serverUrl: string,
    task?: Record<string, unknown>,
): Promise<void> {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    const id =
        task?.taskId && /^[a-zA-Z0-9-]{1,64}$/.test(String(task.taskId))
            ? `-${task.taskId}`
            : "";
    const path = await new ztoolkit.FilePicker(
        "导出诊断包",
        "save",
        [["ZIP", "*.zip"]],
        `pdf2zh-diagnostics${id}-${stamp}.zip`,
    ).open();
    if (!path) return;
    const files = await collect(serverUrl, task);
    // Server data is already capped; trim oldest client rows if aggregate pretty JSON grows.
    if (
        Object.values(files).reduce(
            (n, s) => n + new TextEncoder().encode(s).length,
            0,
        ) > MAX_PACKAGE
    ) {
        const data = JSON.parse(files["server.json"]);
        while (
            data.records?.length &&
            new TextEncoder().encode(JSON.stringify(data)).length >
                10 * 1024 * 1024
        )
            data.records.shift();
        files["server.json"] = JSON.stringify(data);
        files["summary.txt"] += "包体超限，已截断较早记录。\n";
    }
    try {
        await IOUtils.write(path, diagnosticZip(files), {
            tmpPath: path + ".tmp",
        });
        recordDiagnostic("export_complete");
    } catch (error) {
        await IOUtils.remove(path + ".tmp", { ignoreAbsent: true }).catch(
            () => {},
        );
        throw error;
    }
}
