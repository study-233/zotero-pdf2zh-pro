import { config } from "../../package.json";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { loadGlossaryEntries } from "./glossaryStore";
import { recordDiagnostic } from "./diagnostics";
import { getPref, setPref } from "../utils/prefs";
import { getString } from "../utils/locale";
import { loadProfiles } from "./profileStore";
import { profileLabel } from "./llmApiManager";
import { createSelectionRequest, SelectionRequest } from "./selectionRequest";
import { watchSelectionDocuments } from "./selectionEvents";
import { getSelectionContext } from "./selectionContext";
import type { DictionaryEntry } from "./selectionDictionaryStore";
import {
    checkedContextMeaning,
    contextCopyText,
    type ContextMeaning,
} from "./selectionFormatting";
import { createDictionaryRequest } from "./selectionDictionaryService";
import { stopSelectionAudio } from "./selectionAudio";
import {
    saveFavorite,
    deleteFavorite,
    listFavorites,
    favoriteIdentity,
    favoriteLibrary,
    type SelectionFavorite,
} from "./selectionFavorites";
import {
    registerFavoritesPane,
    unregisterFavoritesPane,
    openFavoritesPane,
} from "./selectionFavoritesPane";
import {
    selectionElement,
    SelectionResult,
    SelectionLearning,
    SelectionControls,
} from "./selectionPopup";
import { createSelectionView, type SelectionView } from "./selectionView";
import {
    registerSelectionPane,
    unregisterSelectionPane,
} from "./selectionPane";

export interface MemoryResult {
    matched: boolean;
    status?: string;
    entry?: DictionaryEntry;
    contextMeaning?: ContextMeaning;
    model?: string;
    saved?: boolean;
    source?: string;
    translation?: string;
    page?: number;
    matchType?: "exact" | "contained";
    provider?: string;
    cached?: boolean;
    formattingIncomplete?: boolean;
}

export function normalizeSelection(text: string): string {
    return text
        .normalize("NFKC")
        .replace(/\u00ad/g, "")
        .replace(/\s+/g, " ")
        .trim();
}

function checkedResult(value: unknown, memory: boolean): MemoryResult {
    if (!value || typeof value !== "object")
        throw new Error("本地服务返回格式不正确。");
    const result = value as MemoryResult;
    if (memory && typeof result.matched !== "boolean")
        throw new Error("本地服务返回格式不正确。");
    if (
        (!memory || result.matched) &&
        (typeof result.translation !== "string" || !result.translation.trim())
    )
        throw new Error("本地服务返回空译文，请重新划选重试。");
    return result;
}

export async function lookupSelection(
    request: SelectionRequest,
    serverUrl: string,
    documentFingerprint: string,
    text: string,
    page: number | undefined,
    targetLang: string,
): Promise<MemoryResult> {
    const response = await request.post(
        `${serverUrl.replace(/\/$/, "")}/translation-lookup`,
        {
            documentFingerprint,
            text,
            page,
            side: "source",
            targetLang,
        },
        10000,
    );
    if (!response.ok)
        throw new Error("记忆查询失败，请检查本地服务是否已升级并启动。");
    return checkedResult(response.data, true);
}

export function lookupGlossary(
    text: string,
    targetLang: string,
): string | undefined {
    const selected = normalizeSelection(text).toLowerCase();
    const language = targetLang.toLowerCase().replace(/_/g, "-");
    const entries = loadGlossaryEntries().filter(
        (entry) => normalizeSelection(entry.source).toLowerCase() === selected,
    );
    return (
        entries.find(
            (entry) =>
                entry.tgt_lng.toLowerCase().replace(/_/g, "-") === language,
        ) || entries.find((entry) => !entry.tgt_lng)
    )?.target;
}

