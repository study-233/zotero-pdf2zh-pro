import { getPref, setPref } from "../utils/prefs";

export const GLOSSARY_PACK_IDS = [
    "computing",
    "building",
    "physics",
    "environment",
    "medicine",
] as const;

export interface GlossaryPackReference {
    id: string;
    version: string;
    sha256: string;
}

export interface GlossaryPackSource {
    name: string;
    url: string;
    license: string;
    licenseUrl: string;
}

export interface InstalledGlossaryVersion {
    version: string;
    sha256: string;
    entryCount: number;
    sizeBytes: number;
    installedAt?: string;
}

export interface GlossaryPackInfo {
    id: string;
    name?: string | { zhCN: string; en: string };
    version?: string;
    sha256?: string;
    entryCount?: number;
    sizeBytes?: number;
    sourceLang?: string;
    targetLang?: string;
    sources?: GlossaryPackSource[];
    status:
        | "not_downloaded"
        | "downloading"
        | "installed"
        | "update_available"
        | "failed";
    installedVersions: InstalledGlossaryVersion[];
    download?: {
        state: string;
        receivedBytes?: number;
        totalBytes?: number;
        error?: string | null;
    };
}

export interface GlossaryCatalogResponse {
    packs: GlossaryPackInfo[];
}

function message(zh: string, en: string): string {
    return typeof Zotero !== "undefined" &&
        Zotero.locale &&
        !Zotero.locale.startsWith("zh")
        ? en
        : zh;
}

export function normalizeGlossaryServerUrl(value: string): string {
    const url = new URL(value.trim());
    if (!["http:", "https:"].includes(url.protocol))
        throw new Error(
            message(
                "服务器地址必须使用 http 或 https。",
                "Use an http or https server address.",
            ),
        );
    if (url.search || url.hash || url.username || url.password)
        throw new Error(
            message(
                "服务器地址不能包含查询参数、片段或登录信息。",
                "The server address must not include a query, fragment or credentials.",
            ),
        );
    return url.href.replace(/\/+$/, "");
}

function readSelections(): Record<string, string[]> {
    try {
        const value = JSON.parse(
            String(getPref("glossaryPackSelections") || "{}"),
        );
        if (!value || typeof value !== "object" || Array.isArray(value))
            throw new Error();
        if (
            Object.values(value).some(
                (ids) =>
                    !Array.isArray(ids) ||
                    ids.some((id) => typeof id !== "string"),
            )
        )
            throw new Error();
        return value;
    } catch {
        throw new Error(
            message(
                "无法读取词库勾选项，原设置已保留。",
                "Cannot read glossary selections. Existing settings were preserved.",
            ),
        );
    }
}

export function getSelectedGlossaryPackIds(serverUrl: string): string[] {
    return [
        ...new Set(
            readSelections()[normalizeGlossaryServerUrl(serverUrl)] || [],
        ),
    ].sort();
}

export function setSelectedGlossaryPackIds(
    serverUrl: string,
    ids: string[],
): void {
    const selections = readSelections();
    const key = normalizeGlossaryServerUrl(serverUrl);
    if (ids.length) selections[key] = [...new Set(ids)].sort();
    else delete selections[key];
    setPref("glossaryPackSelections", JSON.stringify(selections));
}

export function supportsGlossaryPackLanguage(
    source: string,
    target: string,
): boolean {
    const normalize = (value: string) =>
        value.trim().toLowerCase().replace(/_/g, "-");
    return (
        /^en(?:-|$)/.test(normalize(source)) &&
        ["zh", "zh-cn", "zh-hans"].includes(normalize(target))
    );
}

async function glossaryRequest(
    serverUrl: string,
    path: string,
    method = "GET",
    body?: object,
): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
        const response = await fetch(
            `${normalizeGlossaryServerUrl(serverUrl)}/glossaries${path}`,
            {
                method,
                headers: { "Content-Type": "application/json" },
                ...(body ? { body: JSON.stringify(body) } : {}),
                signal: controller.signal,
            },
        );
        if ((response.status === 404 && !path) || response.status === 405)
            throw new Error(
                message(
                    "当前服务端不支持下载词库，请升级服务端。",
                    "Upgrade the server to use downloadable glossaries.",
                ),
            );
        const payload = (await response.json()) as { message?: string };
        if (!response.ok)
            throw new Error(
                payload.message ||
                    message(
                        `词库操作失败 (${response.status})。`,
                        `Glossary operation failed (${response.status}).`,
                    ),
            );
        return payload;
    } finally {
        clearTimeout(timer);
    }
}

async function catalogRequest(
    serverUrl: string,
    path = "",
    method = "GET",
): Promise<GlossaryCatalogResponse> {
    const result = (await glossaryRequest(
        serverUrl,
        path,
        method,
    )) as GlossaryCatalogResponse;
    if (
        !Array.isArray(result?.packs) ||
        result.packs.some(
            (pack) =>
                !pack ||
                typeof pack.id !== "string" ||
                !Array.isArray(pack.installedVersions),
        )
    )
        throw new Error(
            message(
                "服务器返回的词库目录格式不正确。",
                "The server returned an invalid glossary catalog.",
            ),
        );
    return result;
}

export function listGlossaryPacks(
    serverUrl: string,
): Promise<GlossaryCatalogResponse> {
    return catalogRequest(serverUrl);
}

export function checkGlossaryUpdates(
    serverUrl: string,
): Promise<GlossaryCatalogResponse> {
    return catalogRequest(serverUrl, "/check-updates", "POST");
}

export async function downloadGlossaryPack(
    serverUrl: string,
    id: string,
    version?: string,
): Promise<void> {
    await glossaryRequest(
        serverUrl,
        `/${encodeURIComponent(id)}/download`,
        "POST",
        version ? { version } : {},
    );
}

export async function cancelGlossaryDownload(
    serverUrl: string,
    id: string,
): Promise<void> {
    await glossaryRequest(
        serverUrl,
        `/${encodeURIComponent(id)}/cancel`,
        "POST",
    );
}

export async function removeGlossaryPack(
    serverUrl: string,
    id: string,
): Promise<void> {
    await glossaryRequest(serverUrl, `/${encodeURIComponent(id)}`, "DELETE");
}

// Resolve once before submitting a batch. Later installs/preferences cannot change it.
export async function resolveSelectedGlossaryPacks(
    serverUrl: string,
    source: string,
    target: string,
): Promise<GlossaryPackReference[]> {
    if (!supportsGlossaryPackLanguage(source, target)) return [];
    const ids = getSelectedGlossaryPackIds(serverUrl);
    if (!ids.length) return [];
    const { packs } = await listGlossaryPacks(serverUrl);
    return ids.map((id) => {
        const pack = packs.find((candidate) => candidate.id === id);
        const installed =
            pack?.installedVersions.find(
                (candidate) =>
                    candidate.version === pack.version &&
                    candidate.sha256 === pack.sha256,
            ) || pack?.installedVersions[0];
        if (
            !installed ||
            !installed.version ||
            !/^[a-f0-9]{64}$/i.test(installed.sha256)
        )
            throw new Error(
                message(
                    `词库 ${id} 未安装或已损坏，请重新下载或取消勾选。`,
                    `Glossary ${id} is missing or damaged. Download it again or deselect it.`,
                ),
            );
        return { id, version: installed.version, sha256: installed.sha256 };
    });
}
