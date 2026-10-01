import { config } from "../../package.json";
import { setPref } from "../utils/prefs";

export type DictionarySense = {
    chinese: string;
    english?: string;
    pos?: string;
    examples: { english: string; chinese: string }[];
};
export type DictionaryEntry = {
    headword: string;
    phonetic?: string;
    senses: DictionarySense[];
    aiGenerated?: boolean;
    usage?: string;
};
export type DictionaryInfo = {
    version?: string;
    importedAt: string;
    entries: number;
    skipped: number;
    sha256: string;
    aiEntries: number;
};
type ImportedDictionary = {
    format: 1;
    info: DictionaryInfo;
    entries: Record<string, DictionaryEntry>;
};
let cached: Promise<ImportedDictionary | undefined> | undefined;
let importing = false;

function object(value: unknown): value is Record<string, unknown> {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function optionalText(value: unknown): string | undefined {
    if (value === undefined || value === null) return undefined;
    if (typeof value !== "string") throw new Error("词库字段格式不正确。");
    return value.trim() || undefined;
}
const normalize = (word: string) =>
    word.normalize("NFKC").replace(/\s+/g, " ").trim();

/** Adapt the user's ODH data; no dictionary content is bundled with this module. */
export function convertODHDictionary(raw: unknown) {
    if (!object(raw)) throw new Error("请选择有效的柯林斯词库 JSON 文件。");
    const entries: Record<string, DictionaryEntry> = Object.create(null);
    let skipped = 0;
    for (const [word, entry] of Object.entries(raw)) {
        if (!word.trim() || !object(entry) || !Array.isArray(entry.defs))
            throw new Error("词库条目格式不正确，未更改已有词库。");
        if (
            entry.readings !== undefined &&
            (!Array.isArray(entry.readings) ||
                entry.readings.some((x) => typeof x !== "string"))
        )
            throw new Error("词库音标格式不正确。");
        const senses: DictionarySense[] = [];
        for (const def of entry.defs) {
            if (!object(def)) throw new Error("词库释义格式不正确。");
            const chinese = optionalText(def.def_cn);
            const english = optionalText(def.def_en);
            const pos = optionalText(def.pos_en) || optionalText(def.pos_cn);
            if (!chinese || !/[\u3400-\u9fff]/u.test(chinese)) continue;
            if (def.ext !== undefined && !Array.isArray(def.ext))
                throw new Error("词库例句格式不正确。");
            const examples: DictionarySense["examples"] = [];
            for (const example of (def.ext || []) as unknown[]) {
                if (!object(example)) throw new Error("词库例句格式不正确。");
                const en = optionalText(example.ext_en),
                    cn = optionalText(example.ext_cn);
                if (en && cn && examples.length < 2)
                    examples.push({ english: en, chinese: cn });
            }
            senses.push({ chinese, english, pos, examples });
        }
        if (!senses.length) {
            skipped++;
            continue;
        }
        const headword = normalize(word);
        const ai =
            object(entry._pdf2zhSupplement) &&
            entry._pdf2zhSupplement.kind === "ai";
        entries[headword] = {
            headword,
            phonetic: (entry.readings as string[] | undefined)
                ?.find((x) => x.trim())
                ?.trim(),
            senses,
            ...(ai ? { aiGenerated: true } : {}),
        };
    }
    if (!Object.keys(entries).length)
        throw new Error("词库中没有有效中文释义，未更改已有词库。");
    return { entries, skipped };
}

function dictionaryPath() {
    return PathUtils.join(
        Zotero.DataDirectory.dir,
        config.addonRef,
        "dictionaries",
        "odh-collins.json",
    );
}
function validStored(data: unknown): data is ImportedDictionary {
    if (
        !object(data) ||
        data.format !== 1 ||
        !object(data.info) ||
        !object(data.entries)
    )
        return false;
    const entries = Object.values(data.entries);
    return Boolean(
        entries.length &&
        entries.length === data.info.entries &&
        typeof data.info.importedAt === "string" &&
        typeof data.info.sha256 === "string" &&
        Number.isInteger(data.info.skipped) &&
        Number.isInteger(data.info.aiEntries) &&
        entries.every(
            (entry) =>
                object(entry) &&
                typeof entry.headword === "string" &&
                (entry.aiGenerated === undefined ||
                    typeof entry.aiGenerated === "boolean") &&
                (entry.phonetic === undefined ||
                    typeof entry.phonetic === "string") &&
                Array.isArray(entry.senses) &&
                entry.senses.length &&
                entry.senses.every(
                    (s) =>
                        object(s) &&
                        typeof s.chinese === "string" &&
                        s.chinese.trim() &&
                        (s.english === undefined ||
                            typeof s.english === "string") &&
                        (s.pos === undefined || typeof s.pos === "string") &&
                        Array.isArray(s.examples) &&
                        s.examples.every(
                            (e) =>
                                object(e) &&
                                typeof e.english === "string" &&
                                typeof e.chinese === "string",
                        ),
                ),
        ),
    );
}
async function load() {
    if (!cached)
        cached = (async () => {
            const path = dictionaryPath();
            if (!(await IOUtils.exists(path))) return undefined;
            const data: unknown = JSON.parse(await IOUtils.readUTF8(path));
            if (!validStored(data))
                throw new Error("导入词库已损坏，请重新导入。");
            return data;
        })().catch(() => {
            cached = undefined;
            throw new Error("无法读取导入词库，请重新导入。");
        });
    return cached;
}
export async function getImportedDictionaryInfo() {
    return (await load())?.info;
}
export async function lookupImportedDictionary(word: string) {
    const data = await load();
    if (!data) return undefined;
    for (const key of [normalize(word), normalize(word).toLowerCase()]) {
        if (Object.prototype.hasOwnProperty.call(data.entries, key))
            return {
                ...data.entries[key],
                dictionarySource: data.info.version ? "download" : "import",
            };
    }
    return undefined;
}
async function saveDictionary(
    raw: unknown,
    sha256: string,
    options: {
        version?: string;
        entries?: number;
        aiEntries?: number;
        current?: () => boolean;
    } = {},
): Promise<DictionaryInfo> {
    if (importing) throw new Error("Dictionary import busy");
    importing = true;
    try {
        const converted = convertODHDictionary(raw);
        const info: DictionaryInfo = {
            importedAt: new Date().toISOString(),
            entries: Object.keys(converted.entries).length,
            skipped: converted.skipped,
            sha256,
            aiEntries: Object.values(converted.entries).filter(
                (entry) => entry.aiGenerated,
            ).length,
            ...(options.version ? { version: options.version } : {}),
        };
        if (
            (options.entries !== undefined &&
                info.entries !== options.entries) ||
            (options.aiEntries !== undefined &&
                info.aiEntries !== options.aiEntries)
        )
            throw new Error("Dictionary count mismatch");
        const destination = dictionaryPath();
        await IOUtils.makeDirectory(PathUtils.parent(destination)!, {
            ignoreExisting: true,
            createAncestors: true,
        });
        if (options.current && !options.current())
            throw new Error("Dictionary operation cancelled");
        await IOUtils.writeUTF8(
            destination,
            JSON.stringify({ format: 1, info, entries: converted.entries }),
            { tmpPath: destination + ".tmp" },
        );
        cached = undefined;
        return info;
    } finally {
        importing = false;
    }
}
export async function installDownloadedDictionary(
    raw: unknown,
    sha256: string,
    version: string,
    entries: number,
    aiEntries: number,
    current: () => boolean,
) {
    return saveDictionary(raw, sha256, {
        version,
        entries,
        aiEntries,
        current,
    });
}
export async function importODHDictionary(
    path: string,
): Promise<DictionaryInfo> {
    const raw: unknown = JSON.parse(await IOUtils.readUTF8(path));
    const info = await saveDictionary(
        raw,
        await IOUtils.computeHexDigest(path, "sha256"),
    );
    setPref("selectionDictionary", "collins");
    return info;
}
