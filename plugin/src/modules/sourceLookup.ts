import { getString } from "../utils/locale";

import { pageGlyphs, paragraphPosition, markParagraph } from "./sourcePosition";

export type SourceMap = {
    version: 1;
    sourceHash: string;
    outputHash: string;
    targetLang: string;
    pages: number[];
    split?: number[];
    paragraphs: Record<string, [string, string][]>;
};

export function checkedSourceMap(value: SourceMap, outputHash: string) {
    if (
        value?.version !== 1 ||
        value.outputHash !== outputHash ||
        !/^[a-f0-9]{64}$/.test(value.sourceHash) ||
        typeof value.targetLang !== "string" ||
        !Array.isArray(value.pages) ||
        !value.pages.length ||
        !value.pages.every((p) => Number.isSafeInteger(p) && p !== 0) ||
        (value.split !== undefined &&
            (!Array.isArray(value.split) ||
                value.split.length !== value.pages.length ||
                !value.split.every(
                    (edge) => Number.isFinite(edge) && edge !== 0,
                ))) ||
        !value.paragraphs ||
        typeof value.paragraphs !== "object" ||
        Array.isArray(value.paragraphs) ||
        !Object.entries(value.paragraphs).every(
            ([page, rows]) =>
                /^[1-9]\d*$/.test(page) &&
                Array.isArray(rows) &&
                rows.every(
                    (row) =>
                        Array.isArray(row) &&
                        row.length === 2 &&
                        row.every((t) => typeof t === "string" && t.trim()),
                ),
        )
    )
        throw new Error(getString("source-lookup-invalid"));
    return value;
}

export type SourceBinding = { sourceKey: string; map: SourceMap };

// undefined is an ordinary PDF. null is a known older output with no frozen map.
export async function readSourceBinding(
    item: Zotero.Item,
): Promise<SourceBinding | null | undefined> {
    if (await Zotero.DB.tableExists("pdf2zhSourceMaps")) {
        const row = (await Zotero.DB.rowQueryAsync(
            "SELECT sourceKey, snapshot FROM pdf2zhSourceMaps WHERE itemID = ?",
            [item.id],
        )) as { sourceKey: string; snapshot: string } | false;
        if (row)
            return { sourceKey: row.sourceKey, map: JSON.parse(row.snapshot) };
    }
    if (await Zotero.DB.tableExists("pdf2zhOutputImports")) {
        const imported = await Zotero.DB.valueQueryAsync(
            "SELECT 1 FROM pdf2zhOutputImports WHERE libraryID = ? AND itemKey = ? LIMIT 1",
            [item.libraryID, item.key],
        );
        if (imported) return null;
    }
}

// Zotero selection rectangles use the PDF's own coordinates, independent of zoom.
export function isOriginalSide(
    map: SourceMap,
    position?: { pageIndex: number; rects?: number[][] },
) {
    if (!position) return false;
    if (map.pages[position.pageIndex] < 0) return true;
    const split = map.split?.[position.pageIndex];
    if (!split || !position.rects?.length) return false;
    return position.rects.every(
        (rect) =>
            rect.length === 4 &&
            (split > 0
                ? Math.max(rect[0], rect[2]) <= split
                : Math.min(rect[0], rect[2]) >= -split),
    );
}

function normalized(text: string) {
    return text
        .normalize("NFKC")
        .replace(/[\u00ad\u200b]/g, "")
        .replace(/(\p{L})-\s*\n\s*(?=\p{Ll})/gu, "$1")
        .replace(/\s+/g, " ")
        .replace(
            /(?<=[\p{sc=Han}\p{sc=Hiragana}\p{sc=Katakana}]) | (?=[\p{sc=Han}\p{sc=Hiragana}\p{sc=Katakana}])/gu,
            "",
        )
        .trim();
}

export function matchSourceParagraphs(
    map: SourceMap,
    outputPage: number | undefined,
    selection: string,
) {
    const mapped = outputPage && map.pages[outputPage - 1];
    if (!mapped) return { candidates: [], uncertain: false };
    const page = Math.abs(mapped);
    const needle = normalized(selection);
    const candidates = (mapped > 0 && needle ? map.paragraphs[page] || [] : [])
        .filter(([, translated]) => normalized(translated).includes(needle))
        .map(([source]) => source);
    return {
        page,
        candidates,
        uncertain: candidates.length > 1 || [...needle].length < 4,
    };
}

type Reader = _ZoteroTypes.ReaderInstance;

const returnControls = new Map<Document, () => void>();

export function clearSourceReturnControls() {
    for (const remove of returnControls.values()) remove();
}

function offerReturn(
    reader: Reader,
    origin: Reader,
    pageOnly: boolean,
    clearMark?: () => void,
) {
    const doc = reader._iframeWindow?.document;
    if (!doc?.body) {
        clearMark?.();
        return;
    }
    returnControls.get(doc)?.();
    const button = doc.createElementNS(
        "http://www.w3.org/1999/xhtml",
        "button",
    );
    button.setAttribute("type", "button");
    const label = getString("source-lookup-return");
    button.textContent = pageOnly
        ? getString("source-lookup-page-only") + " · " + label
        : label;
    button.style.cssText =
        "position:fixed;inset-inline-start:12px;top:48px;z-index:1000;padding:5px 10px;border:1px solid var(--fill-quinary,GrayText);border-radius:5px;background:var(--material-background,Canvas);color:var(--fill-primary,CanvasText);font:menu;cursor:pointer";
    const remove = () => {
        clearMark?.();
        button.remove();
        doc.defaultView?.removeEventListener("pagehide", remove);
        returnControls.delete(doc);
    };
    button.addEventListener("click", async () => {
        const live = Zotero.Reader._readers.includes(origin);
        if (live && origin.tabID)
            origin._window?.Zotero_Tabs.select(origin.tabID);
        else if (live) origin._window?.focus();
        else if (origin.itemID) await Zotero.Reader.open(origin.itemID);
        remove();
    });
    returnControls.set(doc, remove);
    doc.defaultView?.addEventListener("pagehide", remove, { once: true });
    doc.body.append(button);
}

export async function openSourcePage(
    libraryID: number,
    binding: SourceBinding,
    page: number,
    source?: string,
    origin?: Reader,
) {
    const item = await Zotero.Items.getByLibraryAndKeyAsync(
        libraryID,
        binding.sourceKey,
    );
    const path =
        item &&
        !item.deleted &&
        !item.parentItem?.deleted &&
        (await item.getFilePathAsync());
    if (!path || !(await IOUtils.exists(path)))
        throw new Error(getString("source-lookup-missing"));
    if (
        (await IOUtils.computeHexDigest(path, "sha256")) !==
        binding.map.sourceHash
    )
        throw new Error(getString("source-lookup-changed"));
    const pageIndex = page - 1;
    let reader = await Zotero.Reader.open(item.id, { pageIndex });
    if (!origin) return;
    // Selecting a hibernated tab starts an asynchronous Reader.open and returns undefined.
    for (let attempt = 0; !reader && attempt < 40; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        reader = Zotero.Reader._readers.find((r) => r.itemID === item.id);
    }
    if (!reader) return;
    await reader._initPromise;
    const pos =
        source &&
        paragraphPosition(
            await pageGlyphs(reader, pageIndex),
            pageIndex,
            source,
        );
    let clearMark: (() => void) | undefined;
    if (pos) {
        await reader.navigate({ position: pos });
        try {
            clearMark = markParagraph(reader, pos, false);
        } catch {
            /* Reader.navigate also shows Zotero's native location marker. */
        }
    }
    offerReturn(reader, origin, Boolean(source && !pos), clearMark);
}
