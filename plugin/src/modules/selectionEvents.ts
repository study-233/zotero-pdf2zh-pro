/** Events in PDF frames don't bubble into the Reader or the owning Zotero window. */
export function watchSelectionDocuments(
    doc: Document,
    owner: Document | undefined,
    callbacks: {
        input: (event: Event) => void;
        dismiss: (event: Event) => void;
        escape: () => void;
        dispose: () => void;
    },
    onDegraded: () => void,
) {
    let disposed = false;
    const removers: (() => void)[] = [];
    const removeListeners = () => {
        for (const remove of removers.splice(0)) {
            try {
                remove();
            } catch {
                onDegraded();
            }
        }
    };
    const bind = (
        target: EventTarget,
        type: string,
        callback: EventListener,
    ) => {
        try {
            target.addEventListener(type, callback, true);
            removers.push(() =>
                target.removeEventListener(type, callback, true),
            );
        } catch {
            onDegraded();
        }
    };
    const rebuild = () => {
        if (disposed) return;
        removeListeners();
        const visited = new Set<Document>();
        const visit = (current: Document, children: boolean) => {
            if (visited.has(current)) return;
            visited.add(current);
            bind(current, "mousedown", (raw) => {
                const target = raw.target as Element | null;
                // Page navigation and native scrollbar dragging are reading actions,
                // not outside-click dismissal. Labels differ across Zotero locales.
                if (target?.closest?.("scrollbar,scrollbarbutton,slider,thumb"))
                    return;
                if (
                    children &&
                    target?.closest?.(
                        ".toolbar-button.pageUp,.toolbar-button.pageDown,#pageNumber",
                    )
                )
                    return;
                if (target?.getBoundingClientRect) {
                    const event = raw as MouseEvent;
                    const rect = target.getBoundingClientRect();
                    if (
                        (target.scrollHeight > target.clientHeight &&
                            target.clientWidth > 0 &&
                            event.clientX >=
                                rect.left +
                                    target.clientLeft +
                                    target.clientWidth) ||
                        (target.scrollWidth > target.clientWidth &&
                            target.clientHeight > 0 &&
                            event.clientY >=
                                rect.top +
                                    target.clientTop +
                                    target.clientHeight)
                    )
                        return;
                }
                callbacks.input(raw);
            });
            bind(current, "keydown", (raw) => {
                if ((raw as KeyboardEvent).key === "Escape") callbacks.escape();
            });
            if (!children) return;
            for (const frame of Array.from(
                current.querySelectorAll("iframe"),
            ) as HTMLIFrameElement[]) {
                bind(frame, "load", rebuild);
                try {
                    if (frame.contentDocument)
                        visit(frame.contentDocument, true);
                } catch {
                    onDegraded();
                }
            }
        };
        visit(doc, true);
        if (owner) visit(owner, false);
        if (doc.defaultView) {
            bind(doc.defaultView, "pagehide", callbacks.dispose);
        }
    };
    // Do not interpret the event that opened the native selection popup as an outside click.
    const timer = setTimeout(rebuild, 0);
    return () => {
        disposed = true;
        clearTimeout(timer);
        removeListeners();
    };
}
