import { getPref, setPref } from "../utils/prefs";
import {
    createSelectionPopup,
    type SelectionPopup,
    type SelectionLearning,
    type SelectionResult,
} from "./selectionPopup";
import {
    emptySelectionPane,
    openSelectionPane,
    watchSelectionPane,
} from "./selectionPane";

/** Stable result state; moving between documents replaces only the presentation. */
export function createSelectionView(
    reader: _ZoteroTypes.ReaderInstance,
    doc: Document,
    anchor: HTMLElement,
    selected: string,
    action: "lookup" | "translate",
    onClose: () => void,
    id: string,
    onDegraded: (stage: string) => void,
) {
    let popup!: SelectionPopup;
    let host: HTMLElement | undefined;
    let closed = false;
    let switchVersion = 0;
    let pinned = getPref("selectionPopupPinned") === true;
    let result: SelectionResult | undefined;
    let loading = "正在查询…";
    let learning: SelectionLearning | undefined;
    let wantsPane = getPref("selectionDisplayMode") === "sidebar";
    const width = Number(getPref("selectionPopupWidth"));
    const height = Number(getPref("selectionPopupHeight"));
    let size = width > 0 && height > 0 ? { width, height } : undefined;
    const left = Number(getPref("selectionPopupLeft") ?? -1);
    const top = Number(getPref("selectionPopupTop") ?? -1);
    let position =
        Number.isFinite(left) && Number.isFinite(top) && left >= 0 && top >= 0
            ? { left, top }
            : undefined;
    let cleanup = () => {};

    function savePosition(value: { left: number; top: number }) {
        position = { left: Math.round(value.left), top: Math.round(value.top) };
        setPref("selectionPopupLeft", position.left);
        setPref("selectionPopupTop", position.top);
    }

    function mount(nextHost?: HTMLElement) {
        if (closed) return;
        const oldPopup = popup;
        const oldHost = host;
        if (oldPopup && !host) {
            pinned = oldPopup.pinned;
            savePosition(oldPopup.card.getBoundingClientRect());
        }
        const expanded = Array.from(
            oldPopup?.card.querySelectorAll("details") || [],
        ).map((node) => (node as HTMLDetailsElement).open);
        const scroll = oldPopup?.card.querySelector(".st-body")?.scrollTop || 0;
        // Keep the old presentation recoverable until the replacement is ready.
        if (oldPopup) oldPopup.card.id = `${id}-moving`;
        if (nextHost && nextHost !== oldHost) nextHost.replaceChildren();
        let replacement: SelectionPopup | undefined;
        try {
            replacement = createSelectionPopup(
                nextHost?.ownerDocument || doc,
                anchor,
                selected,
                action,
                close,
                id,
                onDegraded,
                {
                    host: nextHost,
                    pinned,
                    size,
                    position:
                        pinned || wantsPane || oldPopup ? position : undefined,
                    onMove: savePosition,
                    onPinChange(value) {
                        pinned = value;
                        setPref("selectionPopupPinned", value);
                    },
                    onResize(value) {
                        size = value;
                        setPref("selectionPopupWidth", Math.round(value.width));
                        setPref(
                            "selectionPopupHeight",
                            Math.round(value.height),
                        );
                    },
                    onSwitch: () => {
                        void switchDisplay().catch(() => {
                            onDegraded("switch_failed");
                            popup.showNotice("切换失败，请重试。");
                        });
                    },
                    onClear: close,
                },
            );
            if (result) replacement.render(result);
            else replacement.loading(loading);
            if (learning) replacement.setLearning(learning);
        } catch (error) {
            replacement?.destroy();
            if (oldPopup) oldPopup.card.id = id;
            throw error;
        }
        oldPopup?.destroy();
        if (oldHost && oldHost !== nextHost) emptySelectionPane(oldHost);
        host = nextHost;
        popup = replacement;
        Array.from(popup.card.querySelectorAll("details")).forEach(
            (node, i) => {
                (node as HTMLDetailsElement).open = expanded[i] || false;
            },
        );
        const body = popup.card.querySelector(".st-body");
        if (body) body.scrollTop = scroll;
    }

    async function dock(persist: boolean) {
        const version = ++switchVersion;
        const target = await openSelectionPane(reader);
        if (closed || version !== switchVersion) return;
        if (!target) {
            onDegraded("sidebar_unavailable");
            if (persist) popup.showNotice("当前窗口无法使用右侧窗格。");
            return; // Keep the usable floating view and the saved preference.
        }
        if (host !== target) mount(target);
        wantsPane = true;
        if (persist) setPref("selectionDisplayMode", "sidebar");
    }

    async function switchDisplay() {
        if (host) {
            ++switchVersion;
            mount();
            wantsPane = false;
            setPref("selectionDisplayMode", "floating");
        } else await dock(true);
    }

    function close() {
        if (closed) return;
        closed = true;
        ++switchVersion;
        cleanup();
        popup?.destroy();
        if (host) emptySelectionPane(host);
        onClose();
    }

    mount();
    cleanup = watchSelectionPane(reader, (nextHost) => {
        if (!wantsPane || closed) return;
        if (nextHost !== host) {
            try {
                mount(nextHost);
            } catch {
                onDegraded("pane_remount_failed");
            }
        }
    });
    if (wantsPane) {
        // Resolve the saved sidebar before showing the initial floating fallback.
        popup.card.style.visibility = "hidden";
        void dock(false)
            .catch(() => onDegraded("sidebar_unavailable"))
            .finally(() => {
                if (!closed) popup.card.style.removeProperty("visibility");
            });
    }
    return {
        get card() {
            return popup.card;
        },
        get alive() {
            return !closed;
        },
        get pinned() {
            return popup.pinned;
        },
        get docked() {
            return Boolean(host);
        },
        close,
        updateSelection(
            text: string,
            kind: "lookup" | "translate",
            nextAnchor: HTMLElement,
        ) {
            selected = text;
            action = kind;
            anchor = nextAnchor;
            result = undefined;
            learning = undefined;
            popup.updateSelection(text, kind, nextAnchor);
        },
        loading(message: string) {
            result = undefined;
            loading = message;
            popup.loading(message);
        },
        render(value: SelectionResult) {
            result = value;
            popup.render(value);
        },
        setLearning(value: SelectionLearning) {
            learning = value;
            popup.setLearning(value);
        },
    };
}

export type SelectionView = ReturnType<typeof createSelectionView>;
