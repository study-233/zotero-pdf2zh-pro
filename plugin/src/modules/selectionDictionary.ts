import { config } from "../../package.json";
import { getPref } from "../utils/prefs";
import { lookupImportedDictionary } from "./selectionDictionaryStore";

type Entry = [phonetic: string, translation: string];
let dictionary: Promise<Record<string, Entry>> | undefined;

async function readDictionary(): Promise<Record<string, Entry>> {
    const url = `chrome://${config.addonRef}/content/dictionaries/ecdict.json`;
    const file = Zotero.File;
    // getContentsAsync(URL) returns an HTTP response object on Zotero 10.
    // These APIs explicitly return UTF-8 text, including packaged jar resources.
    const raw: unknown =
        typeof file.getResourceAsync === "function"
            ? await file.getResourceAsync(url)
            : await file.getContentsFromURLAsync(url);
    if (typeof raw !== "string")
        throw new Error("Invalid dictionary resource type");
    const data: unknown = JSON.parse(raw);
    if (!data || Array.isArray(data) || typeof data !== "object")
        throw new Error("Invalid dictionary structure");
    const entries = Object.entries(data);
    if (
        !entries.length ||
        entries.some(
            ([word, entry]) =>
                !word.trim() ||
                !Array.isArray(entry) ||
                entry.length !== 2 ||
                typeof entry[0] !== "string" ||
                typeof entry[1] !== "string" ||
                !entry[1].trim(),
        )
    )
        throw new Error("Invalid dictionary entries");
    return data as Record<string, Entry>;
}

/** Conservative plural fallback, only used after exact dictionary lookups fail. */
function pluralCandidates(word: string): string[] {
    if (!/^[a-z]{3,}$/.test(word)) return [];
    const irregular: Record<string, string> = {
        children: "child",
        women: "woman",
        men: "man",
        teeth: "tooth",
        feet: "foot",
        mice: "mouse",
        geese: "goose",
        indices: "index",
        matrices: "matrix",
        analyses: "analysis",
        criteria: "criterion",
    };
    if (Object.prototype.hasOwnProperty.call(irregular, word))
        return [irregular[word]];
    if (/[^aeiou]ies$/.test(word)) return [word.slice(0, -3) + "y"];
    if (/(ches|shes|sses|xes|zzes)$/.test(word)) return [word.slice(0, -2)];
    if (/s$/.test(word) && !/(ss|us|is)$/.test(word))
        return [word.slice(0, -1)];
    return [];
}

/** Local dictionaries only; exact entries always precede plural fallbacks. */
export async function lookupDictionary(
    text: string,
    sourceLang: string,
    targetLang: string,
) {
    if (
        !/^en(?:-|$)/i.test(sourceLang) ||
        !/^(zh|zh-cn|zh-hans)$/i.test(targetLang.replace(/_/g, "-"))
    )
        return undefined;
    const word = text
        .normalize("NFKC")
        .replace(/\u00ad/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .toLowerCase();
    if (!word || word.length > 100 || word.split(" ").length > 3)
        return undefined;
    let notice: string | undefined;
    let importedAvailable = getPref("selectionDictionary") === "collins";
    const imported = async (candidate: string) => {
        if (!importedAvailable) return undefined;
        try {
            const entry = await lookupImportedDictionary(candidate);
            if (!entry) return undefined;
            return {
                ...entry,
                text: entry.senses
                    .map((s) => [s.pos, s.chinese].filter(Boolean).join(" "))
                    .join("\n"),
                origin: entry.aiGenerated ? "AI 补充释义" : "柯林斯离线词典",
                ...(candidate.toLowerCase() !== word
                    ? { notice: `词形还原：${word} → ${entry.headword}` }
                    : {}),
            };
        } catch {
            importedAvailable = false;
            notice = "柯林斯词库读取失败，已使用 ECDICT；可在设置中重新导入。";
            return undefined;
        }
    };
    const exact = await imported(text);
    if (exact) return exact;
    if (!dictionary) {
        dictionary = readDictionary().catch(() => {
            dictionary = undefined;
            throw new Error("离线词典读取失败，请重新安装插件后重试。");
        });
    }
    let entries: Record<string, Entry> | undefined;
    let resourceError: unknown;
    try {
        entries = await dictionary;
    } catch (error) {
        resourceError = error;
    }
    const bundled = (candidate: string) => {
        const entry =
            entries && Object.prototype.hasOwnProperty.call(entries, candidate)
                ? entries[candidate]
                : undefined;
        if (!entry) return undefined;
        return {
            headword: candidate,
            phonetic: entry[0],
            text: entry[1],
            origin: "ECDICT 离线词典",
            notice:
                [
                    notice,
                    candidate !== word
                        ? `词形还原：${word} → ${candidate}`
                        : undefined,
                ]
                    .filter(Boolean)
                    .join("；") || undefined,
        };
    };
    const exactBundled = bundled(word);
    if (exactBundled) return exactBundled;
    for (const candidate of pluralCandidates(word)) {
        const result = (await imported(candidate)) || bundled(candidate);
        if (result) return result;
    }
    if (resourceError) throw resourceError;
    if (notice) throw new Error("柯林斯词库读取失败，请重新导入。");
    return undefined;
}