export async function translateSelection(
    request: SelectionRequest,
    fingerprint: string,
    text: string,
    page: number | undefined,
    context: string,
    options: {
        mode?: "dictionary" | "context";
        refresh?: boolean;
        allowGenerate?: boolean;
        freeFallback?: boolean;
        onDelta?: (text: string) => void;
        onStreamStart?: () => void;
        plain?: boolean;
    } = {},
): Promise<MemoryResult> {
    let selectionProvider =
        !options.freeFallback &&
        (options.mode || getPref("selectionTranslationProvider") === "profile")
            ? "profile"
            : "bing";
    const settings = PDF2zhHelperFactory.getServerConfig(
        selectionProvider === "profile" &&
            !(options.mode === "dictionary" && options.allowGenerate === false),
        getPref("selectionApiKey")?.toString() || "",
    );
    if (
        selectionProvider === "profile" &&
        options.allowGenerate !== false &&
        !settings.apiConfig
    )
        if (options.mode) throw new Error("详细释义和语境解释需要配置模型。");
        else selectionProvider = "bing";
    if (!options.mode && selectionProvider === "profile" && !settings.apiConfig)
        selectionProvider = "bing";
    const needsCodexProxy =
        selectionProvider === "profile" &&
        settings.service === "codex" &&
        options.allowGenerate !== false &&
        !!settings.apiConfig?.proxyMode &&
        settings.apiConfig.proxyMode !== "inherit";
    let stream = false;
    const wantsStream =
        !options.plain &&
        !options.mode &&
        options.allowGenerate !== false &&
        selectionProvider === "profile" &&
        getPref("selectionStream") !== false &&
        !!options.onDelta;
    if (
        options.mode ||
        options.refresh ||
        options.allowGenerate === false ||
        needsCodexProxy ||
        wantsStream
    ) {
        const capability = await request.post(
            `${settings.serverUrl.replace(/\/$/, "")}/selection-capabilities`,
            {},
            10000,
        );
        stream =
            wantsStream &&
            capability.ok &&
            (capability.data as { selectionStream?: boolean })
                ?.selectionStream === true;
        if (
            needsCodexProxy &&
            (!capability.ok ||
                (capability.data as { codexProxy?: boolean })?.codexProxy !==
                    true)
        )
            throw new Error(
                "当前 Python 服务不支持 Codex 代理设置，请先升级服务端。",
            );
        if (
            (options.mode ||
                options.refresh ||
                options.allowGenerate === false) &&
            (!capability.ok ||
                !(capability.data as { selectionLearning?: boolean })
                    ?.selectionLearning)
        )
            throw new Error("请升级本地服务后使用个人词典、语境含义或重翻。");
    }
    if (stream) options.onStreamStart?.();
    const response = await request.post(
        `${settings.serverUrl.replace(/\/$/, "")}/translate-text${stream ? "/stream" : ""}`,
        {
            documentFingerprint: fingerprint,
            text,
            page,
            context: selectionProvider === "profile" ? context : "",
            mode: options.mode || "translate",
            cachePolicy: options.refresh ? "refresh" : "prefer",
            allowGenerate: options.allowGenerate !== false,
            selectionProvider,
            memoryPolicy: "exact",
            source: settings.sourceLang,
            target: settings.targetLang,
            // Older servers reject this service name instead of accidentally
            // falling back to an environment-configured paid model.
            ...(selectionProvider === "profile"
                ? {
                      service: settings.service,
                      ...(settings.apiConfig
                          ? { llm_api: settings.apiConfig }
                          : {}),
                  }
                : { service: "bing" }),
            glossaryEntries: loadGlossaryEntries(),
        },
        50000,
        selectionProvider === "profile" &&
            (settings.service === "codex" || stream) &&
            options.allowGenerate !== false
            ? {
                  cancelUrl: `${settings.serverUrl.replace(/\/$/, "")}/cancel-text`,
              }
            : undefined,
        stream ? options.onDelta : undefined,
    );
    if (!response.ok) {
        const errors: Record<string, string> = {
            stream_unsupported: "模型服务不支持流式，请使用普通模式重试。",
            context_unavailable: "无法读取可靠的论文上下文，请重新划选后重试。",
            invalid_output: "模型返回格式不正确，请重试。",
            invalid_config: "划词翻译配置无效，请检查设置。",
            unsupported_selection_provider:
                "当前服务不支持此划词配置，请升级服务端并使用 OpenAI 兼容或 Codex 配置。",
            provider_timeout: "翻译请求超时，请重新划选重试。",
            provider_error: "模型请求失败，请检查翻译配置与网络。",
            empty_output: "翻译服务返回空内容，请重新划选重试。",
            selection_busy: "已有翻译请求正在处理，请稍后重新划选。",
            selection_cancelled: "翻译请求已取消。",
            provider_quota:
                selectionProvider === "bing"
                    ? "免费翻译额度已用尽或服务限流，请稍后重试。未调用模型。"
                    : "模型额度不足或服务限流，请检查账号用量后重试。",
            codex_not_installed: "未找到 Codex CLI，请检查配置中的 CLI 路径。",
            codex_not_logged_in: "Codex 尚未登录，请在终端运行 codex login。",
            codex_model_unavailable:
                "当前 Codex 账号无法使用所选模型，请检查配置。",
            codex_quota_exhausted:
                "Codex 额度不足，请检查账号用量或手动选择其他配置。",
            codex_incompatible: "Codex CLI 版本不兼容，请按安装说明升级。",
        };
        const code = (response.data as { code?: string } | null)?.code;
        const message =
            selectionProvider === "bing" && code === "provider_error"
                ? "必应暂时不可用，请稍后重试。未调用模型。"
                : errors[code || ""];
        const serverMessage = (response.data as { message?: unknown } | null)
            ?.message;
        const codexMessage =
            settings.service === "codex" &&
            code?.startsWith("codex_") &&
            typeof serverMessage === "string"
                ? serverMessage
                : undefined;
        throw new Error(
            codexMessage || message || "划词服务不可用，请检查本地服务。",
        );
    }
    if (
        options.allowGenerate === false &&
        (response.data as MemoryResult)?.status === "miss"
    )
        return response.data as MemoryResult;
    const result = checkedResult(response.data, false);
    if (options.mode === "context" && result.contextMeaning !== undefined) {
        result.contextMeaning = checkedContextMeaning(result.contextMeaning);
        result.translation = contextCopyText(result.contextMeaning);
    }
    if (
        options.mode === "dictionary" &&
        (!result.entry ||
            !Array.isArray(result.entry.senses) ||
            !result.entry.senses.length)
    )
        throw new Error("本地服务返回的词条格式不正确。");
    return result;
}

type Reader = _ZoteroTypes.ReaderInstance;
const fingerprints = new WeakMap<
    Reader,
    { signature: string; value: Promise<string> }
>();
let registered = false;
let nextId = 0;
let tabObserver: string | undefined;

async function readerFingerprint(reader: Reader): Promise<string> {
    const attachment = reader.itemID && Zotero.Items.get(reader.itemID);
    const path = attachment && (await attachment.getFilePathAsync());
    if (!path) throw new Error("请先下载此 PDF 附件。");
    const stat = await IOUtils.stat(path);
    const signature = `${path}:${stat.size}:${stat.lastModified}`;
    let cached = fingerprints.get(reader);
    if (!cached || cached.signature !== signature) {
        cached = { signature, value: IOUtils.computeHexDigest(path, "sha256") };
        fingerprints.set(reader, cached);
    }
    try {
        return await cached.value;
    } catch {
        fingerprints.delete(reader);
        throw new Error("无法读取 PDF 文件指纹。");
    }
}

type ReaderState = {
    reader: Reader;
    doc: Document;
    id: string;
    key: string;
    version: number;
    disposed: boolean;
    dismissed: boolean;
    timer?: ReturnType<typeof setTimeout>;
    request?: SelectionRequest;
    dictionaryRequest?: ReturnType<typeof createDictionaryRequest>;
    original?: string;
    position?: { pageIndex: number; rects?: number[][] };
    onDelta?: (text: string) => void;
    onStreamStart?: () => void;
    onStreamEnd?: (error?: string) => void;
    learningRequests?: Set<SelectionRequest>;
    popup?: SelectionView;
    anchor?: HTMLElement;
    cleanup?: () => void;
    queryKey?: string;
    results?: Map<string, SelectionResult>;
    context?: Pick<
        SelectionLearning["context"],
        "text" | "contextMeaning" | "origin"
    >;
    contextModel?: string;
};
const states = new Map<Reader, ReaderState>();

