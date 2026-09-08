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
        message = "无法完成请求，请检查本地服务连接、API 地址和网络。";
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
        const prepared = prepareApiForServer(api, health.supportedApiProtocols);
        const { data } = await axios.post<ValidateConfigResponse>(
            `${url}/validate-config`,
            {
                service: api.service,
                llm_api: prepared.api,
                liveTest: true,
                sourceLang: getPref("sourceLang") || "en",
                targetLang: getPref("targetLang") || "zh-CN",
            },
            { timeout: 45000 },
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
        return `API 测试成功${data.resolvedProtocol ? ` · ${protocol}` : ""}${prepared.warning ? `（${prepared.warning}）` : ""}`;
    } catch (error) {
        if (axios.isAxiosError(error)) throw safeError(error, api);
        const message = error instanceof Error ? error.message : "API 测试失败";
        throw new Error(
            api.apiKey ? message.split(api.apiKey).join("[已隐藏]") : message,
        );
    }
}
export async function fetchProfileModels(api: LLMApiData): Promise<string[]> {
    try {
        const { data } = await axios.post<{ models: string[] }>(
            `${serverUrl()}/list-models`,
            {
                apiUrl: api.apiUrl,
                apiKey: api.apiKey,
                apiProtocol: api.apiProtocol || "auto",
            },
            { timeout: 20000 },
        );
        if (
            !Array.isArray(data.models) ||
            data.models.some((id) => typeof id !== "string")
        )
            throw new Error("模型列表格式不正确，请手动填写模型。");
        return data.models;
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
