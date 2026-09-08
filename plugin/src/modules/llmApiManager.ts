import type { ApiProtocol } from "./apiCompatibility";

export interface LLMApiData {
    key: string;
    name?: string;
    service: string;
    apiKey: string;
    apiUrl: string;
    model: string;
    /** Read only during legacy migration. */
    activate?: boolean;
    needsTest?: boolean;
    extraData?: Record<string, unknown>;
    apiProtocol?: ApiProtocol;
    requestOptions?: Record<string, unknown>;
}

export const SERVICE_NAMES: Record<string, string> = {
    openai: "OpenAI 兼容（中转站 / 官方）",
    openaicompatible: "OpenAICompatible（旧预设）",
    siliconflowfree: "SiliconFlow Free",
    aliyundashscope: "AliyunDashScope",
    deepseek: "DeepSeek",
    gemini: "Gemini",
    siliconflow: "SiliconFlow",
    zhipu: "Zhipu",
    modelscope: "ModelScope",
    qwenmt: "QwenMt",
    azureopenai: "AzureOpenAI",
    azure: "Azure",
    deepl: "DeepL",
    ollama: "Ollama",
    xinference: "XInference",
    anythingllm: "AnythingLLM",
    dify: "Dify",
    grok: "Grok",
    groq: "Groq",
    tencentmechinetranslation: "Tencent",
    claudecode: "Claude Code",
};
export const emptyLLMApi: LLMApiData = {
    key: "",
    name: "",
    service: "openai",
    apiKey: "",
    apiUrl: "",
    model: "",
    apiProtocol: "auto",
    requestOptions: {},
    extraData: {},
};
export function normalizeService(value: string): string {
    return value.trim().toLowerCase().replace(/[-_]/g, "");
}
export function profileName(api: LLMApiData): string {
    if (api.name?.trim()) return api.name.trim();
    try {
        return new URL(api.apiUrl).hostname;
    } catch {
        return SERVICE_NAMES[api.service] || api.service;
    }
}
export function profileLabel(api: LLMApiData): string {
    return `${profileName(api)}${api.model ? ` · ${api.model}` : ""}`;
}
export function migrateProfiles(legacy: LLMApiData[], service: string) {
    const normalized = normalizeService(service);
    const matches = legacy.filter(
        (api) => api.activate && normalizeService(api.service) === normalized,
    );
    const profiles = legacy.map((original) => {
        const api = JSON.parse(JSON.stringify(original)) as LLMApiData;
        const oldName = api.service;
        api.service = normalizeService(api.service);
        if (!SERVICE_NAMES[api.service]) {
            if (api.apiUrl?.trim() && api.model?.trim()) api.service = "openai";
            api.name ||= oldName;
            api.needsTest = true;
        }
        api.name = profileName(api);
        api.apiProtocol ||= "chat_completions";
        api.extraData ||= {};
        api.requestOptions ||= {};
        delete api.activate;
        return api;
    });
    // The old UI also supported using an engine's defaults without an API row.
    if (!profiles.length && SERVICE_NAMES[normalized]) {
        profiles.push({
            ...emptyLLMApi,
            key: "legacy-default",
            service: normalized,
            name: SERVICE_NAMES[normalized],
            apiProtocol: "chat_completions",
        });
        return { profiles, selectedApiKey: "legacy-default" };
    }
    return {
        profiles,
        selectedApiKey: matches.length === 1 ? matches[0].key : "",
    };
}
export function selectedProfile(
    profiles: LLMApiData[],
    key: string,
): LLMApiData | null {
    const api = profiles.find((entry) => entry.key === key);
    return api ? (JSON.parse(JSON.stringify(api)) as LLMApiData) : null;
}