function invalidate(state: ReaderState) {
    if (state.popup) stopSelectionAudio(state.popup.card);
    state.version++;
    if (state.timer !== undefined) clearTimeout(state.timer);
    state.timer = undefined;
    state.request?.abort();
    state.request = undefined;
    state.dictionaryRequest?.abort();
    state.dictionaryRequest = undefined;
    for (const request of state.learningRequests || []) request.abort();
    state.learningRequests?.clear();
}
function dismiss(state: ReaderState, force = false) {
    if (!force && (state.popup?.pinned || state.popup?.docked)) return;
    invalidate(state);
    state.dismissed = true;
    state.queryKey = undefined;
    state.results?.clear();
    state.context = undefined;
    const popup = state.popup;
    state.popup = undefined;
    popup?.close();
    state.doc.getElementById(state.id)?.remove();
}
function dispose(state: ReaderState) {
    if (state.disposed) return;
    state.disposed = true;
    try {
        dismiss(state, true);
    } finally {
        try {
            state.cleanup?.();
        } finally {
            states.delete(state.reader);
        }
    }
}
function insideCard(state: ReaderState, event: Event) {
    try {
        return Boolean(state.popup?.card.contains(event.target as Node));
    } catch {
        return false;
    }
}
function clearOrphans(doc: Document) {
    for (const node of Array.from(
        doc.querySelectorAll(".pdf2zh-selection-card"),
    ))
        node?.parentNode?.removeChild(node);
}
function getState(reader: Reader, doc: Document) {
    const previous = states.get(reader);
    if (previous?.doc === doc) return previous;
    if (previous) dispose(previous);
    clearOrphans(doc);
    const state: ReaderState = {
        reader,
        doc,
        id: `pdf2zh-selection-card-${++nextId}`,
        key: "",
        version: 0,
        disposed: false,
        dismissed: false,
    };
    states.set(reader, state);
    let owner: Document | undefined;
    try {
        owner = (reader._window || Zotero.getMainWindow())?.document;
    } catch {
        /* Reader may be the only window. */
    }
    state.cleanup = watchSelectionDocuments(
        doc,
        owner,
        {
            input(event) {
                if (insideCard(state, event)) return;
                // A physical interaction starts a fresh selection session, even if
                // the next selection has the same text and coordinates.
                dismiss(state);
                state.key = "";
                state.dismissed = false;
            },
            dismiss(event) {
                if (!insideCard(state, event)) dismiss(state);
            },
            escape() {
                if (!state.popup?.docked) dismiss(state, true);
            },
            dispose() {
                dispose(state);
            },
        },
        () => recordDiagnostic("selection_listener_degraded"),
    );
    return state;
}
function memoryResult(memory: MemoryResult): SelectionResult {
    return {
        kind: memory.matchType === "exact" ? "translation" : "paragraph",
        text: memory.translation!,
        origin: "已有全文译文",
        incomplete: memory.formattingIncomplete,
    };
}
function isShortSelection(text: string) {
    return (
        normalizeSelection(text).split(" ").length <= 3 && text.length <= 100
    );
}

/** Only a single English token opens the dictionary automatically. */
export function selectionRoute(text: string, from: string, to: string) {
    const normalized = normalizeSelection(text);
    const dictionary =
        /^en(?:-|$)/i.test(from) &&
        /^(zh|zh-cn|zh-hans)$/i.test(to.replace(/_/g, "-")) &&
        normalized.length <= 100 &&
        /^[a-z]+(?:['’-][a-z]+)*(?: [a-z]+(?:['’-][a-z]+)*){0,2}$/i.test(
            normalized,
        );
    return {
        dictionary,
        action:
            dictionary && !normalized.includes(" ")
                ? ("lookup" as const)
                : ("translate" as const),
    };
}

