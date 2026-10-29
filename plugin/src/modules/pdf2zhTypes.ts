import type { LLMApiData } from "./llmApiManager";
import type { GlossaryPackReference } from "./glossaryPacks";
export type OutputMode = "mono" | "dual";
export type ServerTaskStatus =
    | "queued"
    | "paused"
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
    glossaryPacks?: GlossaryPackReference[];
    semanticReview?: boolean;
}

export interface DiagnosticMessage {
    code: string;
    severity: "info" | "warning" | "error";
    message: string;
    suggestion?: string;
}

export interface ServerHealthResponse {
    diagnostics?: {
        schemaVersions: number[];
        schemaRevision: number;
        features: string[];
    };
    status?: string;
    version?: string;
    pythonVersion?: string;
    supportedApiProtocols?: string[];
    capabilities?: {
        diagnosticsExport?: boolean;
        boundedCancellation?: boolean;
        detailedTaskProgress?: boolean;
        reasoningMode?: boolean;
        reasoningLow?: boolean;
        mimoApi?: boolean;
        zhipuApi?: boolean;
        codexCli?: boolean;
        chatgptPlan?: boolean;
        codexProxy?: boolean;
        codexDiscovery?: boolean;
        glossaryEntries?: boolean;
        glossaryPacks?: boolean;
        semanticReview?: boolean;
        translationMemory?: boolean;
        textTranslation?: boolean;
        taskSubmissionIdempotency?: boolean;
        taskRecovery?: boolean;
        preprocessingReuse?: boolean;
        taskTiming?: boolean;
        binaryTaskUpload?: boolean;
        taskPagination?: boolean;
    };
    pdf2zhVersion?: string;
    babeldocVersion?: string;
    taskStore?: {
        status: "ok" | "blocked";
        reason?: string | null;
        backupAvailable?: boolean;
        message?: string;
    };
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

export interface CodexRuntime {
    source: string;
    path: string;
    version: string;
    compatible: boolean;
    attempts: Array<{
        path: string;
        source: string;
        code: string;
        message: string;
    }>;
}

export interface ValidateConfigResponse {
    codexRuntime?: CodexRuntime;
    status?: string;
    service?: string;
    model?: string | null;
    resolvedProtocol?: "chat_completions" | "responses" | null;
    message?: string;
    diagnostics?: DiagnosticMessage[];
    liveTest?: {
        reasoning?: {
            requested: string;
            effective: string;
            protocolFallback: boolean;
            modelDetail?: import("./profileApiClient").ProfileModelDetail;
            effort?: string;
        };
        reasoningMessage?: string;
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
    titleTemplate: string;
    titleLayout?: import("./attachmentTitleLayout").TitleLayout;
    openAfterProcess: boolean;
    addTranslatedTag: boolean;
}

export interface ServerSyncMetadata {
    serverInstanceId?: string;
    revision?: number;
}

export interface ServerTaskList extends ServerSyncMetadata {
    tasks: ServerTaskSnapshot[];
    scope?: "dashboard";
    visibleTaskIds?: string[];
    missingTaskIds?: string[];
    pagination?: {
        nextCursor: string | null;
        hasMore: boolean;
        totalHistory: number;
        limit: number;
    };
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

export interface DiagnosticConfiguration {
    provider?: string;
    model?: string;
    protocol?: "auto" | "chat_completions" | "responses";
    qps?: number;
    poolSize?: number;
    sourceLang?: string;
    targetLang?: string;
}

export interface ServerTaskSnapshot extends ServerSyncMetadata {
    canResume?: boolean;
    recoveryState?: "queued" | "interrupted" | null;
    timing?: {
        queuedAt: string;
        startedAt: string | null;
        finishedAt: string | null;
        queueSeconds: number;
        executionSeconds: number;
        totalSeconds: number;
        complete: boolean;
    } | null;
    requestedConfiguration?: DiagnosticConfiguration;
    effectiveConfiguration?: DiagnosticConfiguration;
    taskId: string;
    fileName: string;
    service: string;
    outputModes: OutputMode[];
    status: ServerTaskStatus;
    stage: string | null;
    currentPage?: number;
    selectedPage?: number;
    completedPages?: number;
    totalPages?: number;
    operation?: string;
    lastProgressAt?: string;
    heartbeatAt?: string;
    idleSeconds?: number;
    stalled?: boolean;
    cancelPhase?: string;
    cancelReason?: string;
    queueBlocked?: boolean;
    fontParseCount?: number;
    fontCacheHits?: number;
    stageElapsedSeconds?: number;
    lastRequestAt?: string;
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
    boundedCancellation?: boolean;
    metrics?: TaskMetrics;
    canRepair?: boolean;
    canDownloadResult?: boolean;
    translationMemorySync?: "pending" | "synced";
    canRetryMemorySync?: boolean;
    qualitySummary?: TaskQualitySummary | null;
    translationSummary?: {
        total: number;
        succeeded: number;
        skipped: number;
        failed: number;
        pending: number;
    } | null;
    failedParagraphs?: {
        paragraphRef?: string;
        callRefs?: string[];
        operationRefs?: string[];
        taskAttempt?: number;
        page: number;
        paragraphId: string;
        attempts: number;
        errorType: string;
        reason: string;
        validationCode?: string;
        missingPlaceholderCount?: number;
        extraPlaceholderCount?: number;
    }[];
}

export type RequestKind = "translation" | "review" | "initialization";
export type RequestPurpose = RequestKind | "term_extraction";
export type MetricAvailability = "unavailable" | "partial" | "complete";

export interface RequestMetrics {
    attempts: number;
    succeeded: number;
    cancelled?: number;
    failed: number;
    active: number;
    retries: number;
    repairs?: number;
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
    activity?: {
        queued: number;
        retrying: number;
        fallbackPending: number;
        oldestRequestSeconds: number | null;
        lastParagraphCompletedAgoSeconds: number | null;
    };
    requests: RequestMetrics & {
        qps10s: number;
        byKind?: Partial<Record<RequestKind, RequestMetrics>>;
        byPurpose?: Partial<Record<RequestPurpose, RequestMetrics>>;
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
        byPurpose?: Partial<Record<RequestPurpose, TokenMetrics>>;
    };
    firstParagraphMs?: number | null;
    validation?: { accepted: number; partial: number; rejected: number };
    translationSummary?: {
        total: number;
        succeeded: number;
        failed: number;
        skipped: number;
        pending: number;
    } | null;
    journal?: { enabled: boolean; errors: number };
    throughput: {
        paragraphsPerMinute: number | null;
        outputTokensPerSecond?: number | null;
        etaSeconds: number | null;
    };
    referencesSkipped: number;
    stageDurations?: Record<string, number>;
}

export interface PluginTask extends ServerTaskSnapshot {
    model?: string;
    sourceLang?: string;
    targetLang?: string;
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
