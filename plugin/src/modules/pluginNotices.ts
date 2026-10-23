import { config, version } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import { getString } from "../utils/locale";
import { renderNotice, type NoticeAction, type UserNotice } from "./userNotice";
import {
    releaseSummaries,
    readReleaseState,
    pendingReleaseNotes,
    completeReleaseNotice,
    compareVersions,
    type ReleaseNote,
} from "./releaseNotes";

const namespace = "http://www.w3.org/1999/xhtml";
const windows = new Map<Window, () => void>();
let ready = false;
let pending: ReleaseNote[] = [];
let card: HTMLElement | undefined;
let helpWindow: Window | undefined;
let actionHandler: (action: NoticeAction) => void = () => {};

export function configureNoticeActions(handler: typeof actionHandler) {
    actionHandler = handler;
}
export function runNoticeAction(action: NoticeAction) {
    actionHandler(action);
}
export function initializeReleaseNotices(
    reason?: number,
    existingInstall = false,
) {
    const state = readReleaseState(getPref("releaseNoticeState"));
    // Migration runs before this call. Its defaults cannot identify an old install;
    // the bootstrap reason and an existing notice record can.
    pending = pendingReleaseNotes(
        releaseSummaries,
        version,
        state,
        reason === 5 && !state && !existingInstall,
    );
    ready = true;
    if (!pending.length) {
        setPref(
            "releaseNoticeState",
            JSON.stringify(completeReleaseNotice(version, state, [])),
        );
    }
    for (const win of windows.keys()) showPending(win);
}

function html<K extends keyof HTMLElementTagNameMap>(
    doc: Document,
    tag: K,
    text?: string,
) {
    const element = doc.createElementNS(
        namespace,
        tag,
    ) as HTMLElementTagNameMap[K];
    if (text !== undefined) element.textContent = text;
    return element;
}
function style(doc: Document) {
    const link = html(doc, "link");
    link.rel = "stylesheet";
    link.href = `chrome://${config.addonRef}/content/notices.css`;
    return link;
}

function showPending(win: Window) {
    if (
        !ready ||
        !pending.length ||
        card ||
        win.closed ||
        !win.document.hasFocus()
    )
        return;
    const doc = win.document;
    const panel = html(doc, "section");
    panel.className = "pdf2zh-update-card";
    panel.setAttribute("role", "region");
    panel.setAttribute(
        "aria-labelledby",
        "pdf2zh-update-name pdf2zh-update-title",
    );
    const name = html(doc, "p", config.addonName);
    name.id = "pdf2zh-update-name";
    const title = html(
        doc,
        "h2",
        getString("notice-updated", { args: { version } }),
    );
    title.id = "pdf2zh-update-title";
    panel.append(style(doc), name, title);
    const list = html(doc, "ul");
    for (const note of pending)
        list.append(
            html(doc, "li", getString(note.cardMessage ?? note.message)),
        );
    panel.append(list);
    const actions = html(doc, "div");
    actions.className = "pdf2zh-notice-actions";
    const view = html(doc, "button", getString("notice-view-updates"));
    view.className = "pdf2zh-update-details";
    view.addEventListener("click", () => {
        openNoticeHelp();
        panel.remove();
        card = undefined;
    });
    const close = html(doc, "button", getString("notice-close"));
    close.addEventListener("click", () => {
        panel.remove();
        card = undefined;
    });
    panel.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            event.stopPropagation();
            panel.remove();
            card = undefined;
        }
    });
    actions.append(view, close);
    panel.append(actions);
    card = panel;
    try {
        doc.documentElement.append(panel);
        // Wait for a painted, foreground surface. Never mark a failed render seen.
        win.requestAnimationFrame(() =>
            win.requestAnimationFrame(() => {
                if (
                    card !== panel ||
                    !panel.isConnected ||
                    !doc.hasFocus() ||
                    !panel.getClientRects().length
                ) {
                    panel.remove();
                    if (card === panel) card = undefined;
                    return;
                }
                try {
                    setPref(
                        "releaseNoticeState",
                        JSON.stringify(
                            completeReleaseNotice(
                                version,
                                readReleaseState(getPref("releaseNoticeState")),
                                pending,
                            ),
                        ),
                    );
                    pending = [];
                } catch (error) {
                    ztoolkit.log(error);
                    panel.remove();
                    card = undefined;
                }
            }),
        );
    } catch (error) {
        panel.remove();
        card = undefined;
        ztoolkit.log(error);
    }
}

export function registerNoticeWindow(win: Window) {
    if (windows.has(win)) return;
    const show = () => showPending(win);
    win.addEventListener("focus", show);
    const cleanup = () => {
        win.removeEventListener("focus", show);
        win.removeEventListener("unload", cleanup);
        if (card?.ownerDocument === win.document) {
            card.remove();
            card = undefined;
        }
        windows.delete(win);
    };
    windows.set(win, cleanup);
    win.addEventListener("unload", cleanup, { once: true });
    show();
}
export function unregisterNoticeWindow(win: Window) {
    windows.get(win)?.();
}
export function stopPluginNotices() {
    ready = false;
    pending = [];
    for (const cleanup of [...windows.values()]) cleanup();
    helpWindow?.close();
    helpWindow = undefined;
}

export function showUserNotice(node: HTMLElement, notice: UserNotice | null) {
    renderNotice(node, notice, runNoticeAction);
}

export function openNoticeHelp() {
    if (helpWindow && !helpWindow.closed) {
        helpWindow.focus();
        return;
    }
    const main = Zotero.getMainWindow();
    helpWindow =
        main.openDialog(
            `chrome://${config.addonRef}/content/noticeHelp.xhtml`,
            `${config.addonRef}-help`,
            "chrome,centerscreen,resizable,dialog=no,width=620,height=560",
            {
                message: getString,
                version,
                releases: releaseSummaries
                    .filter((r) => compareVersions(r.version, version) <= 0)
                    .slice()
                    .sort((a, b) => compareVersions(b.version, a.version)),
                guideUrl:
                    "https://github.com/study-233/zotero-pdf2zh-pro/blob/main/docs/user-guide.md",
                changelogUrl:
                    "https://github.com/study-233/zotero-pdf2zh-pro/blob/main/CHANGELOG.md",
                openLink: (url: string) => Zotero.launchURL(url),
            },
        ) || undefined;
}
