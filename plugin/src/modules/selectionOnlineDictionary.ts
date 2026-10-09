import { config } from "../../package.json";
import type { DictionaryEntry } from "./selectionDictionaryStore";
import { recordDiagnostic } from "./diagnostics";

export type OnlineDictionary = "youdao" | "bing";
export type DictionaryDefinition = Partial<DictionaryEntry> & {
    text: string;
    origin: string;
    notice?: string;
};
export type DictionaryResult =
    | {
          status: "hit";
          source: string;
          cached: boolean;
          entry: DictionaryDefinition;
      }
    | { status: "miss"; source: string; cached: boolean }
    | { status: "error"; source: string; message: string };
export const dictionaryNames: Record<string, string> = {
    ecdict: "ECDICT 离线",
    collins: "柯林斯离线",
    youdao: "有道词典",
    bing: "必应词典",
};
const object = (v: unknown): Record<string, unknown> =>
    v && typeof v === "object" && !Array.isArray(v)
        ? (v as Record<string, unknown>)
        : {};
const array = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const plain = (v: unknown): string =>
    typeof v === "string"
        ? v
              .replace(/<[^>]*>/g, "")
              .replace(/&nbsp;/g, " ")
              .replace(/&amp;/g, "&")
              .trim()
              .slice(0, 4000)
        : "";

export function safeDictionaryAudio(value: unknown): string | undefined {
    if (typeof value !== "string") return;
    try {
        const url = new URL(value);
        if (
            url.protocol === "https:" &&
            !url.username &&
            !url.password &&
            /(^|\.)(youdao\.com|bing\.com|baidu\.com)$/.test(url.hostname)
        )
            return url.href;
    } catch {
        /* An absent pronunciation is not a failed lookup. */
    }
}

export function parseYoudao(
    raw: unknown,
    selected: string,
): DictionaryDefinition | undefined {
    const data = object(raw);
    if (
        !Object.keys(data).length ||
        (data.errorCode && data.errorCode !== "0") ||
        data.error ||
        (data.code && data.code !== 200 && data.code !== "200")
    )
        throw new Error("有道词典返回格式异常，请重试。");
    const ec = object(data.ec);
    const word = object(ec.word);
    // A successful web dictionary response identifies itself even for a miss.
    if (!Object.keys(word).length) {
        if (
            (typeof data.input === "string" ||
                typeof data.query === "string") &&
            (data.le ||
                data.meta ||
                data.suggest ||
                data.simple ||
                data.web_trans)
        )
            return;
        throw new Error("有道词典返回格式已变化，请重试。");
    }
    const senses = array(word.trs)
        .map(object)
        .map((row) => ({
            pos: plain(row.pos),
            chinese: plain(row.tran),
            examples: [],
        }))
        .filter((row) => row.chinese);
    if (!senses.length) throw new Error("有道词条缺少释义，请重试。");
    const headword = plain(word["return-phrase"]) || selected;
    const prototype = plain(word.prototype);
    return {
        headword,
        senses,
        text: senses
            .map((s) => [s.pos, s.chinese].filter(Boolean).join(" "))
            .join("\n"),
        origin: "有道在线词典",
        pronunciations:
            word.ukphone || word.usphone
                ? [
                      ...(word.ukphone
                          ? [
                                {
                                    accent: "英",
                                    phonetic: plain(word.ukphone),
                                    audioUrl: `https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(headword)}&type=1`,
                                },
                            ]
                          : []),
                      ...(word.usphone
                          ? [
                                {
                                    accent: "美",
                                    phonetic: plain(word.usphone),
                                    audioUrl: `https://dict.youdao.com/dictvoice?audio=${encodeURIComponent(headword)}&type=2`,
                                },
                            ]
                          : []),
                  ]
                : word.phone
                  ? [{ accent: "发音", phonetic: plain(word.phone) }]
                  : [],
        forms: [
            ...(prototype && prototype !== headword
                ? [{ label: "原形", word: prototype }]
                : []),
            ...array(word.wfs)
                .map((v) => object(object(v).wf))
                .map((w) => ({ label: plain(w.name), word: plain(w.value) }))
                .filter((w) => w.word),
        ],
        examples: array(object(data.blng_sents_part)["sentence-pair"])
            .slice(0, 6)
            .map(object)
            .map((s) => ({
                english: plain(s.sentence || s["sentence-eng"]),
                chinese: plain(s["sentence-translation"]),
            }))
            .filter((s) => s.english && s.chinese),
    };
}

