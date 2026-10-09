import { getPref } from "../utils/prefs";
import { lookupDictionary } from "./selectionDictionary";
import {
    createOnlineDictionaryRequest,
    type DictionaryResult,
} from "./selectionOnlineDictionary";

export function createDictionaryRequest() {
    const online = createOnlineDictionaryRequest();
    let cancelled = false;
    return {
        abort() {
            cancelled = true;
            online.abort();
        },
        async lookup(
            text: string,
            from: string,
            to: string,
            refresh = false,
            override?: string,
        ): Promise<DictionaryResult> {
            const source =
                override || String(getPref("selectionDictionary") || "ecdict");
            if (
                !/^en(?:-|$)/i.test(from) ||
                !/^(zh|zh-cn|zh-hans)$/i.test(to.replace(/_/g, "-"))
            )
                return { status: "miss", source, cached: false };
            if (source === "youdao" || source === "bing")
                return online.lookup(source, text, refresh);
            try {
                const entry = await lookupDictionary(text, from, to, source);
                if (cancelled)
                    return { status: "error", source, message: "查询已取消。" };
                if (entry)
                    return { status: "hit", source, cached: false, entry };
            } catch {
                return {
                    status: "error",
                    source,
                    message: "离线词典读取失败，请重新安装或导入词库。",
                };
            }
            const fallback =
                getPref("selectionDictionaryFallback") === "bing"
                    ? "bing"
                    : "youdao";
            return online.lookup(fallback, text, refresh);
        },
    };
}
