import { getPref, setPref } from "../utils/prefs";
import type { GlossaryEntry } from "./pdf2zhTypes";

type CsvRow = { fields: string[]; line: number };

function csvError(line: number, message: string): Error {
    return new Error(`CSV 第 ${line} 行：${message}`);
}

function readCsv(text: string): CsvRow[] {
    const rows: CsvRow[] = [];
    let fields: string[] = [];
    let value = "";
    let state: "plain" | "quoted" | "closed" = "plain";
    let line = 1;
    let rowLine = 1;
    let quoteLine = 1;
    let hasContent = false;
    const finishRow = () => {
        fields.push(value);
        if (hasContent) rows.push({ fields, line: rowLine });
        fields = [];
        value = "";
        state = "plain";
        hasContent = false;
    };
    text = text.replace(/^\uFEFF/, "");
    for (let index = 0; index < text.length; index++) {
        const char = text[index];
        const newline = char === "\n" || char === "\r";
        if (state === "quoted") {
            if (char === '"') {
                if (text[index + 1] === '"') {
                    value += '"';
                    index += 1;
                } else state = "closed";
            } else if (newline) {
                value += "\n";
                if (char === "\r" && text[index + 1] === "\n") index += 1;
                line += 1;
            } else value += char;
            continue;
        }
        if (newline) {
            finishRow();
            if (char === "\r" && text[index + 1] === "\n") index += 1;
            rowLine = ++line;
        } else if (char === ",") {
            fields.push(value);
            value = "";
            state = "plain";
            hasContent = true;
        } else if (char === '"') {
            if (state === "closed" || value.trim())
                throw csvError(
                    line,
                    "引号必须包住整个字段；字段内引号请写成两个双引号。",
                );
            value = "";
            state = "quoted";
            quoteLine = line;
            hasContent = true;
        } else if (state === "closed") {
            if (char !== " " && char !== "\t")
                throw csvError(line, "结束引号后只能是逗号或换行。");
        } else {
            value += char;
            if (char.trim()) hasContent = true;
        }
    }
    if (state === "quoted") throw csvError(quoteLine, "字段的双引号未闭合。");
    finishRow();
    return rows;
}

function uniqueEntries(
    rows: { entry: GlossaryEntry; line: number }[],
): GlossaryEntry[] {
    const seen = new Map<string, { entry: GlossaryEntry; line: number }>();
    for (const { entry, line } of rows) {
        const key = JSON.stringify([
            entry.source.replace(/\s+/g, " ").toLowerCase(),
            entry.tgt_lng.toLowerCase().replace(/_/g, "-"),
        ]);
        const previous = seen.get(key);
        if (previous && previous.entry.target !== entry.target)
            throw csvError(
                line,
                `译法与第 ${previous.line} 行冲突，请为相同术语和目标语言保留一种译法。`,
            );
        if (!previous) seen.set(key, { entry, line });
    }
    return [...seen.values()].map(({ entry }) => entry);
}

export function parseGlossaryCsv(text: string): GlossaryEntry[] {
    const rows = readCsv(text);
    const header = rows.shift();
    const columns = header?.fields.map((field) => field.trim()) || [];
    if (
        !columns.includes("source") ||
        !columns.includes("target") ||
        columns.some(
            (column) => !["source", "target", "tgt_lng"].includes(column),
        ) ||
        new Set(columns).size !== columns.length
    )
        throw csvError(
            header?.line || 1,
            "表头必须包含 source,target，可选 tgt_lng，不能有重复或未知列。",
        );
    return uniqueEntries(
        rows.map(({ fields, line }) => {
            if (fields.length !== columns.length)
                throw csvError(
                    line,
                    `应有 ${columns.length} 列，实际为 ${fields.length} 列。`,
                );
            const source = fields[columns.indexOf("source")].trim();
            const target = fields[columns.indexOf("target")].trim();
            const languageIndex = columns.indexOf("tgt_lng");
            const tgt_lng =
                languageIndex < 0 ? "" : fields[languageIndex].trim();
            if (!source || !target)
                throw csvError(line, "source 和 target 不能为空。");
            return { entry: { source, target, tgt_lng }, line };
        }),
    );
}

export function loadGlossaryEntries(): GlossaryEntry[] {
    const raw = getPref("glossaryEntries");
    if (raw === undefined || raw === "") return [];
    try {
        const values = JSON.parse(String(raw)) as unknown;
        if (!Array.isArray(values)) throw new Error();
        const rows = values.map((value, index) => {
            if (
                !value ||
                typeof value.source !== "string" ||
                !value.source.trim() ||
                typeof value.target !== "string" ||
                !value.target.trim() ||
                (value.tgt_lng !== undefined &&
                    typeof value.tgt_lng !== "string")
            )
                throw new Error();
            return {
                entry: {
                    source: value.source.trim(),
                    target: value.target.trim(),
                    tgt_lng: value.tgt_lng?.trim() || "",
                },
                line: index + 1,
            };
        });
        return uniqueEntries(rows);
    } catch {
        throw new Error(
            "无法读取已保存的术语表，请重新导入或清除术语表；原数据已保留。",
        );
    }
}

export function importGlossaryCsv(text: string): GlossaryEntry[] {
    const entries = parseGlossaryCsv(text);
    setPref("glossaryEntries", JSON.stringify(entries));
    return entries;
}

export function clearGlossaryEntries(): void {
    setPref("glossaryEntries", "[]");
}
