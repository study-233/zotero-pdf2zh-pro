import { config } from "../../package.json";
import { getString } from "../utils/locale";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { PDF2zhTaskManager } from "./pdf2zhTaskManager";
import { recordDiagnostic } from "./diagnostics";

import { closePageTrialDialogs, selectTrialPages } from "./pageTrialDialog";

export function parseTrialPages(input: string, total: number) {
    if (!Number.isSafeInteger(total) || total <= 0 || input.length > 160)
        return;
    const ranges: [number, number][] = [];
    for (const part of input.split(/[,，]/)) {
        const match = /^\s*(\d+)\s*(?:[-–—]\s*(\d+))?\s*$/.exec(part);
        if (!match) return;
        const start = Number(match[1]),
            end = Number(match[2] ?? match[1]);
        if (start < 1 || end < start || end > total) return;
        ranges.push([start, end]);
    }
    ranges.sort((a, b) => a[0] - b[0]);
    const merged: [number, number][] = [];
    for (const range of ranges) {
        const last = merged[merged.length - 1];
        if (last && range[0] <= last[1] + 1)
            last[1] = Math.max(last[1], range[1]);
        else merged.push(range);
    }
    return {
        pages: merged
            .map(([a, b]) => (a === b ? String(a) : a + "-" + b))
            .join(","),
        count: merged.reduce((n, [a, b]) => n + b - a + 1, 0),
    };
}

export async function openPageTrial(
    reader?: _ZoteroTypes.ReaderInstance,
    clickedPageIndex?: number,
) {
    const win = reader?._window || Zotero.getMainWindow();
    try {
        const items = reader
            ? reader.itemID
                ? Zotero.Items.get([reader.itemID])
                : []
            : win.ZoteroPane.getSelectedItems();
        const selected = items.length === 1 && items[0];
        const item =
            selected && (await PDF2zhHelperFactory.getAttachmentItem(selected));
        if (!item || !item.isPDFAttachment()) {
            win.alert(getString("trial-select-one"));
            return;
        }
        const internal = reader?._internalReader;
        const stats =
            internal?._lastViewPrimary === false
                ? internal._state.secondaryViewStats
                : internal?._state.primaryViewStats;
        let total = stats?.pagesCount;
        if (!total) {
            const indexed = await Zotero.Fulltext.getPages(item.id);
            total =
                (indexed && indexed.total) ||
                (
                    await (
                        Zotero as unknown as {
                            PDFWorker: {
                                getFullText(
                                    id: number,
                                    limit: number,
                                ): Promise<{ totalPages: number }>;
                            };
                        }
                    ).PDFWorker.getFullText(item.id, 1)
                ).totalPages;
        }
        if (!Number.isSafeInteger(total) || total < 1)
            throw new Error("Page count unavailable");
        const pageIndex = clickedPageIndex ?? stats?.pageIndex;
        const pages = await selectTrialPages(
            win,
            item.attachmentFilename,
            total,
            pageIndex === undefined ? "" : String(pageIndex + 1),
            parseTrialPages,
        );
        if (pages) await PDF2zhTaskManager.processWorker({ item, pages });
    } catch {
        recordDiagnostic("page_trial_open_failed");
        win.alert(getString("trial-open-error"));
    }
}

const readerMenu: _ZoteroTypes.Reader.EventHandler<"createViewContextMenu"> = ({
    reader,
    append,
    params,
}) => {
    if (reader.type === "pdf") {
        const hit = params as { position?: { pageIndex?: number } };
        append({
            label: getString("trial-current"),
            onCommand: () =>
                void openPageTrial(reader, hit?.position?.pageIndex),
        });
    }
};
export function registerPageTrial() {
    Zotero.Reader.registerEventListener(
        "createViewContextMenu",
        readerMenu,
        config.addonID,
    );
}
export function unregisterPageTrial() {
    closePageTrialDialogs();
    Zotero.Reader.unregisterEventListener("createViewContextMenu", readerMenu);
}
