import type { ApiProtocol } from "./apiCompatibility";

export interface LLMApiData {
    key: string;
    name?: string;
    service: string;
    providerPreset?: string;
    apiKey: string;
    apiUrl: string;
    model: string;
    /** Read only during legacy migration. */
    activate?: boolean;
    needsTest?: boolean;
    extraData?: Record<string, unknown>;
    apiProtocol?: ApiProtocol;
    reasoningMode?: "default" | "off";
    requestOptions?: Record<string, unknown>;
    cliPath?: string;
    reasoningEffort?: string;
    proxyMode?: "inherit" | "manual" | "direct";
    proxyUrl?: string;
    azureRegion?: string;
}

export interface ProviderPreset {
    id: string;
    service: string;
    group: "relay" | "official" | "local" | "translation";
    label: string;
    labelEn?: string;
    url?: string;
    key?: boolean;
    model?: boolean;
    protocol?: boolean;
    discovery?: boolean;
}

// One catalog for platform choice, defaults, and form capabilities. Preset IDs
// describe the UI; wire requests continue to use the existing service IDs.
export const PROVIDER_PRESETS: ProviderPreset[] = [
    {
        id: "openrouter",
        service: "openai",
        group: "relay",
        label: "OpenRouter",
        url: "https://openrouter.ai/api/v1",
        key: true,
        model: true,
        protocol: true,
        discovery: true,
    },
    {
        id: "custom",
        service: "openai",
        group: "relay",
        label: "自定义 OpenAI 兼容",
        labelEn: "Custom OpenAI-compatible",
        key: true,
        model: true,
        protocol: true,
        discovery: true,
    },
    {
        id: "openai",
        service: "openai",
        group: "official",
        label: "OpenAI",
        url: "https://api.openai.com/v1",
        key: true,
        model: true,
        protocol: true,
        discovery: true,
    },
    {
        id: "deepseek",
        service: "deepseek",
        group: "official",
        label: "DeepSeek",
        url: "https://api.deepseek.com/v1",
        key: true,
        model: true,
        protocol: true,
        discovery: true,
    },
    {
        id: "gemini",
        service: "gemini",
        group: "official",
        label: "Gemini",
        url: "https://generativelanguage.googleapis.com/v1beta/openai",
        key: true,
        model: true,
        protocol: true,
        discovery: true,
    },
    {
        id: "aliyundashscope",
        service: "aliyundashscope",
        group: "official",
        label: "阿里云百炼",
        labelEn: "Alibaba Cloud Model Studio",
        key: true,
        model: true,
        protocol: true,
    },
    {
        id: "siliconflow",
        service: "siliconflow",
        group: "official",
        label: "SiliconFlow",
        url: "https://api.siliconflow.cn/v1",
        key: true,
        model: true,
        discovery: true,
    },
    {
        id: "ollama",
        service: "ollama",
        group: "local",
        label: "Ollama",
        url: "http://localhost:11434",
        model: true,
    },
    {
        id: "codex",
        service: "codex",
        group: "local",
        label: "Codex",
        model: true,
        discovery: true,
    },
    {
        id: "claudecode",
        service: "claudecode",
        group: "local",
        label: "Claude Code",
        url: "claude",
        model: true,
    },
    {
        id: "deepl",
        service: "deepl",
        group: "translation",
        label: "DeepL",
        key: true,
    },
];

export const SERVICE_NAMES: Record<string, string> = Object.fromEntries(
    PROVIDER_PRESETS.filter((preset) => preset.id === preset.service).map(
        (preset) => [preset.service, preset.label],
    ),
);
export const RETIRED_SERVICES = [
    "zhipu",
    "grok",
    "groq",
    "modelscope",
    "qwenmt",
    "azureopenai",
    "azure",
    "xinference",
    "anythingllm",
];
export function isRetiredService(service: string): boolean {
    return RETIRED_SERVICES.includes(normalizeService(service));
}
export function resolveProviderPreset(
    api: Pick<LLMApiData, "service" | "apiUrl" | "providerPreset">,
): ProviderPreset | undefined {
    const saved = PROVIDER_PRESETS.find(
        (preset) =>
            preset.id === api.providerPreset && preset.service === api.service,
    );
    if (saved) return saved;
    if (api.service === "openai") {
        const official =
            /^https:\/\/api\.openai\.com\/v1(?:\/(?:chat\/completions|responses))?\/?$/i.test(
                api.apiUrl?.trim() || "",
            );
        return PROVIDER_PRESETS.find(
            (preset) => preset.id === (official ? "openai" : "custom"),
        );
    }
    return PROVIDER_PRESETS.find((preset) => preset.service === api.service);
}
export function assertSupportedProfile(api: Pick<LLMApiData, "service">) {
    if (!SERVICE_NAMES[api.service])
        throw new Error(
            "此配置的平台已移除或不受支持，请在管理配置中重新选择平台。",
        );
}
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
export function isRemovedService(value: string): boolean {
    return [
        "openaicompatible",
        "tencentmechinetranslation",
        "dify",
        "siliconflowfree",
    ].includes(normalizeService(value));
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
    const name = profileName(api);
    return api.model && !name.endsWith(` · ${api.model}`)
        ? `${name} · ${api.model}`
        : name;
}
export function migrateProfiles(legacy: LLMApiData[], service: string) {
    const hadLegacyRows = legacy.length > 0;
    legacy = legacy.filter((api) => !isRemovedService(api.service));
    const normalized = normalizeService(service);
    const matches = legacy.filter(
        (api) => api.activate && normalizeService(api.service) === normalized,
    );
    const profiles = legacy.map((original) => {
        const api = JSON.parse(JSON.stringify(original)) as LLMApiData;
        const oldName = api.service;
        api.service = normalizeService(api.service);
        if (!SERVICE_NAMES[api.service] && !isRetiredService(api.service)) {
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
    if (
        !hadLegacyRows &&
        !profiles.length &&
        (SERVICE_NAMES[normalized] || isRetiredService(normalized))
    ) {
        profiles.push({
            ...emptyLLMApi,
            key: "legacy-default",
            service: normalized,
            name: SERVICE_NAMES[normalized] || normalized,
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
