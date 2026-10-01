import { config } from "../../package.json";
import { setPref } from "../utils/prefs";
import {
    getImportedDictionaryInfo,
    installDownloadedDictionary,
    type DictionaryInfo,
} from "./selectionDictionaryStore";
import { resetSelectionTranslation } from "./selectionTranslate";
import { recordDiagnostic } from "./diagnostics";

export const DICTIONARY_CATALOG_URL =
    "https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/glossary-data/dictionaries/catalog.json";
const BASE = "https://raw.githubusercontent.com/study-233/zotero-pdf2zh-pro/";
const MAX_PACKAGE = 40 * 1024 * 1024;
const MAX_EXPANDED = 80 * 1024 * 1024;
export type DictionaryPackage = {
    id: "collins-en-zh";
    version: string;
    url: string;
    sha256: string;
    sizeBytes: number;
    entryCount: number;
    aiEntries: number;
};
export type DictionaryDownloadState = {
    phase: "idle" | "checking" | "downloading" | "installing" | "failed";
    available?: DictionaryPackage;
    installed?: DictionaryInfo;
    received: number;
    error?: "catalog" | "download" | "storage";
};
const state: DictionaryDownloadState = { phase: "idle", received: 0 };
const listeners = new Set<() => void>();
let operation: { cancelled: boolean; abort?: () => void } | undefined;
let choiceRevision = 0;
const emit = () => {
    for (const fn of listeners) {
        try {
            fn();
        } catch {
            /* A closing preferences document must not break installation. */
        }
    }
};
export const dictionaryDownloadState = () => ({ ...state });
export function subscribeDictionaryDownload(fn: () => void) {
    listeners.add(fn);
    return () => {
        listeners.delete(fn);
    };
}
export function dictionaryChoiceChanged() {
    choiceRevision++;
}
export function cancelDictionaryDownload() {
    if (!operation || state.phase === "installing") return;
    operation.cancelled = true;
    operation.abort?.();
}
export function validateDictionaryCatalog(raw: unknown): DictionaryPackage {
    const data = raw as { schemaVersion?: number; packs?: unknown[] };
    if (!data || data.schemaVersion !== 1 || !Array.isArray(data.packs))
        throw new Error("Invalid catalog");
    const pack = data.packs.find(
        (p) => (p as DictionaryPackage)?.id === "collins-en-zh",
    ) as DictionaryPackage;
    if (
        !pack ||
        !/^\d{4}\.\d{2}\.\d{2}(?:\.\d+)?$/.test(pack.version) ||
        typeof pack.url !== "string" ||
        !pack.url.startsWith(BASE) ||
        !/^[a-f0-9]{64}$/.test(pack.sha256) ||
        !Number.isInteger(pack.sizeBytes) ||
        pack.sizeBytes < 1 ||
        pack.sizeBytes > MAX_PACKAGE ||
        !Number.isInteger(pack.entryCount) ||
        pack.entryCount < 1 ||
        !Number.isInteger(pack.aiEntries) ||
        pack.aiEntries < 0 ||
        pack.aiEntries > pack.entryCount
    )
        throw new Error("Invalid package");
    const url = new URL(pack.url);
    if (
        url.search ||
        url.hash ||
        url.username ||
        url.password ||
        !url.pathname.endsWith(".json.gz")
    )
        throw new Error("Invalid URL");
    return pack;
}
async function request(
    url: string,
    limit: number,
    op: NonNullable<typeof operation>,
    progress = false,
) {
    const result = await Zotero.HTTP.request("GET", url, {
        responseType: "arraybuffer",
        timeout: progress ? 180000 : 20000,
        errorDelayMax: 0,
        noCache: !progress,
        cancellerReceiver: (abort: () => void) => {
            op.abort = abort;
            if (op.cancelled) abort();
        },
        requestObserver: (xhr: XMLHttpRequest) => {
            xhr.onprogress = (e) => {
                if (e.loaded > limit) {
                    xhr.abort();
                    state.error = "download";
                }
                if (progress) {
                    state.received = e.loaded;
                    emit();
                }
            };
        },
    });
    if (op.cancelled) throw new Error("Cancelled");
    const bytes = new Uint8Array(result.response as ArrayBuffer);
    if (!bytes.byteLength || bytes.byteLength > limit)
        throw new Error("Invalid response size");
    return bytes;
}
async function readGzip(bytes: Uint8Array): Promise<unknown> {
    const win = Zotero.getMainWindow() as unknown as {
        Blob: typeof Blob;
        DecompressionStream: typeof DecompressionStream;
        TextDecoder: typeof TextDecoder;
    };
    const stream = new win.Blob([new Uint8Array(bytes).buffer])
        .stream()
        .pipeThrough(new win.DecompressionStream("gzip"));
    const reader = stream.getReader();
    const decoder = new win.TextDecoder("utf-8", { fatal: true });
    let size = 0,
        text = "";
    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > MAX_EXPANDED)
                throw new Error("Expanded dictionary too large");
            text += decoder.decode(value, { stream: true });
        }
        return JSON.parse(text + decoder.decode());
    } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
    }
}
export async function refreshDictionaryDownload() {
    if (operation) return;
    const op = (operation = { cancelled: false });
    state.phase = "checking";
    state.error = undefined;
    emit();
    try {
        try {
            state.installed = await getImportedDictionaryInfo();
        } catch {
            state.installed = undefined;
        }
        emit();
        const bytes = await request(DICTIONARY_CATALOG_URL, 128 * 1024, op);
        const win = Zotero.getMainWindow() as unknown as {
            TextDecoder: typeof TextDecoder;
        };
        state.available = validateDictionaryCatalog(
            JSON.parse(
                new win.TextDecoder("utf-8", { fatal: true }).decode(bytes),
            ),
        );
        state.phase = "idle";
    } catch {
        state.phase = op.cancelled ? "idle" : "failed";
        state.error = op.cancelled ? undefined : "catalog";
        recordDiagnostic("dictionary_catalog_failed");
    } finally {
        operation = undefined;
        emit();
    }
}
export async function downloadDictionary() {
    if (operation) return;
    if (!state.available) await refreshDictionaryDownload();
    if (operation || !state.available) return;
    const pack = state.available;
    const op = (operation = { cancelled: false });
    const revision = choiceRevision;
    state.phase = "downloading";
    state.received = 0;
    state.error = undefined;
    emit();
    const temporary = PathUtils.join(
        Zotero.DataDirectory.dir,
        config.addonRef,
        "dictionaries",
        "download.json.gz.part",
    );
    try {
        const bytes = await request(pack.url, pack.sizeBytes, op, true);
        if (bytes.byteLength !== pack.sizeBytes)
            throw new Error("Package size mismatch");
        await IOUtils.makeDirectory(PathUtils.parent(temporary)!, {
            ignoreExisting: true,
            createAncestors: true,
        });
        await IOUtils.write(temporary, bytes);
        if (
            (await IOUtils.computeHexDigest(temporary, "sha256")) !==
            pack.sha256
        )
            throw new Error("Digest mismatch");
        const raw = await readGzip(bytes);
        if (op.cancelled) throw new Error("Cancelled");
        // Cancellation is disabled during the short atomic commit phase.
        state.phase = "installing";
        emit();
        state.installed = await installDownloadedDictionary(
            raw,
            pack.sha256,
            pack.version,
            pack.entryCount,
            pack.aiEntries,
            () => !op.cancelled,
        );
        if (choiceRevision === revision)
            setPref("selectionDictionary", "collins");
        resetSelectionTranslation();
        state.phase = "idle";
    } catch {
        state.phase = op.cancelled && !state.error ? "idle" : "failed";
        state.error = state.phase === "failed" ? "download" : undefined;
        recordDiagnostic("dictionary_download_failed");
    } finally {
        await IOUtils.remove(temporary, { ignoreAbsent: true }).catch(() => {});
        operation = undefined;
        emit();
    }
}
/** Local imports also invalidate in-flight enablement and refresh the summary. */
export async function dictionaryImported() {
    dictionaryChoiceChanged();
    state.installed = await getImportedDictionaryInfo();
    state.error = undefined;
    if (!operation) state.phase = "idle";
    emit();
}
