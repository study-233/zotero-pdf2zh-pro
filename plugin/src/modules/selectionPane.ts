import { config } from "../../package.json";
import { getLocaleID, getString } from "../utils/locale";
import { recordDiagnostic } from "./diagnostics";

type Reader = _ZoteroTypes.ReaderInstance;
type PaneArgs = _ZoteroTypes.ItemPaneManagerSection.SectionHookArgs;
type ItemDetails = HTMLElement & {
    tabID?: string;
    render: () => Promise<void>;
    scrollToPane: (id: string, behavior: string) => Promise<void>;
};
type Host = { body: HTMLElement; details: ItemDetails; paneID: string };
const hosts = new Map<Reader, Host>();
const listeners = new Map<Reader, (host?: HTMLElement) => void>();
let registeredID: string | undefined;

function renderPane({ body, tabType, setEnabled, paneID }: PaneArgs) {
    const details = body.closest("item-details") as ItemDetails | null;
    const tabID = details?.tabID || details?.getAttribute("data-tab-id");
    const reader = tabID ? Zotero.Reader.getByTabID(tabID) : undefined;
    setEnabled(tabType === "reader" && reader?.type === "pdf");
    if (!reader || !details || tabType !== "reader") return;
    const previous = hosts.get(reader);
    hosts.set(reader, { body, details, paneID });
    if (previous?.body !== body) listeners.get(reader)?.(body);
    if (!body.childNodes.length)
        body.textContent = getString("selection-pane-empty");
}

export function registerSelectionPane() {
    if (registeredID || !Zotero.ItemPaneManager?.registerSection) return;
    try {
        const id = Zotero.ItemPaneManager.registerSection({
            paneID: "selection-translation",
            pluginID: config.addonID,
            header: {
                l10nID: getLocaleID("selection-pane-header"),
                icon: `chrome://${config.addonRef}/content/icons/selection-translate.svg`,
            },
            sidenav: {
                l10nID: getLocaleID("selection-pane-nav"),
                icon: `chrome://${config.addonRef}/content/icons/selection-translate.svg`,
            },
            onInit: renderPane,
            onItemChange: renderPane,
            onRender: renderPane,
            onDestroy: ({ body }) => {
                for (const [reader, host] of hosts) {
                    if (host.body !== body) continue;
                    hosts.delete(reader);
                    listeners.get(reader)?.();
                }
            },
        });
        if (id) registeredID = id;
    } catch {
        recordDiagnostic("selection_pane_registration_failed");
    }
}

export function unregisterSelectionPane() {
    if (registeredID) Zotero.ItemPaneManager.unregisterSection(registeredID);
    registeredID = undefined;
    hosts.clear();
    listeners.clear();
}

export function watchSelectionPane(
    reader: Reader,
    listener: (host?: HTMLElement) => void,
) {
    listeners.set(reader, listener);
    return () => listeners.delete(reader);
}

/** Resolve by Reader tab, never by parent item (multiple attachments share a parent). */
export async function openSelectionPane(
    reader: Reader,
): Promise<HTMLElement | undefined> {
    if (!registeredID || !reader.tabID) return;
    try {
        const win = reader._window || Zotero.getMainWindow();
        const context = win.ZoteroContextPane;
        const doc = win.document;
        const details = Array.from(doc.querySelectorAll("item-details")).find(
            (element) =>
                (element as ItemDetails).tabID === reader.tabID ||
                (element as HTMLElement).getAttribute("data-tab-id") ===
                    reader.tabID,
        ) as ItemDetails | undefined;
        if (!context || !details) return;
        context.collapsed = false;
        const pane = (context.context || doc.querySelector("context-pane")) as
            (HTMLElement & { mode: string }) | null;
        if (pane) pane.mode = "item";
        await details.render();
        const host = hosts.get(reader);
        if (!host?.body.isConnected) return;
        await details.scrollToPane(host.paneID, "instant");
        return host.body;
    } catch {
        recordDiagnostic("selection_pane_open_failed");
        return undefined;
    }
}

export function emptySelectionPane(body: HTMLElement) {
    body.textContent = getString("selection-pane-empty");
}