function createLearning(
    state: ReaderState,
    current: () => boolean,
    text: string,
    page: number | undefined,
) {
    let refreshMode: "dictionary" | "translate" = "translate";
    let modelAvailable = false;
    let modelError: string | undefined;
    try {
        modelAvailable = Boolean(
            PDF2zhHelperFactory.getServerConfig(
                true,
                getPref("selectionApiKey")?.toString() || "",
            ).apiConfig,
        );
    } catch (error) {
        modelError =
            error instanceof Error ? error.message : "翻译配置无法读取。";
    }
    let personal: MemoryResult | undefined;
    let contextValue = state.context?.text || "";
    let automaticDone = false;
    let initializing = true;
    let freeFallback = !modelAvailable && !modelError;
    const popup = state.popup!;
    const view: SelectionLearning = {
        refreshLabel: "重新翻译",
        onRefresh: () => {
            if (!view.refreshHidden) void run(refreshMode, true);
        },
        busy: true,
        context: {
            title: "当前语境含义",
            actionLabel: modelAvailable
                ? contextValue
                    ? "重翻语境"
                    : "结合上下文解释"
                : undefined,
            error: modelAvailable
                ? undefined
                : modelError || "详细释义和语境解释需要配置模型。",
            onAction: () => void run("context", Boolean(contextValue)),
            ...state.context,
        },
    };
    const render = () => {
        if (current()) popup.setLearning(view);
    };
    render();
    const call = async (
        mode: "dictionary" | "context" | "translate",
        refresh: boolean,
        allowGenerate = true,
    ) => {
        const request = createSelectionRequest();
        (state.learningRequests ||= new Set()).add(request);
        try {
            const fingerprint =
                mode === "dictionary"
                    ? ""
                    : await readerFingerprint(state.reader);
            if (!current()) return undefined;
            const context =
                mode === "dictionary"
                    ? ""
                    : mode === "context" ||
                        (!freeFallback &&
                            getPref("selectionTranslationProvider") ===
                                "profile")
                      ? await getSelectionContext(state.reader, page, text)
                      : "";
            if (!current()) return undefined;
            if (
                mode === "context" &&
                (!context.trim() ||
                    normalizeSelection(context) === normalizeSelection(text) ||
                    (text !== state.original &&
                        !normalizeSelection(context)
                            .toLowerCase()
                            .includes(normalizeSelection(text).toLowerCase())))
            )
                throw new Error("无法读取可靠的论文上下文，请重新划选后重试。");
            return await translateSelection(
                request,
                fingerprint,
                text,
                page,
                context,
                {
                    ...(mode !== "translate" ? { mode } : {}),
                    refresh,
                    allowGenerate,
                    freeFallback: mode === "translate" && freeFallback,
                    onDelta: mode === "translate" ? state.onDelta : undefined,
                    onStreamStart:
                        mode === "translate" ? state.onStreamStart : undefined,
                },
            );
        } finally {
            state.learningRequests?.delete(request);
        }
    };
    function showPersonal(result: MemoryResult) {
        personal = result;
        const entry = result.entry!;
        const origin = `AI 补充 · ${result.model || "模型"}${result.cached ? " · 本地词典" : ""}`;
        const notice =
            result.saved === false ? "本次释义未能保存到本地。" : undefined;
        refreshMode = "dictionary";
        view.refreshHidden = !modelAvailable;
        view.refreshLabel = "重新生成";
        popup.render({
            kind: "dictionary",
            ...entry,
            aiGenerated: true,
            text: [
                ...entry.senses.map((sense) =>
                    [sense.pos, sense.chinese].filter(Boolean).join(" "),
                ),
                entry.usage,
            ]
                .filter(Boolean)
                .join("\n"),
            origin,
            notice,
        });
    }

    async function run(
        mode: "dictionary" | "context" | "translate",
        refresh: boolean,
    ) {
        if (
            !current() ||
            initializing ||
            view.busy ||
            view.context.busy ||
            (mode === "context" && !modelAvailable)
        )
            return;
        if (mode === "context") {
            view.context.busy = true;
            view.context.error = undefined;
        } else {
            view.busy = true;
            view.error = undefined;
        }
        render();
        try {
            const result = await call(mode, refresh);
            if (!current() || !result) return;
            if (mode === "dictionary") {
                showPersonal(result);
                view.refreshLabel = "重新生成";
            } else if (mode === "context") {
                contextValue = result.translation!;
                Object.assign(view.context, {
                    text: contextValue,
                    contextMeaning: result.contextMeaning,
                    actionLabel: "重翻语境",
                    origin: `${result.model || "模型"}${result.cached ? " · 本地缓存" : ""}`,
                    error:
                        result.saved === false
                            ? "本次语境含义未能保存到本地。"
                            : undefined,
                });
            } else
                popup.render({
                    kind: "translation",
                    text: result.translation!,
                    origin:
                        result.provider === "bing"
                            ? "必应 · 在线翻译"
                            : `${result.model || "模型"} · AI 译文`,
                    notice:
                        result.saved === false
                            ? "本次译文未能保存到本地。"
                            : undefined,
                });
        } catch (error) {
            if (!current()) return;
            const message =
                error instanceof Error ? error.message : "请求失败，请重试。";
            if (mode === "translate") state.onStreamEnd?.(message);
            if (mode === "context") view.context.error = message;
            else {
                view.error = message;
                view.refreshLabel = "重试";
            }
        } finally {
            if (current() && mode === "translate") state.onStreamEnd?.();
            view.busy = false;
            view.context.busy = false;
            render();
        }
    }
    return {
        restorePersonal() {
            refreshMode = "dictionary";
            view.refreshHidden = !modelAvailable;
            view.refreshLabel = "重新生成";
        },
        refreshDictionary(action: () => void) {
            view.refreshHidden = false;
            view.refreshLabel = "刷新词典";
            view.onRefresh = () => {
                if (!view.busy && !initializing) action();
            };
        },
        disableContext() {
            view.context.actionLabel = undefined;
            view.context.error =
                "修订原文无法在论文语境中匹配，请重新划选后解释。";
            render();
        },
        get freeFallback() {
            return freeFallback;
        },
        failed(retry: () => void) {
            view.refreshLabel = "重试";
            view.refreshHidden = false;
            view.onRefresh = () => {
                if (!view.busy && !initializing) retry();
            };
        },
        ready() {
            initializing = false;
            view.busy = false;
            render();
        },
        async cachedTranslation() {
            try {
                const result = await call("translate", false, false);
                if (!current()) return true;
                if (result?.status !== "miss" && result?.translation) {
                    popup.render({
                        kind: "translation",
                        text: result.translation,
                        origin: `${result.provider === "bing" ? "必应" : `${result.model || "模型"} · AI 译文`} · 本地缓存`,
                        notice:
                            result.saved === false
                                ? "本次译文未能保存到本地。"
                                : undefined,
                    });
                    return true;
                }
            } catch {
                /* Legacy translation remains available on older servers. */
            }
            return false;
        },
        async dictionary(found: boolean) {
            if (found) {
                view.refreshHidden = true;
                render();
                return true;
            }
            if (automaticDone) return Boolean(personal);
            automaticDone = true;
            let generating = false;
            view.busy = true;
            render();
            try {
                let result = await call("dictionary", false, false);
                if (!current()) return true;
                if (
                    result?.status === "miss" &&
                    !found &&
                    getPref("selectionAutoDictionary") !== false
                ) {
                    if (modelError) throw new Error(modelError);
                    if (modelAvailable) {
                        refreshMode = "dictionary";
                        view.refreshLabel = "重新生成";
                        generating = true;
                        result = await call("dictionary", false);
                    } else {
                        freeFallback = true;
                    }
                }
                if (result?.entry && current()) {
                    showPersonal(result);
                    view.refreshLabel = "重新生成";
                    return true;
                }
            } catch (error) {
                if (current())
                    view.error =
                        error instanceof Error
                            ? error.message
                            : "个人词典暂不可用。";
                if (generating && current()) {
                    view.refreshLabel = "重试";
                    popup.render({
                        kind: "error",
                        text: "释义生成失败，请重试。",
                    });
                    return true;
                }
            } finally {
                view.busy = false;
                render();
            }
            return false;
        },
    };
}

