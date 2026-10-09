/** Reader-owned result card. Source/provider text is always rendered as text. */
import type {
    DictionarySense,
    DictionaryEntry,
} from "./selectionDictionaryStore";
import {
    playSelectionAudio,
    preloadSelectionAudio,
    stopSelectionAudio,
} from "./selectionAudio";
import { getString } from "../utils/locale";
import {
    createSelectionMenu,
    selectionIcon,
    selectionMenuStyle,
} from "./selectionUI";
import {
    dictionarySenses,
    dictionaryCopyText,
    contextCopyText,
    type ContextMeaning,
} from "./selectionFormatting";
export type SelectionResult = (
    | {
          kind: "dictionary";
          text: string;
          phonetic?: string;
          pos?: string;
          origin: string;
          headword?: string;
          senses?: DictionarySense[];
          usage?: string;
          aiGenerated?: boolean;
          pronunciations?: DictionaryEntry["pronunciations"];
          forms?: DictionaryEntry["forms"];
          examples?: DictionaryEntry["examples"];
      }
    | {
          kind: "translation" | "paragraph";
          text: string;
          origin: string;
          incomplete?: boolean;
      }
    | {
          kind: "missing" | "error";
          text: string;
          paragraph?: string;
          incomplete?: boolean;
      }
) & { reference?: { text: string; incomplete?: boolean }; notice?: string };

export type LearningPanel = {
    title: string;
    text?: string;
    contextMeaning?: ContextMeaning;
    origin?: string;
    busy?: boolean;
    error?: string;
    actionLabel?: string;
    onAction: () => void;
};
export type SelectionLearning = {
    refreshLabel: string;
    onRefresh: () => void;
    busy?: boolean;
    error?: string;
    refreshHidden?: boolean;
    context: LearningPanel;
};

export type SelectionControls = {
    dictionaryAvailable?: boolean;
    onMode?: (mode: "lookup" | "translate") => void;
    original: string;
    dictionary: string;
    translation: string;
    models: { value: string; label: string }[];
    onDictionary: (source: string) => void;
    onTranslation: (source: string) => void;
    onSubmit: (text: string) => void;
    onStop?: () => void;
    streaming?: boolean;
    onFavorite?: () => void;
    onUpdateFavorite?: () => void;
    favorite?: boolean;
    favoriteBusy?: boolean;
    onShowFavorites?: () => void;
    offlineFallback?: boolean;
    onPlainRetry?: () => void;
    onMeaning?: (meaning: string) => void;
};

const HTML = "http://www.w3.org/1999/xhtml";
export function selectionElement<K extends keyof HTMLElementTagNameMap>(
    doc: Document,
    tag: K,
): HTMLElementTagNameMap[K] {
    return doc.createElementNS(HTML, tag) as HTMLElementTagNameMap[K];
}

export function clampPopup(
    x: number,
    y: number,
    width: number,
    height: number,
    viewportWidth: number,
    viewportHeight: number,
) {
    return {
        left: Math.max(8, Math.min(x, viewportWidth - width - 8)),
        top: Math.max(8, Math.min(y, viewportHeight - height - 8)),
    };
}

export type SelectionPopupOptions = {
    host?: HTMLElement;
    pinned?: boolean;
    size?: { width: number; height: number };
    position?: { left: number; top: number };
    onResize?: (size: { width: number; height: number }) => void;
    onMove?: (position: { left: number; top: number }) => void;
    onPinChange?: (pinned: boolean) => void;
    onSwitch?: () => void;
    onClear?: () => void;
    onAutoSize?: () => void;
};

export function clampPopupSize(
    width: number,
    height: number,
    vw: number,
    vh: number,
) {
    const maxWidth = Math.max(1, vw - 16);
    const maxHeight = Math.max(1, vh - 16);
    return {
        width: Math.min(maxWidth, Math.max(280, width)),
        height: Math.min(maxHeight, Math.max(200, height)),
    };
}