export function parseBing(
    html: string,
    selected: string,
    parser: DOMParser,
): DictionaryDefinition | undefined {
    const doc = parser.parseFromString(
        html,
        "text/html",
    ) as unknown as Document;
    const nodes = (selector: string) =>
        Array.from(doc.querySelectorAll(selector)) as unknown as HTMLElement[];
    if (
        doc.querySelector("#b_captcha, .b_captcha, #challenge-form") ||
        /verify you are human|unusual traffic|验证码/i.test(doc.title)
    )
        throw new Error("必应词典需要验证，请稍后重试或切换词典。");
    const senses = nodes(".qdef > ul > li")
        .map((li) => ({
            pos: plain(li.querySelector(".pos")?.textContent),
            chinese: plain(li.querySelector(".def")?.textContent),
            examples: [],
        }))
        .filter((s) => s.chinese);
    if (!senses.length) {
        if (doc.querySelector(".no_results, .no-result")) return;
        throw new Error("必应词典页面无法解析，请重试或切换词典。");
    }
    const pronunciations = nodes(".hd_prUS, .hd_pr").map((node) => {
        const label = plain(node.textContent);
        const accent = /美|US/i.test(label) ? "美" : "英";
        const link = doc.querySelector(
            accent === "美" ? "#bigaud_us" : "#bigaud_uk",
        );
        const raw =
            link?.getAttribute("data-mp3link") ||
            node.querySelector("a")?.getAttribute("onclick") ||
            "";
        const audioUrl = safeDictionaryAudio(
            raw.startsWith("/dict/")
                ? `https://www.bing.com${raw}`
                : raw.match(/https:\/\/[^'"\s]+/)?.[0],
        );
        return {
            accent: /美|US/i.test(label) ? "美" : "英",
            phonetic: label.replace(/^[^[]*\[|\].*$/g, ""),
            audioUrl,
        };
    });
    const prototype = senses
        .map(
            (sense) =>
                sense.chinese.match(
                    /[“"]([a-z]+)[”"]的(?:现在分词|过去式|过去分词|复数)/i,
                )?.[1],
        )
        .find(Boolean);
    return {
        headword:
            plain(doc.querySelector("#headword")?.textContent) || selected,
        senses,
        pronunciations,
        origin: "必应在线词典",
        text: senses
            .map((s) => [s.pos, s.chinese].filter(Boolean).join(" "))
            .join("\n"),
        forms: [
            ...(prototype ? [{ label: "原形", word: prototype }] : []),
            ...nodes(".hd_if").map((n) => ({
                label: "词形",
                word: plain(n.textContent),
            })),
        ],
        examples: nodes(".se_li")
            .slice(0, 6)
            .map((n) => ({
                english: plain(n.querySelector(".sen_en")?.textContent),
                chinese: plain(n.querySelector(".sen_cn")?.textContent),
            }))
            .filter((s) => s.english && s.chinese),
    };
}

type Cached = {
    time: number;
    accessed: number;
    result: Exclude<DictionaryResult, { status: "error" }>;
};
let cache = new Map<string, Cached>();
let loaded: Promise<void> | undefined;
let writes = Promise.resolve();
let epoch = 0;
const cachePath = () =>
    PathUtils.join(
        Zotero.DataDirectory.dir,
        config.addonRef,
        "dictionary-cache-v1.json",
    );
async function readCache() {
    if (!loaded)
        loaded = (async () => {
            try {
                const raw = JSON.parse(await IOUtils.readUTF8(cachePath()));
                if (raw?.version === 1 && Array.isArray(raw.entries))
                    for (const [key, row] of raw.entries.slice(-2000)) {
                        if (
                            typeof key === "string" &&
                            Number.isFinite(row?.time) &&
                            Number.isFinite(row?.accessed) &&
                            (row.result?.status === "miss" ||
                                (row.result?.status === "hit" &&
                                    typeof row.result.entry?.text === "string"))
                        )
                            cache.set(key, row);
                    }
            } catch {
                /* Cache is disposable; a network lookup remains available. */
            }
        })();
    await loaded;
}
function persistCache() {
    writes = writes
        .catch(() => {})
        .then(async () => {
            const path = cachePath();
            await IOUtils.makeDirectory(PathUtils.parent(path)!, {
                createAncestors: true,
                ignoreExisting: true,
            });
            await IOUtils.writeUTF8(
                path,
                JSON.stringify({ version: 1, entries: [...cache] }),
                { tmpPath: path + ".tmp" },
            );
        })
        .catch(() => {
            recordDiagnostic("dictionary_cache_write_failed");
        });
    return writes;
}
export async function clearOnlineDictionaryCache() {
    await readCache();
    epoch++;
    cache.clear();
    await persistCache();
}

export function createOnlineDictionaryRequest() {
    let cancelled = false;
    let cancel: (() => void) | undefined;
    return {
        abort() {
            cancelled = true;
            cancel?.();
        },
        async lookup(
            source: OnlineDictionary,
            text: string,
            refresh = false,
        ): Promise<DictionaryResult> {
            const started = Date.now();
            const word = text
                .normalize("NFKC")
                .trim()
                .replace(/\s+/g, " ")
                .toLowerCase();
            const key = JSON.stringify([1, source, word, "en", "zh-CN"]);
            const currentEpoch = epoch;
            try {
                await readCache();
                if (cancelled) throw new Error();
                const saved = cache.get(key);
                if (
                    !refresh &&
                    saved &&
                    Date.now() - saved.time <
                        (saved.result.status === "hit" ? 30 * 86400000 : 600000)
                ) {
                    saved.accessed = Date.now();
                    return { ...saved.result, cached: true };
                }
                const url =
                    source === "youdao"
                        ? "https://dict.youdao.com/jsonapi_s?doctype=json&jsonversion=4"
                        : `https://www.bing.com/dict/search?q=${encodeURIComponent(word)}&FORM=BDVSP6&cc=cn`;
                const response = await Zotero.HTTP.request(
                    source === "youdao" ? "POST" : "GET",
                    url,
                    {
                        responseType: "text",
                        timeout: 10000,
                        ...(source === "youdao"
                            ? {
                                  body: `q=${encodeURIComponent(word)}&le=en&t=3&client=web&keyfrom=webdict`,
                                  headers: {
                                      "Content-Type":
                                          "application/x-www-form-urlencoded",
                                  },
                              }
                            : {}),
                        cancellerReceiver: (fn: () => void) => {
                            cancel = fn;
                            if (cancelled) fn();
                        },
                    },
                );
                if (cancelled) throw new Error();
                const entry =
                    source === "youdao"
                        ? parseYoudao(JSON.parse(response.responseText), word)
                        : parseBing(
                              response.responseText,
                              word,
                              new (Zotero.getMainWindow().DOMParser)(),
                          );
                const result: Exclude<DictionaryResult, { status: "error" }> =
                    entry
                        ? { status: "hit", source, cached: false, entry }
                        : { status: "miss", source, cached: false };
                if (currentEpoch === epoch) {
                    cache.set(key, {
                        time: Date.now(),
                        accessed: Date.now(),
                        result,
                    });
                    if (cache.size > 2000) {
                        cache = new Map(
                            [...cache]
                                .sort((a, b) => b[1].accessed - a[1].accessed)
                                .slice(0, 2000),
                        );
                    }
                    void persistCache();
                }
                recordDiagnostic("dictionary_lookup_complete", {
                    latencyMs: Date.now() - started,
                    provider: source,
                });
                return result;
            } catch (error) {
                recordDiagnostic(
                    cancelled
                        ? "dictionary_lookup_cancelled"
                        : "dictionary_lookup_failed",
                    { provider: source, latencyMs: Date.now() - started },
                );
                return {
                    status: "error",
                    source,
                    message: cancelled
                        ? "查询已取消。"
                        : error instanceof Error &&
                            /词典|词条/.test(error.message)
                          ? error.message
                          : `${dictionaryNames[source]}暂不可用，请重试或改用离线词典。`,
                };
            }
        },
    };
}