async function showSelection(
    state: ReaderState,
    version: number,
    anchor: HTMLElement,
    text: string,
    page: number | undefined,
    refresh = false,
    plain = false,
    requestedAction?: "lookup" | "translate",
    interrupted?: SelectionResult,
) {
    const current = () =>
        registered &&
        !state.disposed &&
        !state.dismissed &&
        state.version === version;
    if (!current()) return;
    let stage = "popup";
    let learning: ReturnType<typeof createLearning> | undefined;
    let paragraphReference: { text: string; incomplete?: boolean } | undefined;
    let partial = "";
    let flushTimer: ReturnType<typeof setTimeout> | undefined;
    let controls: SelectionControls | undefined;
    const previousResult = refresh ? state.popup?.result : undefined;
    let chosenMeaning: string | undefined;
    try {
        const settings = PDF2zhHelperFactory.getServerConfig(false);
        const route = selectionRoute(
            text,
            settings.sourceLang,
            settings.targetLang,
        );
        const kind =
            requestedAction === "lookup" && route.dictionary
                ? "lookup"
                : requestedAction === "translate"
                  ? "translate"
                  : route.action;
        const queryKey = JSON.stringify([state.key, text]);
        if (state.queryKey !== queryKey) {
            state.queryKey = queryKey;
            state.results = new Map();
            state.context = undefined;
        }
        const modelKey = String(getPref("selectionApiKey") || "");
        if (state.contextModel !== modelKey) state.context = undefined;
        const resultKey = JSON.stringify([
            kind,
            settings.sourceLang,
            settings.targetLang,
            ...(kind === "lookup"
                ? [
                      getPref("selectionDictionary"),
                      getPref("selectionDictionaryFallback"),
                      getPref("selectionAutoDictionary"),
                  ]
                : [getPref("selectionTranslationProvider")]),
            modelKey,
        ]);
        const cached =
            interrupted ||
            (refresh ? undefined : state.results?.get(resultKey));
        const remember = () => {
            const result = state.popup?.result;
            if (
                result &&
                result.kind !== "error" &&
                result.kind !== "missing" &&
                !("incomplete" in result && result.incomplete) &&
                !controls?.streaming
            )
                state.results?.set(resultKey, result);
            const context = state.popup?.learning?.context;
            if (context?.text || context?.contextMeaning) {
                state.context = {
                    text: context.text,
                    contextMeaning: context.contextMeaning,
                    origin: context.origin,
                };
                state.contextModel = modelKey;
            }
        };
        if (state.popup?.alive) state.popup.updateSelection(text, kind, anchor);
        else
            state.popup = createSelectionView(
                state.reader,
                state.doc,
                anchor,
                text,
                kind,
                () => {
                    state.popup = undefined;
                    state.dismissed = true;
                    invalidate(state);
                },
                state.id,
                (part) => recordDiagnostic(`selection_popup_${part}_degraded`),
            );
        if (previousResult) state.popup.render(previousResult);
        else state.popup.loading("正在查询…");
        const ownerView = state.popup;
        const restart = (
            query = text,
            bypassCache = false,
            normal = false,
            mode: "lookup" | "translate" | undefined = kind,
        ) => {
            if (state.disposed || state.dismissed || state.popup !== ownerView)
                return;
            remember();
            invalidate(state);
            void showSelection(
                state,
                state.version,
                anchor,
                query,
                page,
                bypassCache,
                normal,
                mode,
            );
        };
        let models: { value: string; label: string }[] = [];
        try {
            models = loadProfiles().map((p) => ({
                value: p.key,
                label: profileLabel(p),
            }));
        } catch {
            /* A broken model profile must not prevent dictionary lookup. */
        }
        controls = {
            dictionaryAvailable: route.dictionary,
            onMode: (mode) => restart(text, false, false, mode),
            original: state.original || text,
            dictionary: String(getPref("selectionDictionary") || "ecdict"),
            translation:
                getPref("selectionTranslationProvider") === "profile"
                    ? String(getPref("selectionApiKey") || "profile")
                    : "bing",
            models: [
                { value: "bing", label: getString("selection-provider-bing") },
                {
                    value: "profile",
                    label: getString("selection-model-follow"),
                },
                ...models,
            ],
            onDictionary(source) {
                setPref("selectionDictionary", source);
                restart();
            },
            onTranslation(source) {
                setPref(
                    "selectionTranslationProvider",
                    source === "bing" ? "bing" : "profile",
                );
                if (source !== "bing")
                    setPref(
                        "selectionApiKey",
                        source === "profile" ? "" : source,
                    );
                restart();
            },
            onSubmit(query) {
                if (query.trim()) {
                    const next = selectionRoute(
                        query.trim(),
                        settings.sourceLang,
                        settings.targetLang,
                    );
                    restart(query.trim(), false, false, next.action);
                }
            },
            onMeaning(meaning) {
                chosenMeaning = meaning;
            },
            onStop() {
                if (!current()) return;
                remember();
                invalidate(state);
                // Rebind controls to the new request version without generating
                // again. Late chunks retain the invalidated version above.
                void showSelection(
                    state,
                    state.version,
                    anchor,
                    text,
                    page,
                    false,
                    false,
                    kind,
                    {
                        kind: "translation",
                        text: partial || "生成已停止。",
                        origin: "AI 译文 · 未完成",
                        incomplete: true,
                    },
                );
            },
        };
        state.popup.setControls(controls);
        const favoriteAction = async (update = false) => {
            const popup = state.popup!;
            const result = popup.result;
            if (
                !current() ||
                !result ||
                result.kind === "error" ||
                result.kind === "missing" ||
                !isShortSelection(text)
            )
                return;
            const attachment =
                state.reader.itemID && Zotero.Items.get(state.reader.itemID);
            if (!attachment) return;
            const parent = attachment.parentItem;
            const position = state.position;
            const original = state.original || text;
            const now = new Date().toISOString();
            const settings = PDF2zhHelperFactory.getServerConfig(false);
            const favorite: SelectionFavorite = {
                version: 1,
                id: Zotero.getMainWindow().crypto.randomUUID(),
                word: text,
                headword:
                    result.kind === "dictionary" ? result.headword : undefined,
                sourceLang: settings.sourceLang,
                targetLang: settings.targetLang,
                meaning:
                    chosenMeaning ||
                    (result.kind === "dictionary"
                        ? result.senses?.[0]?.chinese
                        : undefined) ||
                    result.text,
                source: "origin" in result ? result.origin : "",
                contextMeaning:
                    popup.learning?.context.contextMeaning?.meaning ||
                    popup.learning?.context.text,
                original,
                query: text,
                title: String((parent || attachment).getField("title")),
                parentKey: parent ? parent.key : undefined,
                attachmentKey: attachment.key,
                library: favoriteLibrary(attachment.libraryID),
                pageIndex: position?.pageIndex ?? Math.max(0, (page || 1) - 1),
                pageLabel: String(page || 1),
                rects: position?.rects,
                createdAt: now,
                updatedAt: now,
                result,
            };
            try {
                favorite.sentence = await getSelectionContext(
                    state.reader,
                    page,
                    original,
                );
                try {
                    const view = state.reader._internalReader
                        ?._primaryView as unknown as {
                        _iframeWindow?: {
                            PDFViewerApplication?: {
                                pdfDocument?: {
                                    getPageLabels(): Promise<string[] | null>;
                                };
                            };
                        };
                    };
                    const labels =
                        await view?._iframeWindow?.PDFViewerApplication?.pdfDocument?.getPageLabels();
                    favorite.pageLabel =
                        labels?.[favorite.pageIndex] || favorite.pageLabel;
                } catch {
                    /* Physical page index remains available if page labels cannot be read. */
                }
                try {
                    favorite.fingerprint = await readerFingerprint(
                        state.reader,
                    );
                } catch {
                    /* A snapshot can still retain the stable attachment key. */
                }
                await saveFavorite(favorite, update);
                if (current()) {
                    controls!.favorite = true;
                    popup.setControls(controls!);
                    popup.showNotice(
                        update ? "收藏快照已更新。" : "已收藏词语和论文出处。",
                    );
                }
            } catch (error) {
                if (current())
                    popup.showNotice(
                        error instanceof Error
                            ? error.message
                            : "收藏保存失败。",
                    );
            }
        };
        const currentFavorite = async () => {
            const attachment =
                state.reader.itemID && Zotero.Items.get(state.reader.itemID);
            if (!attachment) return undefined;
            const identity = favoriteIdentity({
                library: favoriteLibrary(attachment.libraryID),
                attachmentKey: attachment.key,
                pageIndex:
                    state.position?.pageIndex ?? Math.max(0, (page || 1) - 1),
                rects: state.position?.rects,
                word: text,
            });
            return (await listFavorites()).find(
                (entry) => favoriteIdentity(entry) === identity,
            );
        };
        let favoriteChanged = false;
        const toggleFavorite = async (update = false) => {
            if (!current() || controls!.favoriteBusy) return;
            favoriteChanged = true;
            controls!.favoriteBusy = true;
            state.popup!.setControls(controls!);
            try {
                const existing = await currentFavorite();
                if (!current()) return;
                if (existing && !update) {
                    await deleteFavorite(existing.id);
                    if (current()) {
                        controls!.favorite = false;
                        state.popup!.showNotice(
                            getString("selection-favorite-removed"),
                        );
                    }
                } else await favoriteAction(update);
            } catch (error) {
                if (current())
                    state.popup!.showNotice(
                        error instanceof Error
                            ? error.message
                            : getString("selection-favorite-failed"),
                    );
            } finally {
                controls!.favoriteBusy = false;
                if (current()) state.popup!.setControls(controls!);
            }
        };
        if (route.dictionary) {
            controls.onFavorite = () => void toggleFavorite();
            controls.onUpdateFavorite = () => void toggleFavorite(true);
        }
        controls.onShowFavorites = () =>
            void openFavoritesPane(state.reader).catch((error) =>
                state.popup?.showNotice(error.message),
            );
        state.popup.setControls(controls);
        void currentFavorite()
            .then((entry) => {
                if (!current() || favoriteChanged) return;
                controls!.favorite = Boolean(entry);
                state.popup!.setControls(controls!);
            })
            .catch(() => {});
        state.onDelta = (delta) => {
            if (!current()) return;
            partial += delta;
            if (!controls!.streaming) {
                controls!.streaming = true;
                state.popup!.setControls(controls!);
            }
            if (flushTimer === undefined)
                flushTimer = setTimeout(() => {
                    flushTimer = undefined;
                    if (current())
                        state.popup!.stream(partial, "AI 译文 · 正在生成");
                }, 50);
        };
        state.onStreamStart = () => {
            if (!current()) return;
            partial = "";
            controls!.streaming = true;
            state.popup!.setControls(controls!);
        };
        state.onStreamEnd = (error) => {
            if (!current()) return;
            if (flushTimer !== undefined) {
                clearTimeout(flushTimer);
                flushTimer = undefined;
            }
            controls!.streaming = false;
            if (error?.includes("普通模式重试"))
                controls!.onPlainRetry = () => restart(text, true, true);
            state.popup!.setControls(controls!);
            if (error && partial)
                state.popup!.render({
                    kind: "translation",
                    text: partial,
                    origin: "AI 译文 · 未完成",
                    notice: error,
                    incomplete: true,
                });
        };
        if (text.length > 20000)
            throw new Error("选中文字过长，请缩小选区后重试。");
        learning = createLearning(state, current, text, page);
        if (text !== state.original) {
            const context = await getSelectionContext(
                state.reader,
                page,
                state.original || text,
            );
            if (!current()) return;
            if (
                !normalizeSelection(context)
                    .toLowerCase()
                    .includes(normalizeSelection(text).toLowerCase())
            )
                learning.disableContext();
        }
        if (cached) {
            state.popup.render(cached);
            if (interrupted) learning.failed(() => restart(text, true));
            else if (cached.kind === "dictionary" && cached.aiGenerated)
                learning.restorePersonal();
            else if (kind === "lookup")
                learning.refreshDictionary(() => restart(text, true));
            return;
        }
        stage = "dictionary";
        const glossary = lookupGlossary(text, settings.targetLang);
        const dictionaryEligible = kind === "lookup" && route.dictionary;
        if (dictionaryEligible) {
            state.dictionaryRequest = createDictionaryRequest();
            const lookup = await state.dictionaryRequest.lookup(
                text,
                settings.sourceLang,
                settings.targetLang,
                refresh,
            );
            if (!current()) return;
            if (lookup.status === "error") {
                controls.offlineFallback = true;
                state.popup.setControls(controls);
                throw new Error(lookup.message);
            }
            if (lookup.status === "hit") {
                const definition = lookup.entry;
                state.popup!.render({
                    kind: "dictionary",
                    ...definition,
                    origin:
                        definition.origin +
                        (lookup.cached ? " · 在线缓存" : ""),
                    notice: [
                        definition.notice,
                        glossary ? `手工术语表：${glossary}` : "",
                    ]
                        .filter(Boolean)
                        .join("\n"),
                });
                await learning.dictionary(true);
                learning.refreshDictionary(() => restart(text, true));
                return;
            }
        }
        if (dictionaryEligible && (await learning.dictionary(false))) return;
        if (dictionaryEligible) {
            state.popup.render({
                kind: "missing",
                text: getString("selection-dictionary-miss"),
            });
            learning.refreshDictionary(() => restart(text, true));
            return;
        }
        if (!current()) return;
        if (await learning.cachedTranslation()) return;
        if (!current()) return;
        stage = "memory";
        let fingerprint: string;
        let memory: MemoryResult;
        try {
            fingerprint = await readerFingerprint(state.reader);
            if (!current()) return;
            state.request = createSelectionRequest();
            memory = await lookupSelection(
                state.request,
                settings.serverUrl,
                fingerprint,
                text,
                page,
                settings.targetLang,
            );
        } catch {
            if (!current()) return;
            recordDiagnostic("selection_memory_failed");
            if (glossary)
                state.popup!.render({
                    kind: "translation",
                    text: glossary,
                    origin: "本地术语表",
                });
            else
                throw new Error(
                    "无法查询已有译文，请检查本地服务和 PDF 附件。",
                );
            return;
        }
        if (!current()) return;
        if (memory.matched && memory.matchType === "exact") {
            state.popup!.render(memoryResult(memory));
            return;
        }
        if (memory.matched)
            paragraphReference = {
                text: memory.translation!,
                incomplete: memory.formattingIncomplete,
            };
        if (glossary) {
            state.popup!.render({
                kind: "translation",
                text: glossary,
                origin: "本地术语表",
                reference: paragraphReference,
            });
            return;
        }
        stage = "context";
        const context =
            !learning.freeFallback &&
            getPref("selectionTranslationProvider") === "profile"
                ? await getSelectionContext(state.reader, page, text)
                : "";
        if (!current()) return;
        stage = "translate";
        if (!previousResult) state.popup!.loading("正在翻译…");
        const generated = await translateSelection(
            state.request!,
            fingerprint,
            text,
            page,
            context,
            {
                freeFallback: learning.freeFallback,
                refresh,
                plain,
                onDelta: state.onDelta,
                onStreamStart: state.onStreamStart,
            },
        );
        if (!current()) return;
        // Older local services may still return contained memory. Never present
        // it as an exact selection translation or retry another provider.
        if (
            generated.provider === "translation-memory" &&
            generated.matchType !== "exact"
        )
            throw new Error("本地服务仍返回段落译文，请更新服务端后重试。");
        state.popup!.render(
            generated.provider === "translation-memory"
                ? memoryResult(generated)
                : {
                      kind: "translation",
                      text: generated.translation!,
                      reference: paragraphReference,
                      notice:
                          generated.saved === false
                              ? "本次译文未能保存到本地。"
                              : undefined,
                      origin:
                          generated.provider === "glossary"
                              ? "本地术语表"
                              : (generated.provider === "bing"
                                    ? "必应 · 在线翻译"
                                    : `${generated.model || "模型"} · AI 译文`) +
                                (generated.cached ? " · 本地缓存" : ""),
                  },
        );
    } catch (error) {
        if (!current()) return;
        recordDiagnostic(`selection_${stage}_failed`);
        learning?.failed(() => {
            if (current())
                void showSelection(state, version, anchor, text, page, true);
        });
        const message =
            stage === "popup"
                ? "翻译浮窗无法打开，请重新划选重试。"
                : error instanceof Error
                  ? error.message
                  : "翻译失败，请重新划选重试。";
        if (message.includes("普通模式重试") && controls)
            controls.onPlainRetry = () => restartPlain();
        function restartPlain() {
            if (!current()) return;
            invalidate(state);
            void showSelection(
                state,
                state.version,
                anchor,
                text,
                page,
                true,
                true,
                requestedAction,
            );
        }
        try {
            if (!state.popup?.alive) throw new Error();
            state.popup.render(
                partial
                    ? {
                          kind: "translation",
                          text: partial,
                          origin: "AI 译文 · 未完成",
                          incomplete: true,
                          notice: message,
                      }
                    : previousResult
                      ? { ...previousResult, notice: message }
                      : {
                            kind: "error",
                            text: message,
                            reference: paragraphReference,
                        },
            );
        } catch {
            // Never mutate the native annotation tools when richer rendering fails.
            dismiss(state, true);
            state.doc.getElementById(state.id)?.remove();
            const fallback = selectionElement(state.doc, "div");
            fallback.id = state.id;
            fallback.className = "pdf2zh-selection-card";
            fallback.style.cssText =
                "position:fixed;z-index:2147483646;left:16px;top:16px;max-width:280px;padding:12px;background:Canvas;color:CanvasText;";
            fallback.textContent = message;
            const close = selectionElement(state.doc, "button");
            close.type = "button";
            close.textContent = "关闭";
            close.addEventListener("click", () => dismiss(state, true));
            fallback.append(close);
            state.doc.body.append(fallback);
        }
    } finally {
        if (flushTimer !== undefined) {
            clearTimeout(flushTimer);
            flushTimer = undefined;
        }
        if (current()) {
            if (controls) {
                controls.streaming = false;
                state.popup?.setControls(controls);
            }
            learning?.ready();
        }
    }
}

