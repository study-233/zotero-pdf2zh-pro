// Zotero's PDF reader exposes glyphs in PDF coordinates, including line breaks.
// Keep the bridge optional so the saved paragraph text is usable on older readers.
type Rect = number[];
type Glyph = {
    u: string;
    rect: Rect;
    lineBreakAfter?: boolean;
    paragraphBreakAfter?: boolean;
};
type Position = { pageIndex: number; rects: Rect[] };
type Reader = _ZoteroTypes.ReaderInstance;
type PageView = {
    div: HTMLElement;
    viewport: {
        width: number;
        height: number;
        convertToViewportPoint(x: number, y: number): number[];
    };
};
type PDFView = {
    initializedPromise?: Promise<void>;
    _pdfPages?: Record<number, { chars?: Glyph[] }>;
    _ensureBasicPageData?(pageIndex: number): Promise<void>;
    _iframeWindow?: Window & {
        PDFViewerApplication?: {
            pdfViewer: { getPageView(i: number): PageView | undefined };
            pdfDocument: {
                getPageData(args: {
                    pageIndex: number;
                }): Promise<{ chars: Glyph[] }>;
            };
            eventBus?: {
                on(
                    name: string,
                    fn: (e: { pageNumber?: number }) => void,
                ): void;
                off(
                    name: string,
                    fn: (e: { pageNumber?: number }) => void,
                ): void;
            };
        };
    };
};

function unwrap<T>(object: T): T {
    return (object as T & { wrappedJSObject?: T })?.wrappedJSObject || object;
}

function activeView(reader: Reader): PDFView | undefined {
    const engine = reader._internalReader as unknown as {
        _lastView?: PDFView;
        _primaryView?: PDFView;
    };
    return engine?._lastView || engine?._primaryView;
}

export async function pageGlyphs(
    reader: Reader,
    pageIndex: number,
): Promise<Glyph[]> {
    try {
        const view = activeView(reader);
        if (!view) return [];
        if (view.initializedPromise) await unwrap(view.initializedPromise);
        if (!view._pdfPages?.[pageIndex]?.chars && view._ensureBasicPageData)
            await unwrap(view._ensureBasicPageData(pageIndex));
        const loaded = view._pdfPages?.[pageIndex]?.chars;
        if (loaded) return loaded;
        const win = view._iframeWindow;
        const pdf = win?.PDFViewerApplication?.pdfDocument;
        if (pdf && win) {
            const data = await unwrap(
                pdf.getPageData(Components.utils.cloneInto({ pageIndex }, win)),
            );
            return unwrap(data).chars || [];
        }
    } catch {
        /* Missing text layer or unsupported reader. */
    }
    return [];
}

function folded(text: string) {
    return text
        .normalize("NFKC")
        .toLowerCase()
        .replace(/[\s\p{Cc}\u00ad\u200b]/gu, "");
}

type GlyphIndex = { stream: string; offsets: number[]; glyphs: Glyph[] };
const indicesByPage = new WeakMap<Glyph[], Map<number, GlyphIndex>>();

function indexPage(chars: Glyph[], signedEdge: number) {
    let sides = indicesByPage.get(chars);
    if (!sides) {
        sides = new Map();
        indicesByPage.set(chars, sides);
    }
    const cached = sides.get(signedEdge);
    if (cached) return cached;
    const glyphs: Glyph[] = [];
    const offsets: number[] = [];
    let stream = "";
    for (const char of chars) {
        if (
            !char.u ||
            char.rect?.length !== 4 ||
            !char.rect.every(Number.isFinite)
        )
            continue;
        const middle = (char.rect[0] + char.rect[2]) / 2;
        if (
            (signedEdge > 0 && middle >= signedEdge) ||
            (signedEdge < 0 && middle <= -signedEdge)
        )
            continue;
        const piece = folded(char.u);
        if (!piece) continue;
        for (let i = 0; i < piece.length; i++) offsets.push(glyphs.length);
        glyphs.push(char);
        stream += piece;
    }
    const index = { stream, offsets, glyphs };
    sides.set(signedEdge, index);
    return index;
}

