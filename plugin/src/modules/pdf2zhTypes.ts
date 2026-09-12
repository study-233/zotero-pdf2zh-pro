import type { LLMApiData } from "./llmApiManager";
export type OutputMode = "mono" | "dual";
export type ServerTaskStatus =
    | "queued"
    | "running"
    | "cancelling"
    | "completed"
    | "incomplete"
    | "failed"
    | "cancelled";

export interface GlossaryEntry {
    source: string;
    target: string;
    tgt_lng: string;
}

export interface ServerConfig {
    apiConfig?: LLMApiData | null;
    serverUrl: string;
    service: string;
    sourceLang: string;
    targetLang: string;
    outputModes: OutputMode[];
    skipLastPages: string;
    qps: string;
    poolSize: string;
    ocr: string;
    autoOcr: string;
    translateTableText: string;
    skipReferences: string;
    skipTextChecks: string;
    noWatermark: string;
    disableTermExtraction: string;
    fontFamily: string;
    glossaryEntries?: GlossaryEntry[];
    semanticReview?: boolean;
}

export interface DiagnosticMessage {
    code: string;
    severity: "info" | "warning" | "error";
    message: string;
    suggestion?: string;
}

export interface ServerHealthResponse {
    status?: string;
    version?: string;
    pythonVersion?: string;
    supportedApiProtocols?: string[];
    capabilities?: {
        glossaryEntries?: boolean;
        semanticReview?: boolean;
    };
    pdf2zhVersion?: string;
    babeldocVersion?: string;
    workspace?: {
        path?: string;
        writable?: boolean;
        freeBytes?: number;
    };
    tasks?: {
        total?: number;
        active?: number;
        failed?: number;
        completed?: number;
    };
}

export interface ValidateConfigResponse {
    status?: string;
    service?: string;
    model?: string | null;
    resolvedProtocol?: "chat_completions" | "responses" | null;
    message?: string;
    diagnostics?: DiagnosticMessage[];
    liveTest?: {
        enabled: boolean;
        ok?: boolean;
        message?: string;
    };
}

export interface ServerErrorResponse {
    status?: "error" | string;
    message?: string;
    diagnostics?: DiagnosticMessage[];
}

export interface PDFOperationOptions {
    rename: boolean;
    openAfterProcess: boolean;
}

export interface ServerSyncMetadata {
    serverInstanceId?: string;
    revision?: number;
}

export interface ServerTaskList extends ServerSyncMetadata {
    tasks: ServerTaskSnapshot[];
}

export interface TaskQualitySummary {
    selected: number;
    checked: number;
    passed: number;
    corrected: number;
    unchecked: number;
    failed: number;
    notSelected: number;
    requestsUsed: number;
    requestLimit: number;
    paragraphLimit: number;
}

export interface ServerTaskSnapshot extends ServerSyncMetadata {
    taskId: string;
    fileName: string;
    service: string;
    outputModes: OutputMode[];
    status: ServerTaskStatus;
    stage: string | null;
    stageCurrent: number;
    stageTotal: number;
    stageProgress: number;
    overallProgress: number;
    error: string | null;
    errorDiagnostics?: DiagnosticMessage[];
    attempt?: number;
    resultFiles: Partial<Record<OutputMode, string>>;
    createdAt: string;
    updatedAt: string;
    canCancel: boolean;
    cancelRequested: boolean;
    metrics?: TaskMetrics;
    canRepair?: boolean;
    qualitySummary?: TaskQualitySummary | null;
    translationSummary?: {
        total: number;
        succeeded: number;
        skipped: number;
        failed: number;
        pending: number;
    } | null;
    failedParagraphs?: {
        page: number;
        paragraphId: string;
        attempts: number;
        errorType: string;
        reason: string;
    }[];
}

export type RequestKind = "translation" | "review" | "initialization";
export type MetricAvailability = "unavailable" | "partial" | "complete";

export interface RequestMetrics {
    attempts: number;
    succeeded: number;
    failed: number;
    active: number;
    retries: number;
    averageLatencyMs: number | null;
    p95LatencyMs: number | null;
    statusCodes?: Record<string, number>;
    errorTypes?: Record<string, number>;
    finishReasons?: Record<string, number>;
    protocols?: Record<string, number>;
    visibleOutputChars?: number | null;
    batchSizes?: Record<string, number>;
}

export interface TokenMetrics {
    input: number | null;
    output: number | null;
    total: number | null;
    reasoning?: number | null;
    availability?: MetricAvailability;
    reasoningAvailability?: MetricAvailability;
}

export interface TaskMetrics {
    requests: RequestMetrics & {
        qps10s: number;
        byKind?: Partial<Record<RequestKind, RequestMetrics>>;
    };
    localCache: { hits: number; misses: number; hitRate: number | null };
    providerCache: {
        hitTokens: number | null;
        missTokens: number | null;
        availability?: MetricAvailability;
        hitRate: number | null;
    };
    tokens: TokenMetrics & {
        byKind?: Partial<Record<RequestKind, TokenMetrics>>;
    };
    throughput: {
        paragraphsPerMinute: number | null;
        etaSeconds: number | null;
    };
    referencesSkipped: number;
    stageDurations?: Record<string, number>;
}

export interface PluginTask extends ServerTaskSnapshot {
    itemID?: number;
    serverUrl: string;
    source: "local" | "remote";
    importState: "pending" | "importing" | "imported" | "failed" | "none";
    importError?: string;
    importedOutputs?: string[];
}

export interface ServerTaskEvent extends ServerSyncMetadata {
    type: "snapshot" | "task" | "deleted" | "resync";
    task?: ServerTaskSnapshot;
    taskId?: string;
}
