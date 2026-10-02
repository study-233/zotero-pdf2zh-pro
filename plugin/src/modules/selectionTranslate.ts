import { config } from "../../package.json";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { loadGlossaryEntries } from "./glossaryStore";
import { recordDiagnostic } from "./diagnostics";
import { getPref } from "../utils/prefs";
import { createSelectionRequest, SelectionRequest } from "./selectionRequest";
import { watchSelectionDocuments } from "./selectionEvents";
import { getSelectionContext } from "./selectionContext";
import type { DictionaryEntry } from "./selectionDictionaryStore";
import {
    checkedContextMeaning,
    contextCopyText,
    type ContextMeaning,
} from "./selectionFormatting";
import { lookupDictionary } from "./selectionDictionary";
import {
    selectionElement,
    SelectionResult,
    SelectionLearning,
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
    if (options.mode || options.refresh || options.allowGenerate === false) {
        const capability = await request.post(
            `${settings.serverUrl.replace(/\/$/, "")}/selection-capabilities`,
            {},
            10000,
        );
        if (
            !capability.ok ||
            !(capability.data as { selectionLearning?: boolean })
                ?.selectionLearning
        )
            throw new Error("请升级本地服务后使用个人词典、语境含义或重翻。");
    }
    const response = await request.post(
        `${settings.serverUrl.replace(/\/$/, "")}/translate-text`,
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
    );
    if (!response.ok) {
        const errors: Record<string, string> = {
            context_unavailable: "无法读取可靠的论文上下文，请重新划选后重试。",
            invalid_output: "模型返回格式不正确，请重试。",
            invalid_config: "划词翻译配置无效，请检查设置。",
            unsupported_selection_provider:
                "划词暂支持 OpenAI 兼容配置，请在设置中切换。",
            provider_timeout: "翻译请求超时，请重新划选重试。",
            provider_error: "模型请求失败，请检查翻译配置与网络。",
            empty_output: "翻译服务返回空内容，请重新划选重试。",
            selection_busy: "已有翻译请求正在处理，请稍后重新划选。",
            provider_quota:
                "免费翻译额度已用尽或服务限流，请稍后重试。未调用模型。",
        };
        const code = (response.data as { code?: string } | null)?.code;
        const message =
            selectionProvider === "bing" && code === "provider_error"
                ? "必应暂时不可用，请稍后重试。未调用模型。"
                : errors[code || ""];
        throw new Error(message || "划词服务不可用，请检查本地服务。");
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
    learningRequests?: Set<SelectionRequest>;
    popup?: SelectionView;
    anchor?: HTMLElement;
    cleanup?: () => void;
};
const states = new Map<Reader, ReaderState>();

function invalidate(state: ReaderState) {
    state.version++;
    if (state.timer !== undefined) clearTimeout(state.timer);
    state.timer = undefined;
    state.request?.abort();
    state.request = undefined;
    for (const request of state.learningRequests || []) request.abort();
    state.learningRequests?.clear();
}
function dismiss(state: ReaderState, force = false) {
    if (!force && (state.popup?.pinned || state.popup?.docked)) return;
    invalidate(state);
    state.dismissed = true;
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
    let contextValue = "";
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
            actionLabel: modelAvailable ? "结合上下文解释" : undefined,
            error: modelAvailable
                ? undefined
                : modelError || "详细释义和语境解释需要配置模型。",
            onAction: () => void run("context", Boolean(contextValue)),
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
                    normalizeSelection(context) === normalizeSelection(text))
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
                            : result.model || "翻译",
                    notice:
                        result.saved === false
                            ? "本次译文未能保存到本地。"
                            : undefined,
                });
        } catch (error) {
            if (!current()) return;
            const message =
                error instanceof Error ? error.message : "请求失败，请重试。";
            if (mode === "context") view.context.error = message;
            else {
                view.error = message;
                view.refreshLabel = "重试";
            }
        } finally {
            view.busy = false;
            view.context.busy = false;
            render();
        }
    }
    return {
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
                        origin: `${result.provider === "bing" ? "必应" : result.model || "翻译"} · 本地缓存`,
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
    try {
        const kind = isShortSelection(text) ? "lookup" : "translate";
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
        state.popup.loading("正在查询…");
        if (text.length > 20000)
            throw new Error("选中文字过长，请缩小选区后重试。");
        learning = createLearning(state, current, text, page);
        stage = "dictionary";
        const settings = PDF2zhHelperFactory.getServerConfig(false);
        const glossary = lookupGlossary(text, settings.targetLang);
        let dictionaryError: Error | undefined;
        if (kind === "lookup") {
            let definition;
            try {
                definition = glossary
                    ? { text: glossary, origin: "本地术语表" }
                    : await lookupDictionary(
                          text,
                          settings.sourceLang,
                          settings.targetLang,
                      );
            } catch {
                recordDiagnostic("selection_dictionary_failed");
                dictionaryError = new Error(
                    "离线词典读取失败，请重新安装插件后重试。",
                );
            }
            if (!current()) return;
            if (definition) {
                state.popup!.render({ kind: "dictionary", ...definition });
                await learning.dictionary(true);
                return;
            }
        }
        if (
            kind === "lookup" &&
            !dictionaryError &&
            (await learning.dictionary(false))
        )
            return;
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
                    dictionaryError
                        ? "离线词典读取失败，且无法查询已有译文。请检查插件安装和本地服务。"
                        : "无法查询已有译文，请检查本地服务和 PDF 附件。",
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
        // A broken dictionary isn't a dictionary miss; don't silently spend tokens.
        if (dictionaryError) throw dictionaryError;
        stage = "context";
        const context =
            !learning.freeFallback &&
            getPref("selectionTranslationProvider") === "profile"
                ? await getSelectionContext(state.reader, page, text)
                : "";
        if (!current()) return;
        stage = "translate";
        state.popup!.loading("正在翻译…");
        const generated = await translateSelection(
            state.request!,
            fingerprint,
            text,
            page,
            context,
            { freeFallback: learning.freeFallback },
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
                              : generated.cached
                                ? "缓存译文"
                                : generated.provider === "bing"
                                  ? "必应 · 在线翻译"
                                  : "翻译",
                  },
        );
    } catch (error) {
        if (!current()) return;
        recordDiagnostic(`selection_${stage}_failed`);
        learning?.failed(() => {
            if (current())
                void showSelection(state, version, anchor, text, page);
        });
        const message =
            stage === "popup"
                ? "翻译浮窗无法打开，请重新划选重试。"
                : error instanceof Error
                  ? error.message
                  : "翻译失败，请重新划选重试。";
        try {
            if (!state.popup?.alive) throw new Error();
            state.popup.render({
                kind: "error",
                text: message,
                reference: paragraphReference,
            });
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
        if (current()) learning?.ready();
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
                        )
                            dismiss(state);
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
