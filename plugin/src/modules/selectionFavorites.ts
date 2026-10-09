import { config } from "../../package.json";
import type { SelectionResult } from "./selectionPopup";

export type SelectionFavorite = {
    version: 1;
    id: string;
    word: string;
    headword?: string;
    sourceLang: string;
    targetLang: string;
    meaning: string;
    source: string;
    contextMeaning?: string;
    original: string;
    query: string;
    sentence?: string;
    title: string;
    parentKey?: string;
    attachmentKey: string;
    library: { type: "user" | "group"; groupID?: number };
    pageIndex: number;
    pageLabel?: string;
    rects?: number[][];
    fingerprint?: string;
    createdAt: string;
    updatedAt: string;
    result?: SelectionResult;
};
type Library = { version: 1; entries: SelectionFavorite[] };
let loaded: Promise<Library> | undefined;
let writes: Promise<unknown> = Promise.resolve();
const listeners = new Set<() => void>();
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));
const path = () =>
    PathUtils.join(
        Zotero.DataDirectory.dir,
        config.addonRef,
        "selection-favorites-v1.json",
    );

export function favoriteIdentity(
    entry: Pick<
        SelectionFavorite,
        "library" | "attachmentKey" | "pageIndex" | "rects" | "word"
    >,
) {
    return JSON.stringify([
        entry.library.type,
        entry.library.groupID || null,
        entry.attachmentKey,
        entry.pageIndex,
        entry.rects || [],
        entry.word.normalize("NFKC").trim().toLowerCase(),
    ]);
}
export function parseFavorites(text: string): Library {
    const data = JSON.parse(text);
    if (
        data?.version !== 1 ||
        !Array.isArray(data.entries) ||
        data.entries.length > 100000
    )
        throw new Error("不支持的收藏文件格式。");
    for (const entry of data.entries) {
        if (
            !entry ||
            entry.version !== 1 ||
            ![
                "id",
                "word",
                "sourceLang",
                "targetLang",
                "meaning",
                "source",
                "original",
                "query",
                "title",
                "attachmentKey",
                "createdAt",
                "updatedAt",
            ].every(
                (key) =>
                    typeof entry[key] === "string" &&
                    entry[key].length <= 100000,
            ) ||
            !entry.word.trim() ||
            !/^[A-Z0-9]{8}$/.test(entry.attachmentKey) ||
            !Number.isInteger(entry.pageIndex) ||
            entry.pageIndex < 0 ||
            !["user", "group"].includes(entry.library?.type) ||
            (entry.library.type === "group" &&
                (!Number.isInteger(entry.library.groupID) ||
                    entry.library.groupID < 1))
        )
            throw new Error("收藏文件包含无效记录，未导入。");
        if (
            entry.rects &&
            (!Array.isArray(entry.rects) ||
                entry.rects.length > 1000 ||
                !entry.rects.every(
                    (rect: unknown) =>
                        Array.isArray(rect) &&
                        rect.length === 4 &&
                        rect.every(Number.isFinite),
                ))
        )
            throw new Error("收藏选区坐标无效。");
        for (const key of [
            "headword",
            "contextMeaning",
            "sentence",
            "parentKey",
            "pageLabel",
            "fingerprint",
        ])
            if (entry[key] !== undefined && typeof entry[key] !== "string")
                throw new Error("收藏字段无效。");
    }
    return data;
}
async function library(): Promise<Library> {
    if (!loaded)
        loaded = (async () => {
            if (!(await IOUtils.exists(path())))
                return { version: 1, entries: [] } as Library;
            try {
                return parseFavorites(await IOUtils.readUTF8(path()));
            } catch {
                try {
                    return parseFavorites(
                        await IOUtils.readUTF8(path() + ".bak"),
                    );
                } catch {
                    throw new Error(
                        "收藏库和备份无法读取。原文件已保留，请从导出文件恢复。",
                    );
                }
            }
        })().catch((error) => {
            loaded = undefined;
            throw error;
        });
    return loaded;
}
export async function listFavorites() {
    return clone((await library()).entries);
}
function mutate<T>(edit: (entries: SelectionFavorite[]) => T): Promise<T> {
    const task = writes
        .catch(() => {})
        .then(async () => {
            const current = await library();
            const next = clone(current);
            const result = edit(next.entries);
            await IOUtils.makeDirectory(PathUtils.parent(path())!, {
                ignoreExisting: true,
            });
            // Back up the last validated state, including after recovery from a damaged file.
            await IOUtils.writeUTF8(path() + ".bak", JSON.stringify(current), {
                tmpPath: path() + ".bak.tmp",
            });
            await IOUtils.writeUTF8(path(), JSON.stringify(next), {
                tmpPath: path() + ".tmp",
            });
            loaded = Promise.resolve(next);
            listeners.forEach((fn) => {
                try {
                    fn();
                } catch {
                    /* A closed pane cannot invalidate a saved file. */
                }
            });
            return result;
        });
    writes = task;
    return task;
}
export function saveFavorite(entry: SelectionFavorite, update = false) {
    return mutate((entries) => {
        const index = entries.findIndex(
            (old) => favoriteIdentity(old) === favoriteIdentity(entry),
        );
        if (index < 0) entries.push(entry);
        else if (update)
            entries[index] = {
                ...entry,
                id: entries[index].id,
                createdAt: entries[index].createdAt,
            };
        return clone(entries[index < 0 ? entries.length - 1 : index]);
    });
}
export function deleteFavorite(id: string) {
    return mutate((entries) => {
        const index = entries.findIndex((e) => e.id === id);
        if (index >= 0) entries.splice(index, 1);
    });
}
export function importFavorites(text: string) {
    const incoming = parseFavorites(text);
    return mutate((entries) => {
        const identities = new Set(entries.map(favoriteIdentity)),
            ids = new Set(entries.map((e) => e.id));
        let added = 0,
            conflicts = 0;
        for (const entry of incoming.entries) {
            const identity = favoriteIdentity(entry);
            if (identities.has(identity) || ids.has(entry.id)) {
                conflicts++;
                continue;
            }
            entries.push(entry);
            identities.add(identity);
            ids.add(entry.id);
            added++;
        }
        return { added, conflicts };
    });
}
export async function exportFavorites(csv = false) {
    const entries = await listFavorites();
    if (!csv) return JSON.stringify({ version: 1, entries }, null, 2);
    const keys = [
        "word",
        "headword",
        "meaning",
        "contextMeaning",
        "original",
        "query",
        "sentence",
        "source",
        "title",
        "attachmentKey",
        "pageLabel",
        "createdAt",
    ] as const;
    const cell = (value: string) =>
        '"' + value.replace(/^[=+@-]/, "'$&").replace(/"/g, '""') + '"';
    return (
        "\uFEFF" +
        [
            keys.join(","),
            ...entries.map((e) => keys.map((k) => cell(e[k] || "")).join(",")),
        ].join("\r\n")
    );
}
export function watchFavorites(fn: () => void) {
    listeners.add(fn);
    return () => listeners.delete(fn);
}
export function favoriteLibrary(
    libraryID: number,
): SelectionFavorite["library"] {
    const library = Zotero.Libraries.get(libraryID);
    if (!library) throw new Error("找不到所属文献库。");
    return library.libraryType === "group"
        ? {
              type: "group",
              groupID: Zotero.Groups.getGroupIDFromLibraryID(libraryID),
          }
        : { type: "user" };
}
export async function openFavorite(entry: SelectionFavorite): Promise<string> {
    const libraryID =
        entry.library.type === "group"
            ? Zotero.Groups.getLibraryIDFromGroupID(entry.library.groupID!)
            : Zotero.Libraries.userLibraryID;
    if (!libraryID) throw new Error("无法访问该群组库。请检查群组权限。");
    const item = Zotero.Items.getByLibraryAndKey(
        libraryID,
        entry.attachmentKey,
    );
    if (!item || item.deleted)
        throw new Error("原附件已删除或尚未同步，收藏内容仍然保留。");
    const file = await item.getFilePathAsync();
    if (!file || !(await IOUtils.exists(file)))
        throw new Error("附件尚未下载，请先在 Zotero 中下载附件。");
    const changed =
        !!entry.fingerprint &&
        (await IOUtils.computeHexDigest(file, "sha256")) !== entry.fingerprint;
    await Zotero.Reader.open(item.id, {
        pageIndex: entry.pageIndex,
        ...(!changed && entry.rects
            ? { position: { pageIndex: entry.pageIndex, rects: entry.rects } }
            : {}),
    });
    return changed
        ? "附件已变化，已定位到页码；原选区可能已经变化。"
        : "已返回原文。";
}
