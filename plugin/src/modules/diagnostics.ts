import { config, version } from "../../package.json";
import { diagnosticContract } from "./diagnosticContract";

const MAX_PACKAGE = 20 * 1024 * 1024;
const MAX_LOG = 2 * 1024 * 1024;
const numeric = new Set<string>(diagnosticContract.numeric);
const strings = new Set<string>(diagnosticContract.strings);
const timestamps = new Set<string>(diagnosticContract.timestamps);
const containers = new Set<string>(diagnosticContract.containers);
const labels = new Set<string>(diagnosticContract.labels);
const identifiers = new Set<string>(diagnosticContract.identifiers);
const flags = new Set<string>(diagnosticContract.flags);
const opaqueIdentifiers: Record<string, string> =
    diagnosticContract.opaqueIdentifiers;
const identifierLists: Record<string, string> =
    diagnosticContract.identifierLists;
const enums: Record<string, readonly string[]> = diagnosticContract.enums;
const validationCodes = new Set<string>(enums.validationCode);
const countMaps: Record<string, readonly string[]> = Object.fromEntries(
    Object.entries(diagnosticContract.countMaps).map(([key, enumName]) => [
        key,
        typeof enumName === "string" ? enums[enumName] : ["unknown"],
    ]),
);
export interface DiagnosticFilterReport {
    filteredFields: number;
    rejectedValues: number;
}

