import { diagnosticFetch } from "./diagnostics";
import { prepareApiForServer } from "./apiCompatibility";
import type { LLMApiData } from "./llmApiManager";
import type { ServerHealthResponse } from "./pdf2zhTypes";
import {
    DiagnosticMessage,
    OutputMode,
    ServerTaskSnapshot,
    ServerTaskList,
    ServerTaskEvent,
} from "./pdf2zhTypes";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";

type TaskListResponse = Partial<ServerTaskList> & {
    status?: string;
    tasks?: ServerTaskSnapshot[];
    message?: string;
};

type TaskCreateResponse = {
    status?: string;
    task?: ServerTaskSnapshot;
    message?: string;
};

export class ServerTaskClient {
    static async listTasks(
        serverUrl: string,
        page?: { cursor?: string; tracked: string[] },
    ): Promise<ServerTaskList> {
        let response = await diagnosticFetch(
            `${serverUrl}/tasks${page ? "/query" : ""}`,
            page
                ? {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ ...page, limit: 30 }),
                  }
                : undefined,
        );
        if (page && [404, 405].includes(response.status))
            response = await diagnosticFetch(`${serverUrl}/tasks`);
        if (!response.ok) {
            throw await this.readError(response);
        }

        const payload = (await response.json()) as TaskListResponse;
        if (!Array.isArray(payload.tasks)) {
            throw new Error("服务器返回的任务列表格式不正确");
        }
        if (
            payload.scope === "dashboard" &&
            (!payload.pagination ||
                typeof payload.pagination.hasMore !== "boolean" ||
                !Number.isInteger(payload.pagination.totalHistory) ||
                payload.pagination.totalHistory < 0 ||
                !(
                    payload.pagination.nextCursor === null ||
                    typeof payload.pagination.nextCursor === "string"
                ) ||
                !Array.isArray(payload.visibleTaskIds) ||
                !payload.visibleTaskIds.every((id) => typeof id === "string") ||
                !Array.isArray(payload.missingTaskIds) ||
                !payload.missingTaskIds.every((id) => typeof id === "string"))
        )
            throw new Error("服务器返回的历史分页格式不正确");
        return {
            tasks: payload.tasks,
            serverInstanceId: payload.serverInstanceId,
            revision: payload.revision,
            scope: payload.scope,
            visibleTaskIds: payload.visibleTaskIds,
            missingTaskIds: payload.missingTaskIds,
            pagination: payload.pagination,
        };
    }

    static async createTask(
        serverUrl: string,
        requestBody: Record<string, unknown>,
        onWarning?: (message: string) => void,
        file?: File,
    ): Promise<ServerTaskSnapshot> {
        let idempotent = false;
        let binaryUpload = false;
        const api = requestBody.llm_api as LLMApiData | undefined;
        const needsApiCheck = Boolean(
            api &&
            (api.service === "codex" ||
                api.service === "chatgpt" ||
                api.reasoningMode === "off" ||
                api.apiProtocol === "auto" ||
                api.apiProtocol === "responses" ||
                Object.keys(api.requestOptions || {}).length),
        );
        const needsGlossary =
            Array.isArray(requestBody.glossaryEntries) &&
            requestBody.glossaryEntries.length > 0;
        const needsReview = requestBody.semanticReview === true;
        const needsGlossaryPacks =
            Array.isArray(requestBody.glossaryPacks) &&
            requestBody.glossaryPacks.length > 0;
        if (
            needsApiCheck ||
            needsGlossary ||
            needsGlossaryPacks ||
            needsReview ||
            requestBody.requestId ||
            file
        ) {
            const healthResponse = await diagnosticFetch(`${serverUrl}/health`);
            if (!healthResponse.ok) throw await this.readError(healthResponse);
            const health =
                (await healthResponse.json()) as ServerHealthResponse;
            idempotent =
                health.capabilities?.taskSubmissionIdempotency === true;
            binaryUpload = health.capabilities?.binaryTaskUpload === true;
            const unavailable = [];
            if (needsGlossary && health.capabilities?.glossaryEntries !== true)
                unavailable.push("术语表");
            if (
                needsGlossaryPacks &&
                health.capabilities?.glossaryPacks !== true
            )
                unavailable.push("下载词库");
            if (needsReview && health.capabilities?.semanticReview !== true)
                unavailable.push("定向校对");
            if (unavailable.length) {
                throw new Error(
                    `当前服务端的${unavailable.join("、")}功能不可用，请升级服务端。` +
                        (needsGlossaryPacks
                            ? "已保留词库勾选项，本次未提交任务。"
                            : needsGlossary
                              ? "已保留术语表，本次未提交任务。"
                              : "也可以在设置中关闭定向校对后提交普通翻译。"),
                );
            }
            if (needsApiCheck && api) {
                const prepared = prepareApiForServer(
                    api,
                    health.supportedApiProtocols,
                    health.capabilities?.reasoningMode,
                    health.capabilities?.codexCli,
                    health.capabilities?.codexProxy,
                    health.capabilities?.chatgptPlan,
                );
                requestBody = { ...requestBody, llm_api: prepared.api };
                if (prepared.warning) {
                    onWarning?.(prepared.warning);
                }
            }
        }
        let body: string | FormData;
        if (file && binaryUpload) {
            body = new FormData();
            body.append("metadata", JSON.stringify(requestBody));
            body.append(
                "file",
                file,
                String(requestBody.fileName || file.name),
            );
        } else {
            // Only choose the legacy transport before submitting; never retry an
            // uncertain binary acceptance using a different request identity.
            body = JSON.stringify(
                file
                    ? {
                          ...requestBody,
                          fileContent:
                              await PDF2zhHelperFactory.blobToBase64(file),
                      }
                    : requestBody,
            );
        }
        for (let attempt = 0; ; attempt++) {
            let response;
            try {
                response = await diagnosticFetch(
                    `${serverUrl}/tasks`,
                    {
                        method: "POST",
                        ...(typeof body === "string"
                            ? {
                                  headers: {
                                      "Content-Type": "application/json",
                                  },
                              }
                            : {}),
                        body,
                    },
                    { totalMs: 600000, idleMs: 30000, headersMs: 600000 },
                );
                if (response.ok) {
                    const result =
                        (await response.json()) as TaskCreateResponse;
                    if (!result.task) throw new Error("服务器没有返回任务信息");
                    return result.task;
                }
            } catch (error) {
                // Only an advertised idempotent endpoint can replay an ambiguous POST.
                if (!idempotent || !requestBody.requestId || attempt >= 1)
                    throw error;
                continue;
            }
            throw Object.assign(await this.readError(response), {
                submissionConflict: response.status === 409,
            });
        }
    }

    static async resumeTask(serverUrl: string, taskId: string) {
        return this.postTaskAction(serverUrl, taskId, "resume");
    }

    static async retryMemorySync(serverUrl: string, taskId: string) {
        return this.postTaskAction(serverUrl, taskId, "retry-memory-sync");
    }

    static async cancelTask(
        serverUrl: string,
        taskId: string,
    ): Promise<ServerTaskSnapshot | undefined> {
        return this.postTaskAction(serverUrl, taskId, "cancel");
    }

    static async retryTask(
        serverUrl: string,
        taskId: string,
    ): Promise<ServerTaskSnapshot | undefined> {
        return this.postTaskAction(serverUrl, taskId, "retry");
    }

    static async repairTask(
        serverUrl: string,
        taskId: string,
    ): Promise<ServerTaskSnapshot | undefined> {
        return this.postTaskAction(serverUrl, taskId, "repair");
    }

    static async deleteTask(
        serverUrl: string,
        taskId: string,
    ): Promise<ServerTaskEvent> {
        const response = await diagnosticFetch(`${serverUrl}/tasks/${taskId}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw await this.readError(response);
        }
        const payload = (await response.json()) as Partial<ServerTaskEvent>;
        return {
            type: "deleted",
            taskId,
            serverInstanceId: payload.serverInstanceId,
            revision: payload.revision,
        };
    }

    static async clearFailedTasks(serverUrl: string): Promise<void> {
        const response = await diagnosticFetch(
            `${serverUrl}/tasks/clear-failed`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            },
        );
        if (!response.ok) {
            throw await this.readError(response);
        }
    }

    static async fetchResult(
        serverUrl: string,
        taskId: string,
        outputMode: OutputMode,
    ): Promise<Uint8Array> {
        const response = await diagnosticFetch(
            `${serverUrl}/tasks/${taskId}/result?mode=${outputMode}`,
            undefined,
            { totalMs: 600000, idleMs: 30000 },
        );
        if (!response.ok) {
            throw await this.readError(response);
        }

        return new Uint8Array(await response.arrayBuffer());
    }

    private static async postTaskAction(
        serverUrl: string,
        taskId: string,
        action: "cancel" | "retry" | "repair" | "resume" | "retry-memory-sync",
    ): Promise<ServerTaskSnapshot | undefined> {
        const response = await diagnosticFetch(
            `${serverUrl}/tasks/${taskId}/${action}`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            },
        );
        if (!response.ok) {
            throw await this.readError(response);
        }

        const result = (await response.json()) as { task?: ServerTaskSnapshot };
        return result.task;
    }

    private static async readError(response: Response): Promise<Error> {
        try {
            const payload = (await response.json()) as {
                message?: string;
                diagnostics?: DiagnosticMessage[];
                status?: string;
                code?: string;
            };
            const message = [
                payload.message || `服务器返回错误: ${response.status}`,
                this.formatDiagnostics(payload.diagnostics),
            ]
                .filter(Boolean)
                .join("\n\n");
            return Object.assign(new Error(message), {
                code: payload.code,
                diagnostics: payload.diagnostics,
            });
        } catch (_error) {
            return new Error(`服务器返回错误: ${response.status}`);
        }
    }

    private static formatDiagnostics(
        diagnostics?: DiagnosticMessage[],
    ): string {
        if (!diagnostics?.length) {
            return "";
        }
        return diagnostics
            .map((diagnostic) => {
                const line = [
                    `[${diagnostic.severity}]`,
                    diagnostic.code,
                    diagnostic.message,
                ]
                    .filter(Boolean)
                    .join(" ");
                if (diagnostic.suggestion) {
                    return `${line}\n建议: ${diagnostic.suggestion}`;
                }
                return line;
            })
            .join("\n");
    }
}
