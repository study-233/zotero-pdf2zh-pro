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
    static async listTasks(serverUrl: string): Promise<ServerTaskList> {
        const response = await fetch(`${serverUrl}/tasks`);
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        const payload = (await response.json()) as TaskListResponse;
        if (!Array.isArray(payload.tasks)) {
            throw new Error("服务器返回的任务列表格式不正确");
        }
        return {
            tasks: payload.tasks,
            serverInstanceId: payload.serverInstanceId,
            revision: payload.revision,
        };
    }

    static async createTask(
        serverUrl: string,
        requestBody: Record<string, unknown>,
    ): Promise<ServerTaskSnapshot> {
        const api = requestBody.llm_api as LLMApiData | undefined;
        const needsApiCheck = Boolean(
            api &&
            (api.apiProtocol === "auto" ||
                api.apiProtocol === "responses" ||
                Object.keys(api.requestOptions || {}).length),
        );
        const needsGlossary =
            Array.isArray(requestBody.glossaryEntries) &&
            requestBody.glossaryEntries.length > 0;
        const needsReview = requestBody.semanticReview === true;
        if (needsApiCheck || needsGlossary || needsReview) {
            const healthResponse = await fetch(`${serverUrl}/health`);
            if (!healthResponse.ok)
                throw new Error(await this.readErrorMessage(healthResponse));
            const health =
                (await healthResponse.json()) as ServerHealthResponse;
            const unavailable = [];
            if (needsGlossary && health.capabilities?.glossaryEntries !== true)
                unavailable.push("术语表");
            if (needsReview && health.capabilities?.semanticReview !== true)
                unavailable.push("定向校对");
            if (unavailable.length) {
                throw new Error(
                    `当前服务端的${unavailable.join("、")}功能不可用，请升级服务端。` +
                        (needsGlossary
                            ? "已保留术语表，本次未提交任务。"
                            : "也可以在设置中关闭定向校对后提交普通翻译。"),
                );
            }
            if (needsApiCheck && api) {
                const prepared = prepareApiForServer(
                    api,
                    health.supportedApiProtocols,
                );
                requestBody = { ...requestBody, llm_api: prepared.api };
                if (prepared.warning) {
                    new ztoolkit.ProgressWindow("API 兼容提示")
                        .createLine({ text: prepared.warning, type: "default" })
                        .show();
                }
            }
        }
        const response = await PDF2zhHelperFactory.retryOperation(() =>
            fetch(`${serverUrl}/tasks`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(requestBody),
            }),
        );
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        const result = (await response.json()) as TaskCreateResponse;
        if (!result.task) {
            throw new Error("服务器没有返回任务信息");
        }
        return result.task;
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
        const response = await fetch(`${serverUrl}/tasks/${taskId}`, {
            method: "DELETE",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
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
        const response = await fetch(`${serverUrl}/tasks/clear-failed`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }
    }

    static async fetchResult(
        serverUrl: string,
        taskId: string,
        outputMode: OutputMode,
    ): Promise<Uint8Array> {
        const response = await fetch(
            `${serverUrl}/tasks/${taskId}/result?mode=${outputMode}`,
        );
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        return new Uint8Array(await response.arrayBuffer());
    }

    private static async postTaskAction(
        serverUrl: string,
        taskId: string,
        action: "cancel" | "retry" | "repair",
    ): Promise<ServerTaskSnapshot | undefined> {
        const response = await fetch(`${serverUrl}/tasks/${taskId}/${action}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
        });
        if (!response.ok) {
            throw new Error(await this.readErrorMessage(response));
        }

        const result = (await response.json()) as { task?: ServerTaskSnapshot };
        return result.task;
    }

    private static async readErrorMessage(response: Response): Promise<string> {
        try {
            const payload = (await response.json()) as {
                message?: string;
                diagnostics?: DiagnosticMessage[];
                status?: string;
            };
            return [
                payload.message || `服务器返回错误: ${response.status}`,
                this.formatDiagnostics(payload.diagnostics),
            ]
                .filter(Boolean)
                .join("\n\n");
        } catch (_error) {
            return `服务器返回错误: ${response.status}`;
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