/** Independent export boundary: exclude arbitrary server fields, bodies and messages. */
export function safeDiagnostic(
    value: unknown,
    depth = 0,
    report?: DiagnosticFilterReport,
): unknown {
    if (depth > 10) return undefined;
    if (Array.isArray(value))
        return value
            .slice(-20000)
            .map((item) => safeDiagnostic(item, depth + 1, report));
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
            Object.hasOwn(opaqueIdentifiers, key) &&
            typeof item === "string" &&
            new RegExp(`^(?:${opaqueIdentifiers[key]})$`).test(item)
        )
            out[key] = item;
        else if (Object.hasOwn(identifierLists, key) && Array.isArray(item)) {
            const pattern = new RegExp(
                `^(?:${opaqueIdentifiers[identifierLists[key]]})$`,
            );
            out[key] = item
                .slice(0, 256)
                .filter(
                    (entry) => typeof entry === "string" && pattern.test(entry),
                );
            if (report)
                report.rejectedValues +=
                    item.length - (out[key] as unknown[]).length;
        } else if (
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
            Object.hasOwn(enums, key) &&
            typeof item === "string" &&
            enums[key].includes(item)
        )
            out[key] = item;
        else if (
            identifiers.has(key) &&
            typeof item === "string" &&
            /^[a-zA-Z0-9-]{1,64}$/.test(item)
        )
            out[key] = item;
        else if (
            timestamps.has(key) &&
            typeof item === "string" &&
            /^[0-9TZ:.+-]{10,40}$/.test(item)
        )
            out[key] = item;
        else if (
            Object.hasOwn(countMaps, key) &&
            item &&
            typeof item === "object" &&
            !Array.isArray(item)
        ) {
            out[key] = Object.fromEntries(
                Object.entries(item).filter(([name, count]) => {
                    const valid =
                        (countMaps[key].includes(name) ||
                            (key === "statusCodes" &&
                                /^[1-5][0-9]{2}$/.test(name))) &&
                        typeof count === "number" &&
                        Number.isSafeInteger(count) &&
                        count >= 0;
                    if (!valid && report) report.rejectedValues++;
                    return valid;
                }),
            );
        } else if (key === "failedParagraphs" && Array.isArray(item)) {
            out[key] = item
                .slice(0, 2000)
                .filter((row) => row && typeof row === "object")
                .map((row) => {
                    const fields = Object.fromEntries(
                        Object.entries(row).filter(([name]) =>
                            (
                                diagnosticContract.failedParagraphFields as readonly string[]
                            ).includes(name),
                        ),
                    );
                    // Compatibility adapter for task checkpoints written before structured codes.
                    if (
                        row.errorType === "InvalidTranslation" &&
                        typeof row.reason === "string" &&
                        !(
                            typeof row.validationCode === "string" &&
                            validationCodes.has(row.validationCode)
                        )
                    ) {
                        const code = row.reason.split(" → ", 1)[0];
                        if (validationCodes.has(code))
                            fields.validationCode = code;
                    }
                    return safeDiagnostic(fields, depth + 1, report);
                });
            if (item.length > 2000) out.failedParagraphsTruncated = true;
        } else if (
            strings.has(key) &&
            typeof item === "string" &&
            /^[a-zA-Z0-9_. :+-]{1,120}$/.test(item) &&
            !/\b(?:sk-|Bearer|api[_-]?key|token=)/i.test(item)
        )
            out[key] = item;
        else if (containers.has(key))
            out[key] = safeDiagnostic(item, depth + 1, report);
        else if (report) {
            const known =
                numeric.has(key) ||
                flags.has(key) ||
                labels.has(key) ||
                identifiers.has(key) ||
                timestamps.has(key) ||
                strings.has(key) ||
                Object.hasOwn(enums, key) ||
                Object.hasOwn(opaqueIdentifiers, key) ||
                Object.hasOwn(identifierLists, key) ||
                Object.hasOwn(countMaps, key);
            if (known) report.rejectedValues++;
            else report.filteredFields++;
        }
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
            `模型请求（含初始化和重试）：尝试=${requests.attempts ?? "未知"}，成功=${requests.succeeded ?? "未知"}，失败=${requests.failed ?? "未知"}，取消=${requests.cancelled ?? "未记录"}，重试=${requests.retries ?? "未知"}，批次修复=${requests.repairs ?? "未记录"}，429=${requests.statusCodes ? (codes["429"] ?? 0) : "未记录"}，超时=${requests.errorTypes ? timeouts : "未记录"}`,
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
    const validationCounts = new Map<string, number>();
    for (const row of safe.failedParagraphs || []) {
        if (row.validationCode)
            validationCounts.set(
                row.validationCode,
                (validationCounts.get(row.validationCode) || 0) + 1,
            );
    }
    if (validationCounts.size)
        lines.push(
            "校验失败原因（已记录段落）：" +
                [...validationCounts]
                    .map(([code, count]) => `${code}=${count}`)
                    .join("，"),
        );
    const unclassified = (safe.failedParagraphs || []).filter(
        (row: Record<string, unknown>) =>
            row.errorType === "InvalidTranslation" &&
            (!row.validationCode ||
                row.validationCode === "unknown_validation"),
    ).length;
    if (unclassified)
        lines.push(
            `其中 ${unclassified} 个校验失败段落缺少具体错误码；旧版记录或未知代码，无法从本包恢复具体原因。`,
        );
    if (safe.failedParagraphsTruncated)
        lines.push("失败段落明细超过上限，仅保留前 2000 项；总数见段落统计。");
    return lines.join("\n") + "\n";
}

type DiagnosticRow = Record<string, unknown>;
let pending = Promise.resolve();
let stopped = false;
const recent: DiagnosticRow[] = [];
let persistenceFailed = false;
let queuedWrites = 0;
let droppedWrites = 0;
let writeFailures = 0;
let memoryEvictions = 0;
let filteredFields = 0;
let rejectedValues = 0;

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
    if (stopped) return;
    // Detailed outcomes belong to task.json; repeating them per progress event fills the log.
    if (event === "task_state")
        fields = Object.fromEntries(
            Object.entries(fields).filter(
                ([key]) => key !== "metrics" && key !== "failedParagraphs",
            ),
        );
    const report = { filteredFields: 0, rejectedValues: 0 };
    const row = safeDiagnostic(
        { ...fields, time: new Date().toISOString(), event },
        0,
        report,
    ) as DiagnosticRow;
    filteredFields += report.filteredFields;
    rejectedValues += report.rejectedValues;
    recent.push(row);
    if (recent.length > 1000) {
        recent.shift();
        memoryEvictions++;
    }
    if (queuedWrites >= 100) {
        droppedWrites++;
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
            writeFailures++;
            persistenceFailed = true;
        })
        .finally(() => {
            queuedWrites--;
        });
}

