/** Reader-owned result card. Source/provider text is always rendered as text. */
import type { DictionarySense } from "./selectionDictionaryStore";
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
    onResize?: (size: { width: number; height: number }) => void;
    onSwitch?: () => void;
    onClear?: () => void;
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
    card.dataset.docked = String(docked);
    let copyText = "";
    const cleanups: (() => void)[] = [];
    function close(notify = true) {
        if (closed) return;
        closed = true;
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
.pdf2zh-selection-card { --st-bg:#fff; --st-text:#202124; --st-muted:#626874; --st-border:#dce0e5; --st-soft:#f3f5f7; --st-accent:#3c64ba; position:fixed; z-index:2147483646; box-sizing:border-box; width:360px; max-width:calc(100vw - 16px); max-height:calc(100vh - 16px); display:flex; flex-direction:column; background:var(--st-bg); color:var(--st-text); border:1px solid var(--st-border); border-radius:12px; box-shadow:0 8px 28px #0003; font:14px/1.6 system-ui,-apple-system,sans-serif; text-align:left; user-select:text; overflow:hidden; color-scheme:light; }
.pdf2zh-selection-card[data-dark="true"] { --st-bg:#25272c; --st-text:#e8eaed; --st-muted:#adb3bf; --st-border:#444851; --st-soft:#32353c; --st-accent:#a4bfff; color-scheme:dark; }
.pdf2zh-selection-card * { box-sizing:border-box; }
.pdf2zh-selection-card [hidden] { display:none !important; }
.pdf2zh-selection-card button { appearance:none; display:inline-flex; align-items:center; justify-content:center; width:auto; min-width:0; margin:0; border:1px solid var(--st-border); border-radius:6px; padding:4px 10px; background:var(--st-bg); color:var(--st-text); font:inherit; line-height:1.4; cursor:pointer; }
.pdf2zh-selection-card button:hover { background:var(--st-soft); }
.pdf2zh-selection-card button:focus-visible, .pdf2zh-selection-card summary:focus-visible { outline:2px solid var(--st-accent); outline-offset:2px; }
.pdf2zh-selection-card button:disabled { opacity:.5; cursor:default; }
.pdf2zh-selection-card .st-header { display:flex; align-items:center; gap:4px; flex-wrap:wrap; padding:7px 10px; background:var(--st-soft); flex-shrink:0; cursor:grab; touch-action:none; user-select:none; }
.pdf2zh-selection-card .st-header strong { flex:1; font-size:12px; font-weight:600; color:var(--st-muted); }
.pdf2zh-selection-card .st-header button { font-size:12px; padding:4px 7px; background:transparent; border-color:transparent; }
.pdf2zh-selection-card .st-header button[aria-pressed="true"] { color:var(--st-accent); border-color:var(--st-accent); }
.pdf2zh-selection-card .st-body { padding:14px 16px; overflow:auto; min-height:0; max-height:340px; flex:1 1 auto; overflow-wrap:anywhere; overscroll-behavior:contain; }
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
.pdf2zh-selection-card .st-resize { position:absolute; right:0; bottom:0; width:14px; height:14px; cursor:nwse-resize; touch-action:none; background:linear-gradient(135deg,transparent 55%,var(--st-muted) 56%,var(--st-muted) 62%,transparent 63%,transparent 75%,var(--st-muted) 76%,var(--st-muted) 82%,transparent 83%); }
.pdf2zh-selection-card[data-docked="true"] { position:relative; z-index:auto; width:100%; max-width:100%; max-height:none; border:0; border-radius:0; box-shadow:none; }
.pdf2zh-selection-card[data-docked="true"] .st-header { cursor:default; }
.pdf2zh-selection-card[data-docked="true"] .st-body { max-height:60vh; padding:12px 8px; }
.pdf2zh-selection-card .st-context-heading { display:block; font-size:12px; color:var(--st-muted); margin-bottom:6px; }
.pdf2zh-selection-card .st-context-actions { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
`;

    const header = selectionElement(doc, "header");
    header.className = "st-header";
    const title = selectionElement(doc, "strong");
    title.textContent = "翻译";
    function button(label: string) {
        const node = selectionElement(doc, "button");
        node.type = "button";
        node.textContent = label;
        node.setAttribute("aria-label", label);
        return node;
    }
    const pin = button("固定");
    pin.setAttribute("aria-pressed", String(pinned));
    pin.textContent = pinned ? "已固定" : "固定";
    pin.hidden = docked;
    const closeButton = button("关闭");
    closeButton.textContent = docked ? "清空" : "关闭";
    closeButton.setAttribute("aria-label", closeButton.textContent);
    const switchButton = button(docked ? "切回悬浮窗" : "移到右侧");
    switchButton.hidden = !options.onSwitch;
    switchButton.addEventListener("click", () => options.onSwitch?.());
    header.append(title, pin, switchButton, closeButton);
    const body = selectionElement(doc, "div");
    body.className = "st-body";
    body.setAttribute("aria-live", "polite");
    const footer = selectionElement(doc, "footer");
    footer.className = "st-footer";
    const status = selectionElement(doc, "span");
    status.className = "st-status";
    const content = selectionElement(doc, "div");
    const learningContent = selectionElement(doc, "div");
    body.append(content, learningContent);
    const refresh = button("重翻译文");
    refresh.hidden = true;
    let learning: SelectionLearning | undefined;
    refresh.addEventListener("click", () => learning?.onRefresh());
    const copy = button("复制");
    copy.disabled = true;
    footer.append(status, refresh, copy);
    card.append(style, header, body, footer);
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
            const rect = card.getBoundingClientRect();
            position(rect.left, rect.top);
        });
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
            const rect = anchor.getBoundingClientRect();
            position(rect.left, rect.bottom + 8);
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
            row.append(definition);
            list.append(row);
        }
        return list;
    }
    function reset() {
        content.replaceChildren();
        status.textContent = "";
        copyText = "";
        copy.disabled = true;
        if (action === "lookup") {
            paragraph(selected, "st-word");
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
            section.style.cssText =
                "border-top:1px solid var(--st-border);margin-top:12px;padding-top:10px";
            const heading = selectionElement(doc, "strong");
            heading.textContent = panel.title;
            heading.className = "st-context-heading";
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
            if (panel.error) addText(panel.error, true);
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
                const copyPart = button("复制" + panel.title);
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
            pin.textContent = pinned ? "已固定" : "固定";
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
        nearSelection();
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
                try {
                    if (header.hasPointerCapture?.(drag.pointer))
                        header.releasePointerCapture(drag.pointer);
                } catch {
                    /* Capture may already be lost. */
                }
                drag = undefined;
            };
            cleanups.push(stop);
            listen(header, "pointerdown", (raw) => {
                const event = raw as PointerEvent;
                if (
                    (event.target as Element)?.closest?.(
                        "button,a,input,textarea,select",
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
        updateSelection(
            text: string,
            kind: "lookup" | "translate",
            nextAnchor: HTMLElement,
        ) {
            selected = text;
            action = kind;
            learning = undefined;
            learningContent.replaceChildren();
            refresh.hidden = true;
            anchor = nextAnchor;
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
                if (result.phonetic)
                    paragraph(
                        `/${result.phonetic.replace(/^\/+|\/+$/g, "")}/`,
                        "st-phonetic",
                    );
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
                const examples = senses.flatMap((sense, i) =>
                    sense.examples.map(
                        (example) =>
                            `${i + 1}. ${example.english}\n${example.chinese}`,
                    ),
                );
                if (examples.length) details("双语例句", examples.join("\n\n"));
                if (result.usage) details("用法说明", result.usage);
                copyText = dictionaryCopyText(
                    result.headword || selected,
                    result.phonetic,
                    senses,
                    result.usage,
                );
                status.textContent = result.origin;
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
            if (result.reference)
                details(
                    "已有段落译文",
                    result.reference.text +
                        (result.reference.incomplete
                            ? "\n部分公式未还原，缺失内容保留原有标记。"
                            : ""),
                );
            if (action === "translate") details("所选原文", selected);
            if (result.kind !== "error" && result.kind !== "missing") {
                if (result.kind !== "dictionary") copyText = result.text;
                copy.disabled = false;
            }
            fit();
        },
    };
}
export type SelectionPopup = ReturnType<typeof createSelectionPopup>;
