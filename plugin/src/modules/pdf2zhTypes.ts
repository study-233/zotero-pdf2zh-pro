export type OutputMode = "mono" | "dual";
export type ServerTaskStatus =
    | "queued"
    | "running"
    | "cancelling"
    | "completed"
    | "incomplete"
    | "failed"
    | "cancelled";

export interface ServerConfig {
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

export interface ServerTaskSnapshot {
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

export interface TaskMetrics {
    requests: {
        attempts: number;
        succeeded: number;
        failed: number;
        active: number;
        retries: number;
        qps10s: number;
        averageLatencyMs: number | null;
        p95LatencyMs: number | null;
    };
    localCache: { hits: number; misses: number; hitRate: number | null };
    providerCache: {
        hitTokens: number | null;
        missTokens: number | null;
        availability?: "unavailable" | "partial" | "complete";
        hitRate: number | null;
    };
    tokens: {
        input: number | null;
        output: number | null;
        total: number | null;
        availability?: "unavailable" | "partial" | "complete";
    };
    throughput: {
        paragraphsPerMinute: number | null;
        etaSeconds: number | null;
    };
    referencesSkipped: number;
}

export interface PluginTask extends ServerTaskSnapshot {
    itemID?: number;
    serverUrl: string;
    source: "local" | "remote";
    importState: "pending" | "importing" | "imported" | "failed" | "none";
    importError?: string;
    importedOutputs?: string[];
}

export interface ServerTaskEvent {
    type: "snapshot" | "task" | "deleted";
    task?: ServerTaskSnapshot;
    taskId?: string;
}
