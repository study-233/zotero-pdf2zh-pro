// Align locally against the immutable paragraph pair saved with this PDF.
function sentences(text: string) {
    const parts: string[] = [];
    let start = 0;
    for (const match of text.matchAll(/[.!?。！？؟।]+["'”’»）)」』]*/gu)) {
        const end = match.index! + match[0].length;
        if (match[0] === "." && end < text.length) {
            // A decimal, DOI, initial, or a reference such as "Fig. 2".
            if (
                !/\s|\p{sc=Han}/u.test(text[end]) ||
                /(?:\b(?:figs?|eqs?|refs?|sec|dr|prof|mr|mrs|ms|vs|e\.g|i\.e)|\bet\s+al|\b[A-Z]|\b(?:[a-z]\.)+[a-z])\.$/iu.test(
                    text.slice(start, end),
                )
            )
                continue;
        }
        const part = text.slice(start, end).trim();
        if (part) parts.push(part);
        start = end;
    }
    const tail = text.slice(start).trim();
    if (tail) parts.push(tail);
    return parts;
}

function folded(text: string) {
    return text
        .normalize("NFKC")
        .toLowerCase()
        .replace(/[\s\u00ad\u200b]/gu, "");
}

// Only terms occurring in exactly one sentence on each side can anchor a pair.
// Repeated terms such as BLEU in two adjacent results cannot shift the alignment.
function uniqueTerms(parts: string[]) {
    const owners = new Map<string, number>();
    parts.forEach((part, i) => {
        const terms =
            part
                .normalize("NFKC")
                .toLowerCase()
                .match(/[a-z][a-z0-9]*(?:[-.][a-z0-9]+)*|\d+(?:\.\d+)*/g) || [];
        for (const term of new Set(terms)) {
            if (term.length < 2 && !/^\d$/.test(term)) continue;
            owners.set(term, owners.has(term) ? -1 : i);
        }
    });
    return owners;
}

export function sourceSentences(
    source: string,
    translated: string,
    selection: string,
    underSelection?: (candidates: string[]) => string | undefined,
): string | undefined {
    const src = sentences(source);
    const dst = sentences(translated);
    const needle = folded(selection);
    if (!src.length || !dst.length || !/[\p{L}\p{N}]/u.test(needle)) return;
    const pieces = dst.map(folded);
    const stream = pieces.join("");
    const at = stream.indexOf(needle);
    if (at < 0) return;
    if (src.length === 1) return src[0];
    if (dst.length === 1) return;
    let offset = 0;
    const ends = pieces.map((part) => (offset += part.length));
    function touchedAt(start: number) {
        return pieces.flatMap((_, i) =>
            (ends[i - 1] || 0) < start + needle.length && ends[i] > start
                ? [i]
                : [],
        );
    }
    let touched = touchedAt(at);
    let next = stream.indexOf(needle, at + 1);
    if (next >= 0) {
        if (touched.length !== 1) return;
        const choices = new Set(touched);
        while (next >= 0) {
            const range = touchedAt(next);
            if (range.length !== 1) return;
            choices.add(range[0]);
            next = stream.indexOf(needle, next + 1);
        }
        if (choices.size > 1) {
            const options = [...choices].map((i) => dst[i]);
            if (new Set(options).size !== options.length) return;
            const hit = underSelection?.(options);
            if (!hit || !options.includes(hit)) return;
            touched = [dst.indexOf(hit)];
        }
    }

    const srcTerms = uniqueTerms(src);
    const links = new Map<number, number>();
    for (const [term, d] of uniqueTerms(dst)) {
        const s = srcTerms.get(term);
        if (d < 0 || s === undefined || s < 0) continue;
        if (links.has(d) && links.get(d) !== s) return;
        links.set(d, s);
    }
    // Equal-sized gaps between anchors can be paired in reading order. A crossed
    // anchor, or an unexplained merge/split touching the selection, is ambiguous.
    const anchors = [
        [-1, -1],
        ...Array.from(links).sort(([a], [b]) => a - b),
        [dst.length, src.length],
    ];
    const aligned = new Map<number, number>();
    const uncertain = new Set<number>();
    for (let i = 1; i < anchors.length; i++) {
        const [d, s] = anchors[i];
        const [pd, ps] = anchors[i - 1];
        if (s <= ps) return;
        if (d < dst.length) aligned.set(d, s);
        if (d - pd === s - ps) {
            for (let j = pd + 1; j < d; j++) aligned.set(j, ps + j - pd);
        } else {
            // Either adjoining sentence could have absorbed words from the gap.
            uncertain.add(pd);
            uncertain.add(d);
        }
    }
    if (
        !touched.length ||
        touched.some((d) => !aligned.has(d) || uncertain.has(d))
    )
        return;
    const first = aligned.get(touched[0])!;
    const last = aligned.get(touched[touched.length - 1])!;
    if (last - first + 1 !== touched.length) return;
    return src.slice(first, last + 1).join(" ");
}