/** Drain the old instance before its sandbox is unloaded or a new writer starts. */
export async function stopDiagnostics(): Promise<void> {
    stopped = true;
    await pending;
}

export async function diagnosticFetch(
    url: string,
    options?: RequestInit,
    limits?: { totalMs: number; idleMs: number; headersMs?: number },
): Promise<Response> {
    return maintenanceFetch(url, options, (url, options) =>
        diagnosticFetchOnce(url, options, limits),
    );
}

export async function maintenanceFetch(
    url: string,
    options?: RequestInit,
    perform: (
        url: string,
        options?: RequestInit,
    ) => Promise<Response> = diagnosticFetchOnce,
): Promise<Response> {
    const deadline = Date.now() + 90000;
    for (;;) {
        const response = await perform(url, options);
        if (
            response.status !== 503 ||
            response.headers.get("X-PDF2ZH-Maintenance") !== "not-accepted" ||
            Date.now() >= deadline
        )
            return response;
        const rejection = (await response
            .clone()
            .json()
            .catch(() => null)) as { code?: string; accepted?: boolean } | null;
        if (
            rejection?.code !== "server_maintenance" ||
            rejection.accepted !== false
        )
            return response;
        const delay =
            Math.min(
                10,
                Math.max(1, Number(response.headers.get("Retry-After")) || 3),
            ) * 1000;
        const wait = () =>
            new Promise<void>((resolve, reject) => {
                if (options?.signal?.aborted) {
                    reject(new DOMException("Aborted", "AbortError"));
                    return;
                }
                const abort = () => {
                    clearTimeout(timer);
                    reject(new DOMException("Aborted", "AbortError"));
                };
                const timer = setTimeout(() => {
                    options?.signal?.removeEventListener("abort", abort);
                    resolve();
                }, delay);
                options?.signal?.addEventListener("abort", abort, {
                    once: true,
                });
            });
        // Once rejection is proven, wait using harmless health reads while the port switches.
        // A lost response to an actual submission remains an error and is never replayed.
        for (;;) {
            await wait();
            if (Date.now() >= deadline) return response;
            try {
                const probe = await perform(new URL("/health", url).href, {
                    method: "GET",
                    signal: options?.signal,
                });
                const health = (await probe.json()) as {
                    status?: string;
                    maintenance?: { held?: boolean };
                };
                if (
                    probe.ok &&
                    health?.status === "ok" &&
                    !health?.maintenance?.held
                )
                    break;
            } catch (error) {
                if (options?.signal?.aborted) throw error;
            }
        }
    }
}

