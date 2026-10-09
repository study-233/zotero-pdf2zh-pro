import { safeDictionaryAudio } from "./selectionOnlineDictionary";
import { getString } from "../utils/locale";
import { recordDiagnostic } from "./diagnostics";

type Sound = { bytes: ArrayBuffer; type: string };
type SoundLoad = {
    promise: Promise<Sound>;
    users: number;
    done: boolean;
    cancelled: boolean;
    size: number;
    cancel?: () => void;
};
const sounds = new Map<string, SoundLoad>();
const audioURL = (word: string, accent: string, url?: string) =>
    safeDictionaryAudio(url) ||
    `https://fanyi.baidu.com/gettts?lan=${accent === "英" ? "uk" : "en"}&text=${encodeURIComponent(word)}&spd=3`;

function trimSounds() {
    let size = [...sounds.values()].reduce((sum, item) => sum + item.size, 0);
    for (const [url, item] of sounds) {
        if (sounds.size <= 12 && size <= 8 * 1024 * 1024) break;
        if (!item.users && item.done) {
            sounds.delete(url);
            size -= item.size;
        }
    }
}

/** Share pending downloads and keep a small in-memory cache of completed audio. */
function acquireSound(url: string) {
    let item = sounds.get(url);
    if (item) {
        item.users++;
        sounds.delete(url);
        sounds.set(url, item);
    } else {
        const created = {
            users: 1,
            done: false,
            cancelled: false,
            size: 0,
        } as SoundLoad;
        sounds.set(url, created);
        created.promise = (async () => {
            const response = await Zotero.HTTP.request("GET", url, {
                responseType: "arraybuffer",
                timeout: 15000,
                errorDelayMax: 0,
                cancellerReceiver: (cancel: () => void) => {
                    created.cancel = cancel;
                    if (created.cancelled) cancel();
                },
            });
            if (created.cancelled) throw new Error("Pronunciation cancelled");
            const bytes = response.response as ArrayBuffer;
            if (!bytes?.byteLength || bytes.byteLength > 4 * 1024 * 1024)
                throw new Error("Invalid pronunciation response");
            const type =
                response.getResponseHeader("Content-Type")?.split(";")[0] ||
                "audio/mpeg";
            if (type.startsWith("text/") || type.includes("json"))
                throw new Error("Pronunciation response is not audio");
            created.size = bytes.byteLength;
            return { bytes, type };
        })()
            .catch((error) => {
                if (sounds.get(url) === created) sounds.delete(url);
                throw error;
            })
            .finally(() => {
                created.done = true;
                created.cancel = undefined;
                trimSounds();
            });
        item = created;
    }
    const source = item;
    let released = false;
    const discard = () => {
        if (sounds.get(url) === source) sounds.delete(url);
    };
    return {
        promise: source.promise,
        discard,
        release() {
            if (released) return;
            released = true;
            source.users--;
            if (!source.users && !source.done) {
                source.cancelled = true;
                discard();
                source.cancel?.();
            }
            trimSounds();
        },
    };
}

export function preloadSelectionAudio(
    word: string,
    accent: string,
    url?: string,
) {
    const source = acquireSound(audioURL(word, accent, url));
    // Preloading is silent. A click retries a failed download and reports errors.
    void source.promise.catch(() => {});
    return source.release;
}

type Playback = {
    button: HTMLButtonElement;
    audio: HTMLAudioElement;
    cancel?: () => void;
    release?: () => void;
    finish: () => void;
};
let active: Playback | undefined;

export function stopSelectionAudio(owner?: HTMLElement) {
    if (owner && active && !owner.contains(active.button)) return;
    const previous = active;
    active = undefined;
    if (!previous) return;
    previous.cancel?.();
    previous.audio.pause();
    previous.audio.removeAttribute("src");
    previous.audio.load();
    previous.audio.remove();
    previous.release?.();
    previous.finish();
}

export function playSelectionAudio(
    doc: Document,
    word: string,
    accent: string,
    url: string | undefined,
    button: HTMLButtonElement,
    onStatus: (message?: string) => void = () => {},
) {
    if (active?.button === button) {
        stopSelectionAudio();
        return;
    }
    stopSelectionAudio();
    // Reader uses a content browser. Keep network/media loading in Zotero's
    // privileged window, independent of the Reader document's media policy.
    const owner = Zotero.getMainWindow() as unknown as {
        document: Document;
        Blob: typeof Blob;
        URL: typeof URL;
    };
    const audio = owner.document.createElementNS(
        "http://www.w3.org/1999/xhtml",
        "audio",
    ) as HTMLAudioElement;
    audio.hidden = true;
    const label = (button.dataset.audioLabel ||= button.title);
    const state = (value: string, title: string) => {
        button.dataset.audioState = value;
        button.title = title;
        button.setAttribute("aria-label", title);
        button.setAttribute(
            "aria-pressed",
            String(value === "loading" || value === "playing"),
        );
        button.setAttribute("aria-busy", String(value === "loading"));
    };
    const closed = () => {
        if (active === playback) stopSelectionAudio();
    };
    const playback: Playback = {
        button,
        audio,
        finish: () => {
            doc.defaultView?.removeEventListener("pagehide", closed);
            state("idle", label);
            onStatus();
        },
    };
    active = playback;
    let discardAudio: (() => void) | undefined;
    doc.defaultView?.addEventListener("pagehide", closed);
    const failed = (stage: "load" | "play", error?: unknown) => {
        if (active !== playback) return;
        if (audio.error?.code === 3 || audio.error?.code === 4)
            discardAudio?.();
        recordDiagnostic("selection_audio_failed", {
            stage,
            statusCode: audio.error?.code,
        });
        stopSelectionAudio();
        const message = getString(
            stage === "load"
                ? "selection-audio-load-failed"
                : (error as { name?: string })?.name === "NotAllowedError"
                  ? "selection-audio-blocked"
                  : "selection-audio-play-failed",
        );
        state("error", message);
        onStatus(message);
    };
    state("loading", getString("selection-audio-loading"));
    onStatus(getString("selection-audio-loading"));
    audio.addEventListener("ended", closed);
    audio.addEventListener("error", () => failed("play"));
    void (async () => {
        let stage: "load" | "play" = "load";
        try {
            const source = acquireSound(audioURL(word, accent, url));
            playback.cancel = source.release;
            discardAudio = source.discard;
            const { bytes, type } = await source.promise;
            if (active !== playback) return;
            const objectURL = owner.URL.createObjectURL(
                new owner.Blob([bytes], { type }),
            );
            playback.release = () => owner.URL.revokeObjectURL(objectURL);
            stage = "play";
            audio.src = objectURL;
            owner.document.documentElement.append(audio);
            await audio.play();
            if (active !== playback) return;
            state("playing", getString("selection-audio-stop"));
            onStatus(getString("selection-audio-stop"));
        } catch (error) {
            failed(stage, error);
        }
    })();
}
