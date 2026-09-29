export const DEFAULT_TITLE_TEMPLATE = "{title} · {type}";
export const TITLE_VARIABLES = [
    "title",
    "fullTitle",
    "author",
    "year",
    "sourceLang",
    "targetLang",
    "service",
    "model",
    "type",
] as const;

export type TitleContext = Record<(typeof TITLE_VARIABLES)[number], string>;
export type TitleMetadata = Pick<
    TitleContext,
    "title" | "fullTitle" | "author" | "year"
>;

export function validateTitleTemplate(template: string): boolean {
    const remainder = template.replace(/\{([^{}]*)\}/g, (match, key) =>
        TITLE_VARIABLES.includes(key) ? "" : match,
    );
    return !/[{}]/.test(remainder);
}

export function renderAttachmentTitle(
    template: string,
    context: TitleContext,
): string {
    const selected =
        template.trim() && validateTitleTemplate(template)
            ? template
            : DEFAULT_TITLE_TEMPLATE;
    const render = (value: string) =>
        value
            .replace(
                /\{([^{}]+)\}/g,
                (_, key: keyof TitleContext) => context[key] || "",
            )
            .replace(/\s+/g, " ")
            .trim();
    return render(selected) || render(DEFAULT_TITLE_TEMPLATE);
}

export function buildTitleMetadata(data: {
    fileName: string;
    shortTitle?: string;
    fullTitle?: string;
    author?: string;
    date?: string;
}): TitleMetadata {
    const fileTitle = data.fileName.replace(/\.pdf$/i, "").trim() || "PDF";
    return {
        title: data.shortTitle?.trim() || data.fullTitle?.trim() || fileTitle,
        fullTitle: data.fullTitle?.trim() || fileTitle,
        author: data.author?.trim() || "",
        year: data.date?.match(/\b\d{4}\b/)?.[0] || "",
    };
}