async function diagnosticFetchOnce(
    url: string,
    options?: RequestInit,
    limits: { totalMs: number; idleMs: number; headersMs?: number } = {
        totalMs: 30000,
        idleMs: 10000,
    },
): Promise<Response> {
    const controller = new AbortController();
    let timedOut = false;
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let rejectAbort: (reason: Error) => void;
    const aborted = new Promise<never>((_resolve, reject) => {
        rejectAbort = reject;
    });
    const abort = () => {
        controller.abort();
        void reader?.cancel().catch(() => {});
        rejectAbort(
            new DOMException(
                timedOut ? "请求超时，请稍后重试。" : "请求已取消。",
                timedOut ? "TimeoutError" : "AbortError",
            ),
        );
    };
    const timeout = () => {
        timedOut = true;
        abort();
    };
    options?.signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(timeout, limits.totalMs);
    // Preserve the connection/header deadline even for a large PDF download.
    let idleTimer = setTimeout(
        timeout,
        limits.headersMs ?? Math.min(10000, limits.idleMs),
    );
    try {
        if (options?.signal?.aborted) abort();
        return await Promise.race([
            aborted,
            (async () => {
                if (controller.signal.aborted) return await aborted;
                const response = await fetch(url, {
                    ...options,
                    signal: controller.signal,
                });
                recordDiagnostic("http_response", {
                    statusCode: response.status,
                });
                if (controller.signal.aborted) {
                    void response.body?.cancel().catch(() => {});
                    return await aborted;
                }
                // Finite requests resolve only after the whole body is received.
                // Selection SSE uses maintenanceFetch with its own streaming transport.
                if (!response.body) return response;
                const bodyReader = response.body.getReader();
                reader = bodyReader;
                const chunks: BlobPart[] = [];
                for (;;) {
                    clearTimeout(idleTimer);
                    idleTimer = setTimeout(timeout, limits.idleMs);
                    const { done, value } = await bodyReader.read();
                    if (controller.signal.aborted) return await aborted;
                    if (done) break;
                    chunks.push(value as Uint8Array<ArrayBuffer>);
                }
                return new Response(new Blob(chunks), {
                    status: response.status,
                    statusText: response.statusText,
                    headers: response.headers,
                });
            })(),
        ]);
    } catch (error) {
        recordDiagnostic(
            timedOut
                ? "http_timeout"
                : controller.signal.aborted
                  ? "http_cancelled"
                  : "http_failure",
        );
        throw error;
    } finally {
        clearTimeout(timer);
        clearTimeout(idleTimer);
        options?.signal?.removeEventListener("abort", abort);
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

type CollectionCode = (typeof diagnosticContract.enums.collectionCode)[number];
interface CollectionSource extends DiagnosticFilterReport {
    status: "available" | "partial" | "unavailable";
    reasonCode: CollectionCode;
    statusCode?: number;
    schemaVersion?: number;
    schemaRevision?: number;
}
function collectionSource(reasonCode: CollectionCode): CollectionSource {
    return {
        status: reasonCode === "none" ? "available" : "unavailable",
        reasonCode,
        filteredFields: 0,
        rejectedValues: 0,
    };
}
const collectionMessages: Partial<Record<CollectionCode, string>> = {
    none: "服务端诊断已采集。堆栈为最近可用快照；原生代码阻塞时可能无法取得实时堆栈。",
    timeout: "服务端诊断采集超时；已保留插件侧记录。",
    unreachable: "服务端诊断不可用；已保留插件侧记录。",
    unsupported_schema:
        "服务端诊断格式不受支持；已保留插件侧记录，请更新兼容版本。",
    invalid_payload: "服务端诊断响应格式无效；已保留插件侧记录。",
    size_limit: "服务端诊断响应超过容量限制；已保留插件侧记录。",
    interface_unavailable:
        "服务端缺少诊断接口或任务已不存在；已保留插件侧记录。",
    http_error: "服务端诊断接口返回错误；状态码见 manifest.json。",
    collection_deadline:
        "采集超过 10 秒，服务端或磁盘记录未完整取得；仅包含当前插件内存快照。",
};
function diagnosticFiles(
    server: Record<string, any>,
    task: Record<string, unknown> | undefined,
    records: unknown[],
    truncated: boolean,
    source: CollectionSource,
): Record<string, string> {
    const serverTask = (
        server.tasks as Record<string, unknown>[] | undefined
    )?.find((row) => row?.taskId === task?.taskId);
    // A new attempt never inherits evidence from an older local snapshot.
    const snapshot = serverTask
        ? {
              ...serverTask,
              importState:
                  serverTask.attempt === task?.attempt
                      ? task?.importState
                      : undefined,
          }
        : task || {};
    const safe = safeDiagnostic(snapshot) as Record<string, any>;
    const unclassified = (safe.failedParagraphs || []).filter(
        (row: Record<string, unknown>) =>
            row.errorType === "InvalidTranslation" &&
            (!row.validationCode ||
                row.validationCode === "unknown_validation"),
    ).length;
    const collected = { ...source };
    const isRow = (row: unknown): row is Record<string, any> =>
        !!row && typeof row === "object" && !Array.isArray(row);
    const evidence = {
        taskEvidence: Array.isArray(server.evidence?.taskEvidence)
            ? server.evidence.taskEvidence.filter(isRow)
            : [],
        omittedTasks: server.evidence?.omittedTasks || 0,
    };
    const analysis = Array.isArray(server.analysis?.findings)
        ? {
              ...server.analysis,
              findings: server.analysis.findings.filter(isRow),
          }
        : {
              schemaVersion: diagnosticContract.schemaVersion,
              schemaRevision: diagnosticContract.schemaRevision,
              findings: [
                  { findingCode: "evidence_unavailable", taskId: safe.taskId },
              ],
          };
    const evidenceMessages = (analysis.findings || [])
        .map((row: Record<string, any>) => {
            const rule =
                diagnosticContract.diagnosisRules[
                    row.findingCode as keyof typeof diagnosticContract.diagnosisRules
                ];
            return rule
                ? `${rule.title}${row.validationCode ? `（${row.validationCode}）` : ""}：${rule.suggestion}`
                : "";
        })
        .filter(Boolean);
    const evidenceSummary = [...new Set<string>(evidenceMessages)]
        .slice(0, 30)
        .join("\n");
    // Large graphs live once in the ZIP; server.json keeps its original task/log envelope.
    const serverSnapshot = { ...server };
    delete serverSnapshot.evidence;
    delete serverSnapshot.analysis;
    if (
        collected.status === "available" &&
        (server.truncated || unclassified || source.rejectedValues)
    ) {
        collected.status = "partial";
        if (unclassified)
            collected.reasonCode = "legacy_validation_details_missing";
    }
    const manifest = {
        schemaVersion: diagnosticContract.schemaVersion,
        schemaRevision: diagnosticContract.schemaRevision,
        createdAt: new Date().toISOString(),
        exporter: { pluginVersion: version },
        sources: {
            evidence: {
                status:
                    evidence.taskEvidence.length &&
                    evidence.taskEvidence.every(
                        (graph: Record<string, any>) =>
                            graph.evidenceStatus === "available",
                    )
                        ? "available"
                        : evidence.taskEvidence.some(
                                (graph: Record<string, any>) =>
                                    ["available", "partial"].includes(
                                        graph.evidenceStatus,
                                    ),
                            )
                          ? "partial"
                          : "unavailable",
                evidenceCode: evidence.taskEvidence.length
                    ? undefined
                    : "evidence_missing",
                taskEvidence: (evidence.taskEvidence || []).map(
                    (graph: Record<string, any>) => ({
                        taskId: graph.taskId,
                        evidenceStatus: graph.evidenceStatus,
                        evidenceCode: graph.evidenceCode,
                        retainedRequests: graph.requests?.length || 0,
                        retainedParagraphs: graph.paragraphs?.length || 0,
                        omittedRequests: graph.omittedRequests || 0,
                        omittedParagraphs: graph.omittedParagraphs || 0,
                        omittedExecutions: graph.omittedExecutions || 0,
                    }),
                ),
                omittedTasks: evidence.omittedTasks || 0,
            },
            server: {
                ...collected,
                producerVersion: server.environment?.serviceVersion,
                truncated: server.truncated === true,
                unclassifiedFailures: unclassified,
                collection: server.collection,
            },
            plugin: {
                status:
                    persistenceFailed || truncated ? "partial" : "available",
                reasonCode: persistenceFailed ? "disk_error" : "none",
                truncated,
                droppedWrites,
                writeFailures,
                memoryEvictions,
                filteredFields,
                rejectedValues,
            },
        },
    };
    return {
        "manifest.json": JSON.stringify(manifest, null, 2),
        "summary.txt": `PDF2ZH 诊断包\n生成时间：${manifest.createdAt}\n插件：${config.addonName} ${version}\nZotero：${Zotero.version}\n${collectionMessages[source.reasonCode] || source.reasonCode}\n${task ? diagnosticSummary(snapshot) : "任务配置和统计见 server.json 的 tasks。\n"}${evidenceSummary}\n任务、执行、请求与段落关联见 evidence.json；结构化分析见 analysis.json。\n${persistenceFailed ? "部分插件持久化日志不可用。\n" : ""}${truncated ? "插件记录已截断。\n" : ""}${server.truncated ? "服务日志已轮转或截断。\n" : ""}${server.queueBlocked ? "任务进程回收未确认，队列已暂停。\n" : ""}采集状态、缺失原因与过滤计数见 manifest.json。\n包含服务商、模型标识、任务参数及结构化错误代码；不包含原论文、正文、密钥、接口地址或请求响应内容。\n`,
        "evidence.json": JSON.stringify({
            schemaVersion: diagnosticContract.schemaVersion,
            schemaRevision: diagnosticContract.schemaRevision,
            ...evidence,
        }),
        "analysis.json": JSON.stringify(analysis, null, 2),
        "task.json": JSON.stringify(safe, null, 2),
        "server.json": JSON.stringify(serverSnapshot),
        "plugin.json": JSON.stringify(
            {
                schemaVersion: diagnosticContract.schemaVersion,
                schemaRevision: diagnosticContract.schemaRevision,
                truncated,
                records,
            },
            null,
            2,
        ),
    };
}
async function collectAvailable(
    serverUrl: string,
    task?: Record<string, unknown>,
): Promise<Record<string, string>> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    let server: Record<string, any> = {};
    let source = collectionSource("unreachable");
    let failureCode: CollectionCode = "unreachable";
    let records: unknown[] = recent.slice();
    try {
        const endpoint = task
            ? `/tasks/${encodeURIComponent(String(task.taskId))}/diagnostics`
            : "/diagnostics";
        // Old v1 services ignore the optional query; new services select a common format.
        const query = diagnosticContract.supportedSchemaVersions.join(",");
        const response = await fetch(
            serverUrl.replace(/\/$/, "") +
                endpoint +
                `?schemaVersions=${query}`,
            {
                signal: controller.signal,
            },
        );
        source.statusCode = response.status;
        if (response.ok) {
            failureCode = "invalid_payload";
            const reader = response.body?.getReader();
            if (!reader) throw new Error("Response stream unavailable");
            const parts: Uint8Array[] = [];
            let size = 0;
            while (true) {
                const part = await reader.read();
                if (part.done) break;
                size += part.value.length;
                if (size > 12 * 1024 * 1024) {
                    failureCode = "size_limit";
                    controller.abort();
                    void reader.cancel().catch(() => {});
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
            if (
                !data ||
                typeof data !== "object" ||
                Array.isArray(data) ||
                typeof data.schemaVersion !== "number"
            )
                throw new Error("Invalid diagnostic envelope");
            if (
                !(
                    diagnosticContract.supportedSchemaVersions as readonly number[]
                ).includes(data.schemaVersion)
            ) {
                failureCode = "unsupported_schema";
                throw new Error("Unsupported diagnostic schema");
            }
            if (
                (data.tasks !== undefined && !Array.isArray(data.tasks)) ||
                (data.records !== undefined && !Array.isArray(data.records))
            )
                throw new Error("Invalid diagnostic sections");
            const filters: DiagnosticFilterReport = {
                filteredFields: 0,
                rejectedValues: 0,
            };
            server = safeDiagnostic(data, 0, filters) as Record<string, any>;
            source = {
                ...collectionSource("none"),
                ...filters,
                statusCode: response.status,
                schemaVersion: data.schemaVersion,
                schemaRevision: server.schemaRevision,
            };
        } else {
            source.reasonCode =
                response.status === 406
                    ? "unsupported_schema"
                    : response.status === 404
                      ? "interface_unavailable"
                      : "http_error";
        }
    } catch {
        source.reasonCode =
            failureCode === "size_limit"
                ? failureCode
                : controller.signal.aborted
                  ? "timeout"
                  : failureCode;
        recordDiagnostic("export_partial", {
            collectionCode: source.reasonCode,
        });
    } finally {
        clearTimeout(timer);
    }
    // Serialize the disk snapshot with writes/rotation, including writes queued
    // while the server response was being collected.
    pending = pending.then(async () => {
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
    });
    await pending;
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
    return diagnosticFiles(server, task, records, truncated, source);
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
                        resolve(
                            diagnosticFiles(
                                {},
                                task,
                                recent.slice(),
                                false,
                                collectionSource("collection_deadline"),
                            ),
                        ),
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