/** Build the closeable core before mounting; optional enhancements never block it. */
export function createSelectionPopup(
    doc: Document,
    anchor: HTMLElement,
    selected: string,
    action: "lookup" | "translate",
    onClose: () => void,
    id = "pdf2zh-selection-card",
    onDegraded: (stage: string) => void = () => {},
    options: SelectionPopupOptions = {},
) {
    const win = doc.defaultView;
    if (!win) throw new Error("Reader window unavailable");
    doc.getElementById(id)?.remove();
    const card = selectionElement(doc, "section");
    card.id = id;
    card.className = "pdf2zh-selection-card";
    card.setAttribute("role", options.host ? "region" : "dialog");
    card.setAttribute("aria-label", "翻译结果");
    let closed = false;
    let pinned = options.pinned || false;
    const docked = Boolean(options.host);
    let requestedSize = options.size;
    let manuallyPositioned = Boolean(options.position);
    card.dataset.docked = String(docked);
    let copyText = "";
    const cleanups: (() => void)[] = [];
    const audioPreloads: (() => void)[] = [];
    function clearAudio() {
        stopSelectionAudio(card);
        for (const release of audioPreloads.splice(0)) release();
    }
    function close(notify = true) {
        if (closed) return;
        closed = true;
        clearAudio();
        for (const cleanup of cleanups.splice(0)) {
            try {
                cleanup();
            } catch {
                onDegraded("cleanup");
            }
        }
        try {
            card.remove();
        } finally {
            if (notify) onClose();
        }
    }
    function listen(
        target: EventTarget,
        type: string,
        callback: EventListener,
    ) {
        target.addEventListener(type, callback, true);
        cleanups.push(() => target.removeEventListener(type, callback, true));
    }
    function optional(stage: string, setup: () => void) {
        try {
            setup();
        } catch {
            onDegraded(stage);
        }
    }
    const style = selectionElement(doc, "style");
    style.textContent = `
.pdf2zh-selection-card { --st-bg:#fff; --st-text:#202124; --st-muted:#626874; --st-border:#dce0e5; --st-soft:#f3f5f7; --st-accent:#3c64ba; position:fixed; z-index:2147483646; box-sizing:border-box; width:380px; max-width:calc(100vw - 16px); max-height:65vh; display:flex; flex-direction:column; background:var(--st-bg); color:var(--st-text); border:1px solid var(--st-border); border-radius:12px; box-shadow:0 8px 28px #0003; font:14px/1.6 system-ui,-apple-system,sans-serif; text-align:left; user-select:text; overflow:hidden; color-scheme:light; }
.pdf2zh-selection-card[data-dark="true"] { --st-bg:#25272c; --st-text:#e8eaed; --st-muted:#adb3bf; --st-border:#444851; --st-soft:#32353c; --st-accent:#a4bfff; color-scheme:dark; }
.pdf2zh-selection-card * { box-sizing:border-box; }
.pdf2zh-selection-card [hidden] { display:none !important; }
.pdf2zh-selection-card button { appearance:none; display:inline-flex; align-items:center; justify-content:center; width:auto; min-width:0; margin:0; border:1px solid var(--st-border); border-radius:6px; padding:4px 10px; background:var(--st-bg); color:var(--st-text); font:inherit; line-height:1.4; cursor:pointer; }
.pdf2zh-selection-card button:hover { background:var(--st-soft); }
.pdf2zh-selection-card button:focus-visible, .pdf2zh-selection-card summary:focus-visible { outline:2px solid var(--st-accent); outline-offset:2px; }
.pdf2zh-selection-card button:disabled { opacity:.5; cursor:default; }
.pdf2zh-selection-card .st-header { display:flex; align-items:center; gap:2px; flex-wrap:nowrap; padding:5px 8px; background:var(--st-bg); border-bottom:1px solid var(--st-border); flex-shrink:0; cursor:grab; touch-action:none; user-select:none; }
.pdf2zh-selection-card .st-header strong { flex:1; font-size:12px; font-weight:600; color:var(--st-muted); }
.pdf2zh-selection-card .st-header button { font-size:12px; padding:4px 7px; background:transparent; border-color:transparent; }
.pdf2zh-selection-card .st-header button[aria-pressed="true"] { color:var(--st-accent); border-color:var(--st-accent); }
.pdf2zh-selection-card .st-body { padding:14px 16px; overflow:auto; min-height:0; flex:1 1 auto; overflow-wrap:anywhere; overscroll-behavior:contain; }
.pdf2zh-selection-card .st-word { margin:0 0 10px; font-size:19px; line-height:1.4; font-weight:650; }
.pdf2zh-selection-card .st-phonetic { margin:0 0 8px; color:var(--st-muted); font-size:13px; }
.pdf2zh-selection-card .st-result { margin:0; white-space:pre-wrap; }
.pdf2zh-selection-card .st-senses { list-style:none; margin:0; padding:0; }
.pdf2zh-selection-card .st-sense { display:flex; align-items:baseline; gap:8px; margin:4px 0; color:var(--st-text); }
.pdf2zh-selection-card .st-pos { flex:0 0 auto; max-width:40%; color:var(--st-muted); font-size:12px; font-weight:400; }
.pdf2zh-selection-card .st-definition { min-width:0; white-space:pre-wrap; }
.pdf2zh-selection-card .st-context-meaning { margin:0; font-weight:600; }
.pdf2zh-selection-card .st-context-explanation { margin:4px 0 8px; font-size:13px; color:var(--st-muted); white-space:pre-wrap; }
.pdf2zh-selection-card .st-label { margin:0 0 8px; font-size:12px; color:var(--st-muted); }
.pdf2zh-selection-card details { margin-top:12px; font-size:13px; color:var(--st-muted); }
.pdf2zh-selection-card summary { cursor:pointer; user-select:none; }
.pdf2zh-selection-card details p { margin:8px 0 0; white-space:pre-wrap; }
.pdf2zh-selection-card .st-footer { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; padding:9px 12px; border-top:1px solid var(--st-border); flex-shrink:0; }
.pdf2zh-selection-card .st-status { color:var(--st-muted); font-size:12px; flex:1; overflow-wrap:anywhere; }
.pdf2zh-selection-card[data-sized="true"] .st-body { max-height:none; }
.pdf2zh-selection-card[data-sized="true"] { max-height:calc(100vh - 16px); }
.pdf2zh-selection-card select, .pdf2zh-selection-card textarea { font:inherit; color:var(--st-text); background:var(--st-bg); border:1px solid var(--st-border); border-radius:6px; max-width:100%; }
.pdf2zh-selection-card .st-source { min-width:0; flex:1; font-size:12px; padding:3px; }
.pdf2zh-selection-card .st-editor textarea { width:100%; min-height:72px; resize:vertical; padding:6px; }
.pdf2zh-selection-card .st-actions { display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }
.pdf2zh-selection-card .st-example { margin:8px 0; }
.pdf2zh-selection-card .st-example p { margin:2px 0; }
.pdf2zh-selection-card .st-example mark { color:var(--st-accent); font-weight:650; background:transparent; }
.pdf2zh-selection-card .st-pronunciation { display:inline-flex; align-items:center; gap:5px; margin:0 12px 8px 0; color:var(--st-muted); font-size:12px; }
.pdf2zh-selection-card .st-pronunciation button { padding:2px 5px; }
.pdf2zh-selection-card .st-pronunciation button[aria-pressed="true"] { color:var(--st-accent); }
.pdf2zh-selection-card .st-pronunciation button[aria-busy="true"] { opacity:.55; }
.pdf2zh-selection-card .st-resize { position:absolute; right:0; bottom:0; width:14px; height:14px; cursor:nwse-resize; touch-action:none; background:linear-gradient(135deg,transparent 55%,var(--st-muted) 56%,var(--st-muted) 62%,transparent 63%,transparent 75%,var(--st-muted) 76%,var(--st-muted) 82%,transparent 83%); }
.pdf2zh-selection-card[data-docked="true"] { position:relative; z-index:auto; width:100%; max-width:100%; max-height:none; border:0; border-radius:0; box-shadow:none; }
.pdf2zh-selection-card[data-docked="true"] .st-header { cursor:default; }
.pdf2zh-selection-card[data-docked="true"] .st-body { max-height:60vh; padding:12px 8px; }
.pdf2zh-selection-card .st-context-heading { display:block; font-size:12px; color:var(--st-muted); margin-bottom:6px; }
.pdf2zh-selection-card .st-context-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
${selectionMenuStyle}
.pdf2zh-selection-card { border-radius:8px; font-size:14px; line-height:1.55; }
.pdf2zh-selection-card .st-tabs { display:flex; flex:1; min-width:0; gap:2px; }
.pdf2zh-selection-card .st-tabs button { padding:4px 8px; min-height:28px; font-size:13px; white-space:nowrap; }
.pdf2zh-selection-card .st-tabs button[aria-selected="true"] { color:var(--st-accent); background:var(--st-soft); font-weight:600; }
.pdf2zh-selection-card .st-source-row { display:flex; align-items:center; gap:8px; padding:6px 12px; flex-shrink:0; min-width:0; }
.pdf2zh-selection-card .st-source-label { flex:0 0 auto; color:var(--st-muted); font-size:12px; }
.pdf2zh-selection-card .st-source { width:0; padding:3px 6px; min-height:26px; border-color:transparent; background:var(--st-soft); text-overflow:ellipsis; }
.pdf2zh-selection-card .st-body, .pdf2zh-selection-card[data-docked="true"] .st-body { padding:10px 12px; }
.pdf2zh-selection-card .st-word-row { display:flex; align-items:center; gap:8px; margin-bottom:6px; }
.pdf2zh-selection-card .st-word { flex:1; min-width:0; margin:0; font-size:20px; font-weight:600; }
.pdf2zh-selection-card .st-word-row button[aria-pressed="true"] { color:var(--st-accent); background:var(--st-soft) !important; }
.pdf2zh-selection-card .st-footer { padding:6px 10px; gap:4px; }
.pdf2zh-selection-card .st-footer button { border-color:transparent; padding:4px 6px; font-size:12px; }
.pdf2zh-selection-card .st-status { min-width:50px; font-size:11px; }
.pdf2zh-selection-card .st-pronunciation { margin-bottom:6px; }
.pdf2zh-selection-card .st-context-actions { margin-top:6px; }
.pdf2zh-selection-card .st-context-actions button { font-size:12px; padding:3px 6px; border-color:transparent; background:var(--st-soft); }
`;

    const header = selectionElement(doc, "header");
    header.className = "st-header";
    function button(label: string) {
        const node = selectionElement(doc, "button");
        node.type = "button";
        node.textContent = label;
        node.setAttribute("aria-label", label);
        return node;
    }
    const tabs = selectionElement(doc, "div");
    tabs.className = "st-tabs";
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", getString("selection-result-tabs"));
    const dictionaryTab = button(getString("selection-tab-dictionary"));
    const translationTab = button(getString("selection-tab-translation"));
    for (const [tab, mode] of [
        [dictionaryTab, "lookup"],
        [translationTab, "translate"],
    ] as const) {
        tab.setAttribute("role", "tab");
        tab.id = `${id}-${mode}`;
        tab.setAttribute("aria-controls", `${id}-result`);
        tab.addEventListener("click", () => {
            if (action !== mode) controls?.onMode?.(mode);
        });
        tab.addEventListener("keydown", (event) => {
            if (
                ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
            ) {
                event.preventDefault();
                const next = dictionaryTab.hidden
                    ? translationTab
                    : event.key === "Home"
                      ? dictionaryTab
                      : event.key === "End"
                        ? translationTab
                        : tab === dictionaryTab
                          ? translationTab
                          : dictionaryTab;
                next.focus();
                next.click();
            }
        });
        tabs.append(tab);
    }
    const pin = button(getString("selection-pin"));
    selectionIcon(
        pin,
        "pin",
        getString(pinned ? "selection-unpin" : "selection-pin"),
    );
    pin.setAttribute("aria-pressed", String(pinned));
    pin.hidden = docked;
    const closeButton = button(getString("selection-close"));
    selectionIcon(closeButton, "x", getString("selection-close"));
    closeButton.hidden = docked;
    const switchLabel = getString(
        docked ? "selection-float" : "selection-dock",
    );
    const switchButton = button(switchLabel);
    selectionIcon(
        switchButton,
        docked ? "external-link" : "panel-right",
        switchLabel,
    );
    switchButton.hidden = !options.onSwitch;
    switchButton.addEventListener("click", () => options.onSwitch?.());
    const more = createSelectionMenu(card, getString("selection-more"));
    cleanups.push(more.destroy);
    header.append(tabs, pin, switchButton, more.trigger, closeButton);
    const sourceRow = selectionElement(doc, "label");
    sourceRow.className = "st-source-row";
    const sourceLabel = selectionElement(doc, "span");
    sourceLabel.className = "st-source-label";
    const sourceChoice = selectionElement(doc, "select");
    sourceChoice.className = "st-source";
    sourceChoice.hidden = true;
    sourceRow.append(sourceLabel, sourceChoice);
    const clear = button(getString("selection-clear"));
    clear.addEventListener("click", () =>
        options.onClear ? options.onClear() : close(),
    );
    more.menu.append(clear);
    const autoSize = button(getString("selection-auto-size"));
    autoSize.hidden = docked;
    autoSize.addEventListener("click", () => {
        requestedSize = undefined;
        card.dataset.sized = "false";
        card.style.removeProperty("width");
        card.style.removeProperty("height");
        options.onAutoSize?.();
        more.close();
        fit();
    });
    more.menu.append(autoSize);
    const plainRetry = button(getString("selection-plain-retry"));
    plainRetry.hidden = true;
    plainRetry.addEventListener("click", () => controls?.onPlainRetry?.());
    more.menu.append(plainRetry);
    const body = selectionElement(doc, "div");
    body.className = "st-body";
    body.id = `${id}-result`;
    body.setAttribute("role", "tabpanel");
    body.setAttribute("aria-live", "polite");
    const footer = selectionElement(doc, "footer");
    footer.className = "st-footer";
    const status = selectionElement(doc, "span");
    status.className = "st-status";
    const content = selectionElement(doc, "div");
    const learningContent = selectionElement(doc, "div");
    const editor = selectionElement(doc, "details");
    editor.className = "st-editor";
    const editorSummary = selectionElement(doc, "summary");
    editorSummary.textContent = getString("selection-original-editor");
    const input = selectionElement(doc, "textarea");
    input.value = selected;
    input.setAttribute("aria-label", "用于查询或翻译的原文");
    const editActions = selectionElement(doc, "div");
    editActions.className = "st-actions";
    const submit = button(getString("selection-submit")),
        cancelEdit = button(getString("selection-cancel")),
        restore = button(getString("selection-restore"));
    editActions.append(submit, cancelEdit, restore);
    editor.append(editorSummary, input, editActions);
    editor.hidden = true;
    const favorite = button(getString("selection-favorite")),
        updateFavorite = button(getString("selection-update-favorite")),
        showFavorites = button(getString("selection-show-favorites"));
    selectionIcon(favorite, "star", getString("selection-favorite"));
    more.menu.append(showFavorites, updateFavorite);
    body.append(content, editor, learningContent);
    let controls: SelectionControls | undefined;
    function choose(
        node: HTMLSelectElement,
        choices: { value: string; label: string }[],
        value: string,
    ) {
        node.replaceChildren();
        for (const choice of choices) {
            const option = selectionElement(doc, "option");
            option.value = choice.value;
            option.textContent = choice.label;
            node.append(option);
        }
        node.value = value;
    }
    const sources = [
        { value: "ecdict", label: getString("selection-dict-ecdict") },
        { value: "collins", label: getString("selection-dict-collins") },
        { value: "youdao", label: getString("selection-dict-youdao") },
        { value: "bing", label: getString("selection-dict-bing") },
    ];
    sourceChoice.addEventListener("change", () =>
        action === "lookup"
            ? controls?.onDictionary(sourceChoice.value)
            : controls?.onTranslation(sourceChoice.value),
    );
    const submitText = () => {
        if (input.value.trim()) {
            editor.open = false;
            controls?.onSubmit(input.value.trim());
        }
    };
    submit.addEventListener("click", submitText);
    input.addEventListener("keydown", (event) => {
        if (
            (event.ctrlKey || event.metaKey) &&
            event.key === "Enter" &&
            !event.isComposing
        ) {
            event.preventDefault();
            submitText();
        }
    });
    cancelEdit.addEventListener("click", () => {
        input.value = selected;
        editor.open = false;
    });
    restore.addEventListener("click", () => {
        input.value = controls?.original || selected;
    });
    favorite.addEventListener("click", () => controls?.onFavorite?.());
    updateFavorite.addEventListener("click", () =>
        controls?.onUpdateFavorite?.(),
    );
    showFavorites.addEventListener("click", () =>
        controls?.onShowFavorites?.(),
    );
    const refresh = button("重翻译文");
    refresh.hidden = true;
    let learning: SelectionLearning | undefined;
    refresh.addEventListener("click", () => learning?.onRefresh());
    const copy = button(getString("selection-copy"));
    copy.disabled = true;
    footer.append(status, refresh, copy);
    const stop = button(getString("selection-stop"));
    stop.hidden = true;
    stop.addEventListener("click", () => controls?.onStop?.());
    footer.append(stop);
    const offline = button(getString("selection-offline"));
    offline.hidden = true;
    offline.addEventListener("click", () => controls?.onDictionary("ecdict"));
    footer.append(offline);
    function setControls(value: SelectionControls) {
        controls = value;
        dictionaryTab.hidden = !(
            value.dictionaryAvailable ?? action === "lookup"
        );
        for (const [tab, mode] of [
            [dictionaryTab, "lookup"],
            [translationTab, "translate"],
        ] as const) {
            tab.setAttribute("aria-selected", String(action === mode));
            tab.tabIndex = action === mode ? 0 : -1;
        }
        body.setAttribute("aria-labelledby", `${id}-${action}`);
        sourceChoice.hidden = false;
        editor.hidden = false;
        choose(
            sourceChoice,
            action === "lookup" ? sources : value.models,
            action === "lookup" ? value.dictionary : value.translation,
        );
        sourceLabel.textContent = getString(
            action === "lookup"
                ? "selection-dictionary-source"
                : "selection-translation-source",
        );
        sourceChoice.setAttribute("aria-label", sourceLabel.textContent);
        favorite.hidden = !value.onFavorite;
        favorite.title = getString(
            value.favorite ? "selection-favorited" : "selection-favorite",
        );
        favorite.setAttribute("aria-label", favorite.title);
        favorite.setAttribute("aria-pressed", String(Boolean(value.favorite)));
        favorite.disabled = Boolean(value.favoriteBusy) || !copyText;
        updateFavorite.hidden = !value.favorite;
        updateFavorite.disabled = Boolean(value.favoriteBusy);
        showFavorites.hidden = !value.onShowFavorites;
        stop.hidden = !value.streaming;
        offline.hidden = !value.offlineFallback;
        plainRetry.hidden = !value.onPlainRetry;
    }
    card.append(style, header, sourceRow, body, footer);
    function position(x: number, y: number) {
        if (docked) return;
        const rect = card.getBoundingClientRect();
        const point = clampPopup(
            x,
            y,
            rect.width,
            rect.height,
            win!.innerWidth || 1024,
            win!.innerHeight || 768,
        );
        card.style.left = `${point.left}px`;
        card.style.top = `${point.top}px`;
    }
    function fit() {
        if (closed || !card.isConnected || docked) return;
        if (requestedSize) applySize(requestedSize.width, requestedSize.height);
        optional("position", () => {
            if (!pinned && !manuallyPositioned) {
                nearSelection();
                return;
            }
            const rect = card.getBoundingClientRect();
            position(rect.left, rect.top);
        });
    }
    function savePosition() {
        if (docked || closed || !card.isConnected) return;
        const { left, top } = card.getBoundingClientRect();
        options.onMove?.({ left, top });
    }
    function applySize(width: number, height: number) {
        const size = clampPopupSize(
            width,
            height,
            win!.innerWidth,
            win!.innerHeight,
        );
        card.dataset.sized = "true";
        card.style.width = `${size.width}px`;
        card.style.height = `${size.height}px`;
    }
    function nearSelection() {
        if (docked) return;
        optional("position", () => {
            const toolbar = anchor.getBoundingClientRect();
            const normalizeText = (text: string) =>
                text.normalize("NFKC").replace(/\s+/g, " ").trim();
            const rect = {
                left: toolbar.left,
                right: toolbar.right,
                top: toolbar.top,
                bottom: toolbar.bottom,
            };
            // PDF text can live in nested iframes; convert every range into this document's coordinates.
            function includeSelection(
                document: Document,
                x = 0,
                y = 0,
                depth = 0,
            ) {
                if (depth > 4) return;
                const selection = document.getSelection();
                if (
                    selection?.rangeCount &&
                    !selection.isCollapsed &&
                    normalizeText(selection.toString()) ===
                        normalizeText(selected)
                ) {
                    const box = selection.getRangeAt(0).getBoundingClientRect();
                    rect.left = Math.min(rect.left, box.left + x);
                    rect.right = Math.max(rect.right, box.right + x);
                    rect.top = Math.min(rect.top, box.top + y);
                    rect.bottom = Math.max(rect.bottom, box.bottom + y);
                }
                for (const frame of Array.from(
                    document.querySelectorAll("iframe"),
                ) as unknown as HTMLIFrameElement[]) {
                    try {
                        if (frame.contentDocument) {
                            const box = frame.getBoundingClientRect();
                            includeSelection(
                                frame.contentDocument,
                                x + box.left,
                                y + box.top,
                                depth + 1,
                            );
                        }
                    } catch {
                        /* Cross-origin frames do not expose PDF selection. */
                    }
                }
            }
            includeSelection(doc);
            const size = card.getBoundingClientRect();
            const vw = win!.innerWidth,
                vh = win!.innerHeight;
            const candidates = [
                { left: rect.left, top: rect.bottom + 8 },
                { left: rect.left, top: rect.top - size.height - 8 },
                { left: rect.right + 8, top: rect.top },
                { left: rect.left - size.width - 8, top: rect.top },
            ];
            const point =
                candidates.find(
                    (p) =>
                        p.left >= 8 &&
                        p.top >= 8 &&
                        p.left + size.width <= vw - 8 &&
                        p.top + size.height <= vh - 8,
                ) || candidates[0];
            position(point.left, point.top);
        });
    }
    function paragraph(text: string, className: string) {
        const node = selectionElement(doc, "p");
        node.className = className;
        node.textContent = text;
        content.append(node);
    }
    function details(label: string, text: string) {
        const node = selectionElement(doc, "details");
        const summary = selectionElement(doc, "summary");
        summary.textContent = label;
        const detailText = selectionElement(doc, "p");
        detailText.textContent = text;
        node.append(summary, detailText);
        node.addEventListener("toggle", fit);
        content.append(node);
        return node;
    }
    function senseList(senses: DictionarySense[]) {
        const list = selectionElement(doc, "ul");
        list.className = "st-senses";
        for (const sense of senses) {
            const row = selectionElement(doc, "li");
            row.className = "st-sense";
            if (sense.pos) {
                const pos = selectionElement(doc, "span");
                pos.className = "st-pos";
                pos.textContent = sense.pos;
                row.append(pos);
            }
            const definition = selectionElement(doc, "span");
            definition.className = "st-definition";
            definition.textContent = sense.chinese;
            if (controls?.onMeaning) {
                definition.tabIndex = 0;
                definition.setAttribute("role", "button");
                definition.title = "选择此释义用于收藏";
                const selectMeaning = () => {
                    controls?.onMeaning?.(sense.chinese);
                    status.textContent = "已选定收藏释义";
                };
                definition.addEventListener("click", selectMeaning);
                definition.addEventListener("keydown", (event) => {
                    if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        selectMeaning();
                    }
                });
            }
            row.append(definition);
            list.append(row);
        }
        return list;
    }
    function reset() {
        clearAudio();
        content.replaceChildren();
        status.textContent = "";
        copyText = "";
        copy.disabled = true;
        favorite.disabled = true;
        if (action === "lookup" || controls?.dictionaryAvailable) {
            const row = selectionElement(doc, "div");
            row.className = "st-word-row";
            const word = selectionElement(doc, "strong");
            word.className = "st-word";
            word.textContent = selected;
            row.append(word, favorite);
            content.append(row);
        }
    }
    function renderLearning(value: SelectionLearning) {
        learning = value;
        refresh.hidden = Boolean(value.refreshHidden);
        refresh.disabled = Boolean(value.busy);
        refresh.textContent = value.busy ? "处理中…" : value.refreshLabel;
        refresh.setAttribute("aria-label", refresh.textContent);
        learningContent.replaceChildren();
        if (value.error) {
            const error = selectionElement(doc, "p");
            error.className = "st-label";
            error.textContent = value.error;
            learningContent.append(error);
        }
        for (const panel of [value.context]) {
            if (!panel) continue;
            const section = selectionElement(doc, "section");
            section.style.cssText = "margin-top:8px";
            const heading = selectionElement(doc, "strong");
            heading.textContent = panel.title;
            heading.className = "st-context-heading";
            heading.hidden = !panel.text && !panel.contextMeaning;
            section.append(heading);
            const addText = (text: string, muted = false) => {
                const p = selectionElement(doc, "p");
                p.className = muted ? "st-label" : "st-result";
                p.textContent = text;
                section.append(p);
            };
            if (panel.contextMeaning) {
                const meaning = selectionElement(doc, "p");
                meaning.className = "st-sense st-context-meaning";
                if (panel.contextMeaning.pos) {
                    const pos = selectionElement(doc, "span");
                    pos.className = "st-pos";
                    pos.textContent = panel.contextMeaning.pos;
                    meaning.append(pos);
                }
                const definition = selectionElement(doc, "span");
                definition.className = "st-definition";
                definition.textContent = panel.contextMeaning.meaning;
                meaning.append(definition);
                const explanation = selectionElement(doc, "p");
                explanation.className = "st-context-explanation";
                explanation.textContent = panel.contextMeaning.explanation;
                section.append(meaning, explanation);
            } else if (panel.text) addText(panel.text);
            if (panel.origin) addText(panel.origin, true);
            if (panel.error) {
                const explanation = selectionElement(doc, "details");
                const summary = selectionElement(doc, "summary");
                summary.textContent = getString("selection-context-help");
                const message = selectionElement(doc, "p");
                message.textContent = panel.error;
                explanation.append(summary, message);
                section.append(explanation);
            }
            const actions = selectionElement(doc, "div");
            actions.className = "st-context-actions";
            if (panel.actionLabel) {
                const actionButton = button(
                    panel.busy ? "正在生成…" : panel.actionLabel,
                );
                actionButton.disabled = Boolean(panel.busy || value.busy);
                actionButton.addEventListener("click", panel.onAction);
                actions.append(actionButton);
            }
            const textToCopy = panel.contextMeaning
                ? contextCopyText(panel.contextMeaning)
                : panel.text;
            if (textToCopy) {
                const copyPart = button(
                    getString("selection-copy") + panel.title,
                );
                copyPart.addEventListener("click", () => {
                    try {
                        Zotero.Utilities.Internal.copyTextToClipboard(
                            textToCopy,
                        );
                    } catch {
                        copyPart.textContent = "复制失败";
                    }
                });
                actions.append(copyPart);
            }
            if (actions.childNodes.length) section.append(actions);
            learningContent.append(section);
        }
        fit();
    }
    try {
        // Close is bound before anything can expose the card to the user.
        closeButton.addEventListener("click", (event) => {
            event.stopPropagation();
            if (docked && options.onClear) options.onClear();
            else close();
        });
        pin.addEventListener("click", (event) => {
            event.stopPropagation();
            pinned = !pinned;
            pin.setAttribute("aria-pressed", String(pinned));
            pin.title = getString(pinned ? "selection-unpin" : "selection-pin");
            pin.setAttribute("aria-label", pin.title);
            savePosition();
            options.onPinChange?.(pinned);
        });
        copy.addEventListener("click", () => {
            try {
                Zotero.Utilities.Internal.copyTextToClipboard(copyText);
                status.textContent = "已复制";
            } catch {
                status.textContent = "复制失败，请选择文本复制";
            }
        });
        reset();
        paragraph("正在查询…", "st-label");
        // Fallback position remains usable even when layout APIs fail.
        if (!docked) {
            card.style.left = "16px";
            card.style.top = "16px";
            if (requestedSize)
                applySize(requestedSize.width, requestedSize.height);
        }
        (options.host || doc.documentElement).append(card);
        if (options.position)
            optional("position", () =>
                position(options.position!.left, options.position!.top),
            );
        else nearSelection();
        optional("theme", () => {
            const darkQuery = win.matchMedia?.("(prefers-color-scheme: dark)");
            const syncTheme = () =>
                optional("theme", () => {
                    const theme =
                        doc.documentElement.getAttribute("data-color-scheme") ||
                        doc.documentElement.getAttribute("data-theme");
                    const scheme = win.getComputedStyle?.(
                        doc.documentElement,
                    )?.colorScheme;
                    card.dataset.dark = String(
                        theme === "dark" ||
                            (theme !== "light" &&
                                (scheme === "dark" ||
                                    (scheme !== "light" &&
                                        darkQuery?.matches))),
                    );
                });
            syncTheme();
            if (darkQuery?.addEventListener)
                listen(darkQuery, "change", syncTheme);
            if (typeof win.MutationObserver === "function") {
                const observer = new win.MutationObserver(syncTheme);
                cleanups.push(() => observer.disconnect());
                observer.observe(doc.documentElement, {
                    attributes: true,
                    attributeFilter: [
                        "data-color-scheme",
                        "data-theme",
                        "class",
                        "style",
                    ],
                });
            }
        });
        optional("resize", () => listen(win, "resize", fit));
        optional("resize-handle", () => {
            if (docked) return;
            const handle = selectionElement(doc, "div");
            handle.className = "st-resize";
            handle.setAttribute("aria-label", "调整窗口大小");
            handle.setAttribute("role", "separator");
            handle.tabIndex = 0;
            card.append(handle);
            let resize:
                | {
                      pointer: number;
                      x: number;
                      y: number;
                      width: number;
                      height: number;
                  }
                | undefined;
            const save = () => {
                if (!resize) return;
                const pointer = resize.pointer;
                resize = undefined;
                if (handle.hasPointerCapture?.(pointer))
                    handle.releasePointerCapture(pointer);
                const rect = card.getBoundingClientRect();
                requestedSize = { width: rect.width, height: rect.height };
                options.onResize?.(requestedSize);
                savePosition();
            };
            listen(handle, "pointerdown", (raw) => {
                const event = raw as PointerEvent;
                if (event.button !== 0) return;
                const rect = card.getBoundingClientRect();
                resize = {
                    pointer: event.pointerId,
                    x: event.clientX,
                    y: event.clientY,
                    width: rect.width,
                    height: rect.height,
                };
                try {
                    handle.setPointerCapture(event.pointerId);
                } catch {
                    /* Document fallback. */
                }
                event.preventDefault();
                event.stopPropagation();
            });
            listen(doc, "pointermove", (raw) => {
                const event = raw as PointerEvent;
                if (!resize || event.pointerId !== resize.pointer) return;
                requestedSize = clampPopupSize(
                    resize.width + event.clientX - resize.x,
                    resize.height + event.clientY - resize.y,
                    win.innerWidth,
                    win.innerHeight,
                );
                fit();
                event.preventDefault();
            });
            listen(doc, "pointerup", save);
            listen(doc, "pointercancel", save);
            listen(handle, "lostpointercapture", save);
            listen(handle, "keydown", (raw) => {
                const event = raw as KeyboardEvent;
                if (
                    ![
                        "ArrowLeft",
                        "ArrowRight",
                        "ArrowUp",
                        "ArrowDown",
                    ].includes(event.key)
                )
                    return;
                const rect = card.getBoundingClientRect();
                requestedSize = clampPopupSize(
                    rect.width +
                        (event.key === "ArrowRight"
                            ? 20
                            : event.key === "ArrowLeft"
                              ? -20
                              : 0),
                    rect.height +
                        (event.key === "ArrowDown"
                            ? 20
                            : event.key === "ArrowUp"
                              ? -20
                              : 0),
                    win.innerWidth,
                    win.innerHeight,
                );
                fit();
                options.onResize?.(requestedSize);
                savePosition();
                event.preventDefault();
            });
            cleanups.push(() => {
                resize = undefined;
            });
        });
        optional("drag", () => {
            let drag:
                | {
                      pointer: number;
                      x: number;
                      y: number;
                      left: number;
                      top: number;
                  }
                | undefined;
            const stop = () => {
                if (!drag) return;
                const pointer = drag.pointer;
                drag = undefined;
                try {
                    if (header.hasPointerCapture?.(pointer))
                        header.releasePointerCapture(pointer);
                } catch {
                    /* Capture may already be lost. */
                }
                savePosition();
            };
            cleanups.push(stop);
            listen(header, "pointerdown", (raw) => {
                const event = raw as PointerEvent;
                if (
                    (event.target as Element)?.closest?.(
                        "button,a,input,textarea,select,summary",
                    ) ||
                    docked ||
                    event.button !== 0
                )
                    return;
                const rect = card.getBoundingClientRect();
                drag = {
                    pointer: event.pointerId,
                    x: event.clientX,
                    y: event.clientY,
                    left: rect.left,
                    top: rect.top,
                };
                manuallyPositioned = true;
                try {
                    header.setPointerCapture(event.pointerId);
                } catch {
                    /* Document listeners handle embedded Reader capture failures. */
                }
                event.stopPropagation();
                event.preventDefault();
            });
            listen(doc, "pointermove", (raw) => {
                const event = raw as PointerEvent;
                if (!drag || drag.pointer !== event.pointerId) return;
                optional("drag", () =>
                    position(
                        drag!.left + event.clientX - drag!.x,
                        drag!.top + event.clientY - drag!.y,
                    ),
                );
                event.preventDefault();
            });
            listen(doc, "pointerup", stop);
            listen(doc, "pointercancel", stop);
            listen(header, "lostpointercapture", stop);
        });
    } catch (error) {
        close(false);
        throw error;
    }
    return {
        card,
        setControls,
        stream(text: string, origin = "正在生成…") {
            let node = content.querySelector(
                ".st-stream",
            ) as HTMLElement | null;
            if (!node) {
                reset();
                paragraph("", "st-result st-stream");
                node = content.querySelector(".st-stream") as HTMLElement;
            }
            const atBottom =
                body.scrollTop + body.clientHeight >= body.scrollHeight - 24;
            node.textContent = text;
            copyText = text;
            copy.disabled = !text;
            status.textContent = origin;
            if (atBottom) body.scrollTop = body.scrollHeight;
            fit();
        },
        setLearning: renderLearning,
        showNotice: (message: string) => {
            status.textContent = message;
        },
        close: () => close(),
        destroy: () => close(false),
        get alive() {
            return !closed && card.isConnected;
        },
        get pinned() {
            return pinned;
        },
        get editorDraft() {
            return input.value;
        },
        set editorDraft(value: string) {
            input.value = value;
        },
        updateSelection(
            text: string,
            kind: "lookup" | "translate",
            nextAnchor: HTMLElement,
        ) {
            if (text !== selected || !editor.open) input.value = text;
            selected = text;
            more.close();
            clearAudio();
            action = kind;
            learning = undefined;
            learningContent.replaceChildren();
            refresh.hidden = true;
            anchor = nextAnchor;
            if (!pinned) manuallyPositioned = false;
            if (controls) setControls(controls);
            if (!pinned) nearSelection();
        },
        loading(message: string) {
            if (closed) return;
            reset();
            paragraph(message, "st-label");
            fit();
        },
        render(result: SelectionResult) {
            if (closed) return;
            reset();
            if (result.kind === "dictionary") {
                const word = body.querySelector(".st-word");
                if (word && result.headword) word.textContent = result.headword;
                const base =
                    result.forms?.find((form) => form.label === "原形")?.word ||
                    (result.headword?.toLowerCase() !== selected.toLowerCase()
                        ? result.headword
                        : undefined);
                if (base) paragraph(`${selected} → ${base}`, "st-label");
                const pronunciations = result.pronunciations?.length
                    ? result.pronunciations
                    : [
                          { accent: "英", phonetic: result.phonetic },
                          { accent: "美" },
                      ];
                for (const pronunciation of pronunciations) {
                    const row = selectionElement(doc, "span");
                    row.className = "st-pronunciation";
                    const label = selectionElement(doc, "span");
                    label.textContent = [
                        pronunciation.accent,
                        pronunciation.phonetic
                            ? `/${pronunciation.phonetic}/`
                            : "",
                    ]
                        .filter(Boolean)
                        .join(" ");
                    const play = button(`${pronunciation.accent}音发音`);
                    selectionIcon(
                        play,
                        "volume-2",
                        `${pronunciation.accent}音发音`,
                    );
                    play.addEventListener("click", () =>
                        playSelectionAudio(
                            doc,
                            result.headword || selected,
                            pronunciation.accent,
                            "audioUrl" in pronunciation
                                ? pronunciation.audioUrl
                                : undefined,
                            play,
                            (message) => {
                                status.textContent = message || result.origin;
                            },
                        ),
                    );
                    row.append(label, play);
                    content.append(row);
                    audioPreloads.push(
                        preloadSelectionAudio(
                            result.headword || selected,
                            pronunciation.accent,
                            "audioUrl" in pronunciation
                                ? pronunciation.audioUrl
                                : undefined,
                        ),
                    );
                }
                const senses = dictionarySenses(result);
                content.append(senseList(senses.slice(0, 3)));
                if (senses.length > 3) {
                    const more = details("更多释义", "");
                    more.querySelector("p")?.remove();
                    more.append(senseList(senses.slice(3)));
                }
                const english = senses
                    .map((sense, i) =>
                        sense.english ? `${i + 1}. ${sense.english}` : "",
                    )
                    .filter(Boolean);
                if (english.length) details("英文解释", english.join("\n"));
                const examples = result.examples?.length
                    ? result.examples
                    : senses.flatMap((s) => s.examples);
                const exampleNode = (example: {
                    english: string;
                    chinese: string;
                }) => {
                    const row = selectionElement(doc, "div");
                    row.className = "st-example";
                    const en = selectionElement(doc, "p"),
                        zh = selectionElement(doc, "p");
                    zh.className = "st-label";
                    const words = [
                        selected,
                        result.headword,
                        ...(result.forms || [])
                            .filter((f) => f.label === "原形")
                            .map((f) => f.word),
                    ].filter((w): w is string => !!w);
                    const pattern = new RegExp(
                        `(${words
                            .map((w) =>
                                w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
                            )
                            .sort((a, b) => b.length - a.length)
                            .join("|")})`,
                        "gi",
                    );
                    let offset = 0;
                    for (const part of example.english.split(pattern)) {
                        const matches =
                            words.some(
                                (w) => w.toLowerCase() === part.toLowerCase(),
                            ) &&
                            !/[a-z]/i.test(example.english[offset - 1] || "") &&
                            !/[a-z]/i.test(
                                example.english[offset + part.length] || "",
                            );
                        const span = selectionElement(
                            doc,
                            matches ? "mark" : "span",
                        );
                        span.textContent = part;
                        en.append(span);
                        offset += part.length;
                    }
                    zh.textContent = example.chinese;
                    row.append(en, zh);
                    return row;
                };
                if (examples.length) {
                    paragraph("词典例句", "st-label");
                    content.append(exampleNode(examples[0]));
                    if (examples.length > 1) {
                        const more = details("更多双语例句", "");
                        more.querySelector("p")?.remove();
                        examples
                            .slice(1)
                            .forEach((e) => more.append(exampleNode(e)));
                    }
                }
                if (result.forms?.some((form) => form.label !== "原形"))
                    paragraph(
                        result.forms
                            .filter((form) => form.label !== "原形")
                            .map((f) => `${f.label}：${f.word}`)
                            .join("；"),
                        "st-label",
                    );
                if (result.usage) details("用法说明", result.usage);
                copyText = dictionaryCopyText(
                    result.headword || selected,
                    result.phonetic,
                    senses,
                    result.usage,
                );
                status.textContent = result.origin;
                if (result.examples?.length)
                    copyText +=
                        "\n" +
                        result.examples
                            .map((e) => `${e.english}\n${e.chinese}`)
                            .join("\n");
            } else {
                if (result.kind === "paragraph")
                    paragraph("所在段落译文", "st-label");
                paragraph(result.text, "st-result");
                if (result.kind === "missing" && result.paragraph)
                    details("所在段落译文", result.paragraph);
                if (
                    result.kind === "translation" ||
                    result.kind === "paragraph"
                )
                    status.textContent = result.origin;
                if ("incomplete" in result && result.incomplete)
                    details(
                        "部分公式未还原",
                        "旧译文中无法恢复的公式或排版已标为 ⟦原排版内容⟧，此处复用已有结果。",
                    );
            }
            if (result.notice) paragraph(result.notice, "st-label");
            if (
                (result.kind === "missing" || result.kind === "error") &&
                action === "lookup" &&
                controls?.onMode
            ) {
                const translate = button(getString("selection-translate-word"));
                translate.addEventListener("click", () =>
                    controls?.onMode?.("translate"),
                );
                content.append(translate);
            }
            if (result.reference)
                details(
                    "已有段落译文",
                    result.reference.text +
                        (result.reference.incomplete
                            ? "\n部分公式未还原，缺失内容保留原有标记。"
                            : ""),
                );
            if (action === "translate" && !controls)
                details("所选原文", selected);
            if (result.kind !== "error" && result.kind !== "missing") {
                if (result.kind !== "dictionary") copyText = result.text;
                copy.disabled = false;
                favorite.disabled = Boolean(controls?.favoriteBusy);
            }
            fit();
        },
    };
}
export type SelectionPopup = ReturnType<typeof createSelectionPopup>;