const onSelection: _ZoteroTypes.Reader.EventHandler<
    "renderTextSelectionPopup"
> = (event) => {
    const { reader, doc, params, append } = event;
    if (!registered || reader.type !== "pdf") return;
    try {
        const state = getState(reader, doc);
        const text = params.annotation?.text?.trim() || "";
        // Scrolling can remove the native popup without invalidating the result.
        if (!text) return;
        const key = JSON.stringify([
            normalizeSelection(text),
            params.annotation.position,
        ]);
        if (getPref("selectionTrigger") === "click") {
            if (key !== state.key) {
                invalidate(state);
                state.key = key;
                state.original = text;
                state.position = params.annotation.position;
                state.dismissed = false;
            }
            const button = selectionElement(doc, "button");
            button.type = "button";
            button.textContent = "翻译";
            button.setAttribute("aria-label", "翻译选中文字");
            button.addEventListener("click", (event) => {
                event.stopPropagation();
                invalidate(state);
                state.dismissed = false;
                void showSelection(
                    state,
                    state.version,
                    (button.closest(".selection-popup") as HTMLElement) ||
                        button,
                    text,
                    params.annotation.position?.pageIndex + 1,
                );
            });
            append(button);
            return;
        }
        // Native popups may rerender without a new selection. In particular,
        // do not reopen a manually dismissed card for those events.
        const anchor = selectionElement(doc, "span");
        anchor.style.cssText =
            "display:inline-block;width:1px;height:1px;pointer-events:none;";
        anchor.setAttribute("aria-hidden", "true");
        append(anchor);
        // append() creates .custom-sections > .section in Zotero. Remove our
        // whole wrapper, including its border/padding, while retaining the
        // native popup as a layout anchor and leaving other plugins untouched.
        const nativePopup = anchor.closest?.(
            ".selection-popup",
        ) as HTMLElement | null;
        const section = anchor.parentElement;
        if (
            section?.matches(".section") &&
            section.parentElement?.matches(".custom-sections") &&
            section.childNodes.length === 1
        )
            section.remove();
        else anchor.remove();
        state.anchor = nativePopup || anchor;
        if (key === state.key) return;
        invalidate(state);
        state.key = key;
        state.original = text;
        state.position = params.annotation.position;
        state.dismissed = false;
        const version = state.version;
        const pageIndex = params.annotation.position?.pageIndex;
        const page =
            Number.isInteger(pageIndex) && pageIndex >= 0
                ? pageIndex + 1
                : undefined;
        state.timer = setTimeout(() => {
            state.timer = undefined;
            void showSelection(
                state,
                version,
                state.anchor || anchor,
                text,
                page,
            );
        }, 400);
    } catch {
        recordDiagnostic("selection_capture_failed");
    }
};

