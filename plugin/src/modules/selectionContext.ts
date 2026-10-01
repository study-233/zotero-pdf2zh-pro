/** Bounded selectable-text context from the current page and its immediate neighbors. */
type TextItem = {
    str?: string;
    transform?: number[];
    height?: number;
    hasEOL?: boolean;
};
const normalize = (text: string) =>
    text
        .normalize("NFKC")
        .replace(/\u00ad/g, "")
        .replace(/\s+/g, " ")
        .trim();

export function reconstructParagraphs(items: TextItem[]): string[] {
    const paragraphs: string[] = [];
    let current = "";
    let previous: TextItem | undefined;
    for (const item of items) {
        if (typeof item.str !== "string") continue;
        const y = item.transform?.[5];
        const oldY = previous?.transform?.[5];
        const height = Math.max(item.height || 0, previous?.height || 0, 1);
        const gap =
            y !== undefined && oldY !== undefined ? Math.abs(y - oldY) : 0;
        // Large vertical gaps or a jump back up the page usually start a new
        // paragraph/column. PDF text order is retained; this is not layout OCR.
        if (
            current &&
            (gap > height * 1.8 ||
                (y !== undefined && oldY !== undefined && y - oldY > height))
        ) {
            paragraphs.push(normalize(current));
            current = "";
        }
        current += item.str + (item.hasEOL ? "\n" : " ");
        previous = item;
    }
    if (current.trim()) paragraphs.push(normalize(current));
    return paragraphs.filter(Boolean);
}

function selectionRange(text: string, paragraphs: string[]) {
    const normalized = paragraphs.map(normalize);
    const start = normalized.join(" ").indexOf(text);
    if (start < 0) return undefined;
    const end = start + text.length;
    let offset = 0,
        first = -1,
        last = -1;
    normalized.forEach((paragraph, index) => {
        if (offset < end && offset + paragraph.length > start) {
            if (first < 0) first = index;
            last = index;
        }
        offset += paragraph.length + 1;
    });
    return first < 0 ? undefined : { first, last };
}

export function resolveSelectionContext(
    selected: string,
    paragraphs: string[],
): { level: "word" | "sentence" | "paragraph"; context: string } {
    const text = normalize(selected);
    const sentences = text.match(/[^.!?。！？]+(?:[.!?。！？]+|$)/g) || [];
    const level =
        text.split(/\s+/).length <= 3 && !/[。！？]/.test(text)
            ? "word"
            : sentences.length <= 1
              ? "sentence"
              : "paragraph";
    const range = selectionRange(text, paragraphs);
    if (!range) return { level, context: text.slice(0, 12000) };
    let context = paragraphs.slice(range.first, range.last + 1).join(" ");
    if (level === "word") {
        context =
            (
                context.match(/[^.!?。！？]+(?:[.!?。！？]+|$)/g) || [context]
            ).find((sentence) => normalize(sentence).includes(text)) || context;
    } else if (level === "paragraph") {
        context = paragraphs
            .slice(Math.max(0, range.first - 1), range.last + 2)
            .join("\n\n");
    }
    // Keep the selection in the context even inside unusually long paragraphs.
    const start = Math.max(0, context.indexOf(text) - 2000);
    return { level, context: context.slice(start, start + 12000).trim() };
}

type PageDocument = {
    numPages?: number;
    getPage(
        page: number,
    ): Promise<{ getTextContent(): Promise<{ items: TextItem[] }> }>;
};
// Gecko wraps objects returned by PDF.js promises when crossing the Reader
// compartment. Promise.then and class methods may only be accessible through
// wrappedJSObject; unwrap both promises and their fulfilled values.
function readerObject<T>(value: T): T {
    return (value as T & { wrappedJSObject?: T })?.wrappedJSObject || value;
}

const pageCache = new WeakMap<PageDocument, Map<number, Promise<string[]>>>();

export async function getSelectionContext(
    reader: _ZoteroTypes.ReaderInstance,
    page: number | undefined,
    text: string,
): Promise<string> {
    const fallback = normalize(text).slice(0, 12000);
    if (!page || !Number.isInteger(page) || page < 1) return fallback;
    try {
        // Guard the private PDF.js bridge; native selection text remains usable
        // when Zotero changes reader internals or the document has no text layer.
        const view = reader._internalReader?._primaryView;
        const pdf = (view as _ZoteroTypes.Reader.PDFView)?._iframeWindow
            ?.PDFViewerApplication?.pdfDocument as PageDocument | undefined;
        if (!pdf) return fallback;
        let cache = pageCache.get(pdf);
        if (!cache) {
            cache = new Map();
            pageCache.set(pdf, cache);
        }
        const read = (number: number) => {
            let pending = cache.get(number);
            if (!pending) {
                pending = (async () => {
                    const value = await readerObject(pdf.getPage(number));
                    const content = await readerObject(
                        readerObject(value).getTextContent(),
                    );
                    return reconstructParagraphs(readerObject(content).items);
                })();
                cache.set(number, pending);
                if (cache.size > 8) cache.delete(cache.keys().next().value!);
                const requested = pending;
                pending.catch(() => {
                    if (cache.get(number) === requested) cache.delete(number);
                });
            }
            return pending;
        };
        const paragraphs = await read(page);
        const current = resolveSelectionContext(text, paragraphs);
        // Without a trustworthy page count, retain the current-page fallback.
        if (
            !Number.isInteger(pdf.numPages) ||
            page > pdf.numPages! ||
            !paragraphs.length
        )
            return current.context;
        const range = selectionRange(normalize(text), paragraphs);
        const margin = current.level === "paragraph" ? 1 : 0;
        const needPrevious = page > 1 && (!range || range.first <= margin);
        const needNext =
            page < pdf.numPages! &&
            (!range || range.last >= paragraphs.length - 1 - margin);
        const adjacent = async (number: number, tail: boolean) => {
            let timer: ReturnType<typeof setTimeout> | undefined;
            try {
                const result = await Promise.race([
                    read(number),
                    new Promise<string[]>((resolve) => {
                        timer = setTimeout(() => resolve([]), 1500);
                    }),
                ]);
                // At most two paragraphs / 800 characters from either page edge.
                const snippet = (
                    tail ? result.slice(-2) : result.slice(0, 2)
                ).join(" ");
                return (
                    tail ? snippet.slice(-800) : snippet.slice(0, 800)
                ).trim();
            } catch {
                return "";
            } finally {
                if (timer !== undefined) clearTimeout(timer);
            }
        };
        const [previous, next] = await Promise.all([
            needPrevious ? adjacent(page - 1, true) : "",
            needNext ? adjacent(page + 1, false) : "",
        ]);
        if (!range) {
            // The selection itself may straddle the page break. Only join text
            // actually obtained from these pages; never infer missing content.
            return resolveSelectionContext(
                text,
                [previous, ...paragraphs, next].filter(Boolean),
            ).context;
        }
        // Keep the central selection's context when neighboring pages repeat it.
        const budget = Math.max(0, 12000 - current.context.length - 4);
        const beforeLength = Math.min(previous.length, Math.floor(budget / 2));
        const before = beforeLength ? previous.slice(-beforeLength) : "";
        const after = next.slice(0, budget - before.length);
        return [budget >= 2 ? before : "", current.context, after]
            .filter(Boolean)
            .join("\n\n");
    } catch {
        return fallback;
    }
}
