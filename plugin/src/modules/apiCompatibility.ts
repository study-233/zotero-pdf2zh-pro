import type { LLMApiData } from "./llmApiManager";

export type ApiProtocol = "auto" | "chat_completions" | "responses";

export function prepareApiForServer<
    T extends Pick<
        LLMApiData,
        "apiUrl" | "apiProtocol" | "requestOptions" | "reasoningMode"
    >,
>(
    api: T,
    supportedProtocols?: string[],
    supportsReasoningMode?: boolean,
): { api: T; warning?: string } {
    if (api.reasoningMode === "off" && supportsReasoningMode !== true)
        throw new Error("当前 Python 服务不支持关闭推理，请先升级服务端。");
    if (supportedProtocols?.includes("responses")) return { api };
    if (api.apiProtocol === "responses") {
        throw new Error("当前 Python 服务不支持 Responses，请先升级服务端。");
    }
    if (Object.keys(api.requestOptions || {}).length) {
        throw new Error("当前 Python 服务不支持额外请求参数，请先升级服务端。");
    }
    if (api.apiProtocol === "auto") {
        return {
            api: {
                ...api,
                apiProtocol: "chat_completions",
                apiUrl: api.apiUrl.replace(/\/responses\/?$/, ""),
            },
            warning:
                "当前 Python 服务不支持协议识别，将使用 Chat Completions；升级服务端后可自动识别 Responses。",
        };
    }
    return { api };
}
