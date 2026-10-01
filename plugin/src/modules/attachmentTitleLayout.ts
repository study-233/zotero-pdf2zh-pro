import {
    DEFAULT_TITLE_TEMPLATE,
    TITLE_VARIABLES,
    renderAttachmentTitle,
    validateTitleTemplate,
    type TitleContext,
} from "./attachmentNaming";

export type TitleBlock =
    | { kind: "field"; field: keyof TitleContext }
    | { kind: "text"; text: string };
export type TitleLayout = {
    version: 1;
    blocks: TitleBlock[];
    separator: string;
};
export const defaultTitleLayout = (): TitleLayout => ({
    version: 1,
    blocks: [
        { kind: "field", field: "title" },
        { kind: "field", field: "type" },
    ],
    separator: " · ",
});
export function validTitleLayout(value: unknown): value is TitleLayout {
    if (!value || typeof value !== "object") return false;
    const v = value as TitleLayout;
    return (
        v.version === 1 &&
        typeof v.separator === "string" &&
        v.separator.length <= 30 &&
        Array.isArray(v.blocks) &&
        v.blocks.length <= 100 &&
        v.blocks.every(
            (b) =>
                b &&
                (b.kind === "field"
                    ? TITLE_VARIABLES.includes(b.field)
                    : b.kind === "text" &&
                      typeof b.text === "string" &&
                      b.text.length <= 1000),
        )
    );
}
export function parseTitleLayout(raw: unknown): TitleLayout | undefined {
    if (typeof raw !== "string" || !raw) return undefined;
    try {
        const value: unknown = JSON.parse(raw);
        return validTitleLayout(value) ? value : undefined;
    } catch {
        return undefined;
    }
}
export function hasTitleContent(layout: TitleLayout) {
    return layout.blocks.some((b) => b.kind === "field" || b.text.trim());
}
export function renderTitleLayout(
    layout: TitleLayout,
    context: TitleContext,
): string {
    const result = layout.blocks
        .map((b) => (b.kind === "field" ? context[b.field] || "" : b.text))
        .filter((text) => (layout.separator ? text.trim() !== "" : text !== ""))
        .join(layout.separator)
        .replace(/\s+/g, " ")
        .trim();
    return result || renderAttachmentTitle(DEFAULT_TITLE_TEMPLATE, context);
}
/** Exact text tokenization preserves custom punctuation and repeated legacy fields. */
export function migrateTitleTemplate(
    template: string,
): TitleLayout | undefined {
    if (!validateTitleTemplate(template)) return undefined;
    template = template.trim() || DEFAULT_TITLE_TEMPLATE;
    const blocks: TitleBlock[] = [];
    let end = 0;
    for (const match of template.matchAll(/\{([^{}]+)\}/g)) {
        if (match.index! > end)
            blocks.push({
                kind: "text",
                text: template.slice(end, match.index),
            });
        blocks.push({ kind: "field", field: match[1] as keyof TitleContext });
        end = match.index! + match[0].length;
    }
    if (end < template.length)
        blocks.push({ kind: "text", text: template.slice(end) });
    // Recognize a uniform separator between fields, with no leading/trailing literals.
    const literals = blocks.filter((b) => b.kind === "text");
    if (
        blocks.length > 1 &&
        blocks[0].kind === "field" &&
        blocks.at(-1)!.kind === "field" &&
        blocks.every((b, i) => b.kind === (i % 2 ? "text" : "field")) &&
        literals.length &&
        literals.every(
            (b) =>
                b.kind === "text" &&
                b.text === (literals[0] as { text: string }).text,
        ) &&
        [" · ", " - ", "_", " ", ""].includes(
            (literals[0] as { text: string }).text,
        )
    ) {
        const layout: TitleLayout = {
            version: 1,
            blocks: blocks.filter((b) => b.kind === "field"),
            separator: (literals[0] as { text: string }).text,
        };
        return validTitleLayout(layout) ? layout : undefined;
    }
    const layout: TitleLayout = { version: 1, blocks, separator: "" };
    return validTitleLayout(layout) ? layout : undefined;
}
