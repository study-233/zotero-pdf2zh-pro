import axios from "axios";
import { getPref } from "../utils/prefs";
import { prepareApiForServer } from "./apiCompatibility";
import type { LLMApiData } from "./llmApiManager";
import type {
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
    return new Error(message);
}
export async function testProfile(api: LLMApiData): Promise<string> {
    try {
        const url = serverUrl();
        const { data: health } = await axios.get<ServerHealthResponse>(
            `${url}/health`,
            { timeout: 5000 },
        );
        const prepared = prepareApiForServer(
            api,
            health.supportedApiProtocols,
            health.capabilities?.reasoningMode,
            health.capabilities?.codexCli,
            health.capabilities?.codexProxy,
        );
        const { data } = await axios.post<ValidateConfigResponse>(
            `${url}/validate-config`,
            {
                service: api.service,
                llm_api: prepared.api,
                liveTest: true,
                sourceLang: getPref("sourceLang") || "en",
                targetLang: getPref("targetLang") || "zh-CN",
            },
            { timeout: api.service === "codex" ? 65000 : 45000 },
        );
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
    models: string[];
    modelDetails?: ProfileModelDetail[];
}
export async function fetchProfileModelCatalog(
    api: LLMApiData,
): Promise<ProfileModelCatalog> {
    try {
        if (
            api.service === "codex" &&
            api.proxyMode &&
            api.proxyMode !== "inherit"
        ) {
            const { data: health } = await axios.get<ServerHealthResponse>(
                `${serverUrl()}/health`,
                { timeout: 5000 },
            );
            prepareApiForServer(
                api,
                health.supportedApiProtocols,
                health.capabilities?.reasoningMode,
                health.capabilities?.codexCli,
                health.capabilities?.codexProxy,
            );
        }
        const { data } = await axios.post<ProfileModelCatalog>(
            `${serverUrl()}/list-models`,
            api.service === "codex"
                ? {
                      service: "codex",
                      cliPath: api.cliPath || "",
                      proxyMode: api.proxyMode || "inherit",
                      proxyUrl: api.proxyUrl || "",
                  }
                : {
                      service: api.service,
                      apiUrl: api.apiUrl,
                      apiKey: api.apiKey,
                      apiProtocol: api.apiProtocol || "auto",
                  },
            { timeout: api.service === "codex" ? 35000 : 20000 },
        );
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