export function paragraphPosition(
    chars: Glyph[],
    pageIndex: number,
    text: string,
    signedEdge = 0,
): Position | undefined {
    const needle = folded(text);
    if (!needle) return;
    const index = indexPage(chars, signedEdge);
    const { offsets, glyphs } = index;
    let stream = index.stream;
    let start = stream.indexOf(needle);
    let indices = offsets;
    // Some PDFs insert a hyphen at a visual line break in an otherwise unbroken word.
    // Try joining only these lines, and only if the literal search missed.
    if (start < 0 && stream.includes("-")) {
        const joined: string[] = [];
        indices = [];
        for (let i = 0; i < stream.length; i++) {
            if (stream[i] === "-" && glyphs[offsets[i]].lineBreakAfter)
                continue;
            joined.push(stream[i]);
            indices.push(offsets[i]);
        }
        stream = joined.join("");
        start = stream.indexOf(needle);
    }
    if (start < 0 || stream.indexOf(needle, start + 1) >= 0) return;
    const rects: Rect[] = [];
    let line: Rect | undefined;
    for (let i = indices[start]; i <= indices[start + needle.length - 1]; i++) {
        const c = glyphs[i];
        const [x1, y1, x2, y2] = c.rect;
        const box = [
            Math.min(x1, x2),
            Math.min(y1, y2),
            Math.max(x1, x2),
            Math.max(y1, y2),
        ];
        // Some PDF text layers omit line breaks between columns or text runs.
        const height = box[3] - box[1];
        if (
            line &&
            (box[1] >= line[3] ||
                box[3] <= line[1] ||
                box[0] > line[2] + 2 * height ||
                box[2] < line[0] - 2 * height)
        )
            line = undefined;
        if (line) {
            line[0] = Math.min(line[0], box[0]);
            line[1] = Math.min(line[1], box[1]);
            line[2] = Math.max(line[2], box[2]);
            line[3] = Math.max(line[3], box[3]);
        } else {
            line = box;
            rects.push(line);
        }
        if (c.lineBreakAfter || c.paragraphBreakAfter) line = undefined;
    }
    return rects.length ? { pageIndex, rects } : undefined;
}

function overlaps(a: Rect, b: Rect) {
    return (
        Math.min(a[2], b[2]) > Math.max(a[0], b[0]) &&
        Math.min(a[3], b[3]) > Math.max(a[1], b[1])
    );
}

export function selectedParagraphs(
    chars: Glyph[],
    position: { pageIndex: number; rects?: Rect[] },
    pairs: [string, string][],
    edge: number,
) {
    if (!chars.length || !position.rects?.length) return [];
    return pairs.filter(([, translated]) => {
        const location = paragraphPosition(
            chars,
            position.pageIndex,
            translated,
            -edge,
        );
        return (
            location &&
            position.rects!.every((box) =>
                location.rects.some((row) => overlaps(row, box)),
            )
        );
    });
}

// Percentage positions survive zoom. Rebuild when PDF.js replaces a page or changes its rotation.
export function markParagraph(
    reader: Reader,
    position: Position,
    visibleOnly = true,
): (() => void) | undefined {
    const win = activeView(reader)?._iframeWindow;
    const app = win?.PDFViewerApplication;
    if (!app || !win) return;
    let overlay: HTMLElement | undefined;
    function paint(visibleOnly = false) {
        const page = app!.pdfViewer.getPageView(position.pageIndex);
        if (!page?.div.isConnected) return false;
        const { div, viewport } = page;
        const bounds = div.getBoundingClientRect();
        const frame = div.ownerDocument
            .getElementById("viewerContainer")
            ?.getBoundingClientRect();
        const boxes = position.rects.map((rect) => {
            const [x1, y1] = viewport.convertToViewportPoint(rect[0], rect[1]);
            const [x2, y2] = viewport.convertToViewportPoint(rect[2], rect[3]);
            return [
                Math.min(x1, x2) / viewport.width,
                Math.min(y1, y2) / viewport.height,
                Math.abs(x2 - x1) / viewport.width,
                Math.abs(y2 - y1) / viewport.height,
            ];
        });
        if (
            visibleOnly &&
            (!frame ||
                !boxes.some(([x, y, w, h]) =>
                    overlaps(
                        [
                            bounds.left + x * bounds.width,
                            bounds.top + y * bounds.height,
                            bounds.left + (x + w) * bounds.width,
                            bounds.top + (y + h) * bounds.height,
                        ],
                        [frame.left, frame.top, frame.right, frame.bottom],
                    ),
                ))
        )
            return false;
        overlay?.remove();
        const doc = div.ownerDocument;
        overlay = doc.createElementNS("http://www.w3.org/1999/xhtml", "div");
        overlay.className = "pdf2zh-source-mark";
        overlay.setAttribute("aria-hidden", "true");
        overlay.style.cssText =
            "position:absolute;inset:0;pointer-events:none;z-index:4";
        for (const [x, y, w, h] of boxes) {
            const line = doc.createElementNS(
                "http://www.w3.org/1999/xhtml",
                "div",
            );
            line.style.cssText = `position:absolute;left:${100 * x}%;top:${100 * y}%;width:${100 * w}%;height:${100 * h}%;background:rgba(255,176,44,.30);box-shadow:inset 0 -1px 0 rgba(200,124,10,.65);border-radius:2px;pointer-events:none`;
            overlay.append(line);
        }
        div.append(overlay);
        return true;
    }
    if (!paint(visibleOnly)) return;
    const redraw = (event: { pageNumber?: number }) => {
        if (event?.pageNumber === position.pageIndex + 1) paint();
    };
    app.eventBus?.on("pagerendered", redraw);
    return () => {
        app.eventBus?.off("pagerendered", redraw);
        overlay?.remove();
    };
}
