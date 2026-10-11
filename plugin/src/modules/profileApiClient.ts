import axios from "axios";
import { getPref } from "../utils/prefs";
import { prepareApiForServer } from "./apiCompatibility";
import { assertSupportedProfile, type LLMApiData } from "./llmApiManager";
import type {
    CodexRuntime,
    ServerHealthResponse,
    ValidateConfigResponse,
} from "./pdf2zhTypes";

function serverUrl() {
    const url = getPref("new_serverip")?.toString().trim().replace(/\/+$/, "");
    if (!url) throw new Error("请先填写本地服务地址。");
    return url;
}
function safeError(error: unknown, api: LLMApiData): Error {
    const response = axios.isAxiosError(error) ? error.response : undefined;
    let message = response?.data?.message;
    if (typeof message !== "string")
        message =
            api.service === "codex"
                ? "无法完成请求，请检查本地服务、Codex CLI 路径及登录状态。"
                : "无法完成请求，请检查本地服务连接、API 地址和网络。";
    if (api.apiKey) message = message.split(api.apiKey).join("[已隐藏]");
    const failure = new Error(message);
    return Object.assign(failure, {
        code:
            typeof response?.data?.code === "string"
                ? response.data.code
                : undefined,
        attempts: validAttempts(response?.data?.attempts),
    });
}
function validAttempts(value: unknown): CodexRuntime["attempts"] {
    if (!Array.isArray(value)) return [];
    return value.filter(
        (entry) =>
            entry &&
            ["path", "source", "code", "message"].every(
                (key) => typeof entry[key] === "string",
            ),
    );
}
function runtimeInfo(value: unknown): CodexRuntime | undefined {
    if (value === undefined) return undefined;
    const data = value as CodexRuntime;
    if (
        !data ||
        typeof data.path !== "string" ||
        typeof data.source !== "string" ||
        typeof data.version !== "string" ||
        data.compatible !== true
    )
        throw new Error("Codex 检测结果格式不正确，请升级本地服务。");
    return { ...data, attempts: validAttempts(data.attempts) };
}
export async function codexDiscoverySupported(
    signal?: AbortSignal,
): Promise<boolean> {
    try {
        const { data } = await axios.get<ServerHealthResponse>(
            `${serverUrl()}/health`,
            { timeout: 5000, signal },
        );
        return data.capabilities?.codexDiscovery === true;
    } catch {
        return false;
    }
}
async function codexPost<T>(
    path: string,
    body: Record<string, unknown>,
    signal: AbortSignal | undefined,
    cancellable: boolean,
    timeout = 35000,
) {
    const requestId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const url = serverUrl();
    let cancelSent = false;
    const cancel = () => {
        if (cancelSent) return;
        cancelSent = true;
        void axios
            .post(
                `${url}/codex/discover/cancel`,
                { requestId },
                { timeout: 5000 },
            )
            .catch(() => {});
    };
    if (signal?.aborted) throw new Error("Codex 检测已取消。");
    if (cancellable) signal?.addEventListener("abort", cancel, { once: true });
    try {
        return await axios.post<T>(
            `${url}${path}`,
            { ...body, ...(cancellable ? { requestId } : {}) },
            { timeout, signal },
        );
    } catch (error) {
        // A client timeout or lost connection must not leave a probe/live test running.
        if (cancellable && axios.isAxiosError(error) && !error.response)
            cancel();
        throw error;
    } finally {
        if (cancellable) signal?.removeEventListener("abort", cancel);
    }
}
export async function discoverProfileCodex(
    api: LLMApiData,
    signal?: AbortSignal,
): Promise<CodexRuntime> {
    try {
        const { data } = await codexPost<{ codexRuntime: CodexRuntime }>(
            "/codex/discover",
            {
                cliPath: api.cliPath || "",
                proxyMode: api.proxyMode || "inherit",
                proxyUrl: api.proxyUrl || "",
                force: true,
            },
            signal,
            true,
        );
        const runtime = runtimeInfo(data.codexRuntime);
        if (!runtime) throw new Error("Codex 检测结果缺失。");
        return runtime;
    } catch (error) {
        if (axios.isAxiosError(error)) throw safeError(error, api);
        throw error;
    }
}
export async function testProfile(
    api: LLMApiData,
    signal?: AbortSignal,
    onRuntime?: (runtime: CodexRuntime) => void,
): Promise<string> {
    assertSupportedProfile(api);
    try {
        const url = serverUrl();
        const { data: health } = await axios.get<ServerHealthResponse>(
            `${url}/health`,
            { timeout: 5000, signal },
        );
        const prepared = prepareApiForServer(
            api,
            health.supportedApiProtocols,
            health.capabilities?.reasoningMode,
            health.capabilities?.codexCli,
            health.capabilities?.codexProxy,
        );
        const body = {
            service: api.service,
            llm_api: prepared.api,
            liveTest: true,
            sourceLang: getPref("sourceLang") || "en",
            targetLang: getPref("targetLang") || "zh-CN",
        };
        const { data } =
            api.service === "codex"
                ? await codexPost<ValidateConfigResponse>(
                      "/validate-config",
                      body,
                      signal,
                      health.capabilities?.codexDiscovery === true,
                      65000,
                  )
                : await axios.post<ValidateConfigResponse>(
                      `${url}/validate-config`,
                      body,
                      { timeout: 45000, signal },
                  );
        const runtime = runtimeInfo(data.codexRuntime);
        if (runtime) onRuntime?.(runtime);
        if (data.liveTest?.ok !== true) {
            throw new Error(
                data.liveTest?.message ||
                    data.diagnostics?.map((item) => item.message).join("；") ||
                    "API 测试未通过。",
            );
        }
        const protocol =
            data.resolvedProtocol === "responses"
                ? "Responses"
                : "Chat Completions";
        return `${api.service === "codex" ? "Codex 连接测试成功" : "API 测试成功"}${data.resolvedProtocol ? ` · ${protocol}` : ""}${prepared.warning ? `（${prepared.warning}）` : ""}${data.liveTest?.reasoningMessage ? ` · ${data.liveTest.reasoningMessage}` : ""}`;
    } catch (error) {
        if (axios.isAxiosError(error)) throw safeError(error, api);
        const message = error instanceof Error ? error.message : "API 测试失败";
        throw new Error(
            api.apiKey ? message.split(api.apiKey).join("[已隐藏]") : message,
        );
    }
}
export interface ProfileModelDetail {
    id: string;
    displayName?: string;
    defaultReasoningEffort?: string;
    supportedReasoningEfforts: string[];
}
export interface ProfileModelCatalog {
    codexRuntime?: CodexRuntime;
    models: string[];
    modelDetails?: ProfileModelDetail[];
}
export async function fetchProfileModelCatalog(
    api: LLMApiData,
    signal?: AbortSignal,
): Promise<ProfileModelCatalog> {
    assertSupportedProfile(api);
    try {
        let cancellable = false;
        if (api.service === "codex") {
            const { data: health } = await axios.get<ServerHealthResponse>(
                `${serverUrl()}/health`,
                { timeout: 5000, signal },
            );
            cancellable = health.capabilities?.codexDiscovery === true;
            prepareApiForServer(
                api,
                health.supportedApiProtocols,
                health.capabilities?.reasoningMode,
                health.capabilities?.codexCli,
                health.capabilities?.codexProxy,
            );
        }
        const { data } =
            api.service === "codex"
                ? await codexPost<ProfileModelCatalog>(
                      "/list-models",
                      {
                          service: "codex",
                          cliPath: api.cliPath || "",
                          proxyMode: api.proxyMode || "inherit",
                          proxyUrl: api.proxyUrl || "",
                      },
                      signal,
                      cancellable,
                      65000,
                  )
                : await axios.post<ProfileModelCatalog>(
                      `${serverUrl()}/list-models`,
                      {
                          service: api.service,
                          apiUrl: api.apiUrl,
                          apiKey: api.apiKey,
                          apiProtocol: api.apiProtocol || "auto",
                      },
                      { timeout: 20000, signal },
                  );
        if (data.codexRuntime !== undefined)
            data.codexRuntime = runtimeInfo(data.codexRuntime);
        if (
            !Array.isArray(data.models) ||
            data.models.some((id) => typeof id !== "string")
        )
            throw new Error("模型列表格式不正确，请手动填写模型。");
        if (
            data.modelDetails !== undefined &&
            (!Array.isArray(data.modelDetails) ||
                data.modelDetails.some(
                    (model) =>
                        !model ||
                        typeof model.id !== "string" ||
                        !Array.isArray(model.supportedReasoningEfforts) ||
                        model.supportedReasoningEfforts.some(
                            (effort) => typeof effort !== "string",
                        ) ||
                        (model.defaultReasoningEffort !== undefined &&
                            typeof model.defaultReasoningEffort !== "string") ||
                        (model.displayName !== undefined &&
                            typeof model.displayName !== "string"),
                ))
        )
            throw new Error("模型能力格式不正确，请升级本地服务后重试。");
        return data;
    } catch (error) {
        if (
            axios.isAxiosError(error) &&
            [404, 405].includes(error.response?.status || 0)
        ) {
            throw new Error(
                "当前本地服务不支持获取模型，请升级服务端或手动填写模型。",
            );
        }
        if (axios.isAxiosError(error)) throw safeError(error, api);
        throw error;
    }
}

export async function fetchProfileModels(api: LLMApiData): Promise<string[]> {
    return (await fetchProfileModelCatalog(api)).models;
}