export function registerSelectionTranslation(): void {
    if (registered || typeof Zotero.Reader.registerEventListener !== "function")
        return;
    Zotero.Reader.registerEventListener(
        "renderTextSelectionPopup",
        onSelection,
        config.addonID,
    );
    registered = true;
    registerSelectionPane();
    registerFavoritesPane();
    // Clean old cards left by an interrupted development build.
    for (const reader of Zotero.Reader._readers || []) {
        try {
            if (reader._iframeWindow?.document)
                clearOrphans(reader._iframeWindow.document);
        } catch {
            recordDiagnostic("selection_orphan_cleanup_failed");
        }
    }
    if (Zotero.Notifier?.registerObserver)
        tabObserver = Zotero.Notifier.registerObserver(
            {
                notify(event, type, ids) {
                    if (
                        type !== "tab" ||
                        (event !== "select" && event !== "close")
                    )
                        return;
                    for (const state of states.values()) {
                        if (
                            event === "close" &&
                            ids.some((id) => String(id) === state.reader.tabID)
                        )
                            dispose(state);
                        else if (
                            event === "select" &&
                            !ids.some((id) => String(id) === state.reader.tabID)
                        ) {
                            state.onStreamEnd?.("已切换论文，生成已停止。");
                            invalidate(state);
                            dismiss(state);
                        }
                    }
                },
            },
            ["tab"],
            "pdf2zh-selection",
        );
}

export function unregisterSelectionTranslation(): void {
    if (!registered) return;
    registered = false;
    for (const state of states.values()) dispose(state);
    states.clear();
    unregisterSelectionPane();
    unregisterFavoritesPane();
    if (tabObserver) Zotero.Notifier.unregisterObserver(tabObserver);
    tabObserver = undefined;
    Zotero.Reader.unregisterEventListener(
        "renderTextSelectionPopup",
        onSelection,
    );
}

/** Dismiss work using old sources when a selection setting or dictionary changes. */
export function resetSelectionTranslation(): void {
    for (const state of states.values()) dismiss(state, true);
}
