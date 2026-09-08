import { config, version } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import {
    emptyLLMApi,
    profileLabel,
    profileName,
    SERVICE_NAMES,
    type LLMApiData,
} from "./llmApiManager";
import {
    loadProfiles,
    saveProfiles,
    getSelectedProfile,
    removeProfile,
} from "./profileStore";
import { testProfile, fetchProfileModels } from "./profileApiClient";
import type { ServerHealthResponse } from "./pdf2zhTypes";
import axios from "axios";

// Chrome preference windows use XUL popups. HTML select popups can become
// accessible without being painted by Zotero's native settings window.
const xulNS = "http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul";
function fillMenu(menu: XULMenuListElement, items: [string, string][]) {
    const popup = menu.querySelector("menupopup")!;
    popup.replaceChildren();
    for (const [label, value] of items) {
        const item = menu.ownerDocument.createElementNS(xulNS, "menuitem");
        item.setAttribute("label", label);
        item.setAttribute("value", value);
        popup.append(item);
    }
}
let managerWindow: Window | undefined;
function onDialogClosed(win: Window, url: string, callback: () => void) {
    const onUnload = (event: Event) => {
        // openDialog first unloads about:blank while loading the real dialog.
        // That navigation must not resolve edit() or discard the manager.
        if ((event.target as Document | null)?.documentURI !== url) return;
        win.removeEventListener("unload", onUnload);
        callback();
    };
    win.addEventListener("unload", onUnload);
}

function refreshProfileManager() {
    if (managerWindow && !managerWindow.closed) {
        const event = managerWindow.document.createEvent("Event");
        event.initEvent("profiles-changed", false, false);
        managerWindow.dispatchEvent(event);
    }
}

function element(id: string) {
    return addon.data.prefs?.window?.document.getElementById(
        `zotero-prefpane-${config.addonRef}-${id}`,
    );
}
function status(id: string, text: string) {
    const node = element(id);
    if (node) {
        node.textContent = text;
        node.hidden = !text;
    }
}
function report(error: unknown) {
    status("apiResult", error instanceof Error ? error.message : "操作失败");
}

export async function registerPrefsScripts(window: Window) {
    addon.data.prefs = { window, columns: [], rows: [] };
    for (const field of ["sourceLang", "targetLang"]) {
        const select = element(
            `${field}Select`,
        ) as unknown as XULMenuListElement;
        fillMenu(select, Object.entries(lang_map));
        select.value =
            getPref(field)?.toString() ||
            (field === "sourceLang" ? "en" : "zh-CN");
        select.addEventListener("command", () => {
            setPref(field, select.value);
            (element(field) as HTMLInputElement).value = select.value;
        });
        element(field)?.addEventListener("change", () => {
            select.value = (element(field) as HTMLInputElement).value;
        });
    }
    for (const [field, fallback] of [
        ["outputMono", "outputDual"],
        ["outputDual", "outputMono"],
    ]) {
        element(field)?.addEventListener("command", () => {
            if (
                !(element(field) as unknown as XUL.Checkbox).checked &&
                !(element(fallback) as unknown as XUL.Checkbox).checked
            ) {
                (element(fallback) as unknown as XUL.Checkbox).checked = true;
                setPref(fallback, true);
            }
        });
    }
    element("selectedApiKey")?.addEventListener("command", () => {
        const menu = element("selectedApiKey") as unknown as XULMenuListElement;
        const selected = menu.value;
        setPref("selectedApiKey", selected);
        // Native macOS menu commands run while the popup is hiding. Reflowing
        // the pane here can strand it in that state and block future clicks.
        window.setTimeout(() => {
            if (!menu.isConnected || getPref("selectedApiKey") !== selected)
                return;
            status("apiResult", "");
            refreshProfiles(false);
        }, 0);
    });
    element("profile-add")?.addEventListener("click", () => {
        void openProfileEditor().catch(report);
    });
    element("profile-edit")?.addEventListener("click", () => {
        void openProfileEditor(getPref("selectedApiKey")?.toString()).catch(
            report,
        );
    });
    element("profile-manage")?.addEventListener("click", openProfileManager);
    element("profile-test")?.addEventListener("click", async () => {
        const button = element("profile-test") as HTMLButtonElement;
        let api: LLMApiData | null;
        try {
            api = getSelectedProfile();
        } catch (error) {
            report(error);
            return;
        }
        if (!api) return;
        button.disabled = true;
        status("apiResult", "正在发送短翻译请求…");
        try {
            const message = await testProfile(api);
            const profiles = loadProfiles();
            const saved = profiles.find((entry) => entry.key === api.key);
            if (saved && JSON.stringify(saved) === JSON.stringify(api)) {
                saved.needsTest = false;
                saveProfiles(profiles);
                if (getPref("selectedApiKey") === api.key)
                    status("apiResult", message);
            }
        } catch (error) {
            if (getPref("selectedApiKey") === api.key) report(error);
        } finally {
            refreshProfiles();
        }
    });
    element("checkConnection")?.addEventListener("click", () => {
        void refreshServerVersion();
    });
    element("new_serverip")?.addEventListener("change", () => {
        void refreshServerVersion();
    });
    status("pluginVersion", version);
    try {
        refreshProfiles();
    } catch (error) {
        report(error);
    }
    void refreshServerVersion();
}

function refreshProfiles(rebuildMenu = true) {
    refreshProfileManager();
    const profiles = loadProfiles();
    const select = element(
        "selectedApiKey",
    ) as unknown as XULMenuListElement | null;
    if (!select) return;
    const selected = getPref("selectedApiKey")?.toString() || "";
    if (rebuildMenu)
        fillMenu(select, [
            ["请选择配置", ""],
            ...profiles.map((api): [string, string] => [
                profileLabel(api),
                api.key,
            ]),
        ]);
    const api = profiles.find((entry) => entry.key === selected);
    select.value = api?.key || "";
    for (const id of ["profile-edit", "profile-test"])
        (element(id) as HTMLButtonElement).disabled = !api;
    status(
        "profileSummary",
        api
            ? `${api.apiUrl || SERVICE_NAMES[api.service] || api.service}${api.needsTest ? " · 待测试" : ""}`
            : "选择一份配置即可使用，也可以新增中转站。",
    );
}

async function openProfileEditor(key?: string, copy = false): Promise<void> {
    const original = key
        ? loadProfiles().find((api) => api.key === key)
        : undefined;
    if (key && !original) throw new Error("配置已删除，请重新选择。");
    const data = JSON.parse(
        JSON.stringify(original || emptyLLMApi),
    ) as LLMApiData;
    if (copy) {
        data.key = "";
        data.name = `${profileName(data)} 副本`;
    }
    return new Promise((resolve) => {
        const args = {
            data,
            isEdit: !!original && !copy,
            services: SERVICE_NAMES,
            test: testProfile,
            listModels: fetchProfileModels,
            save: (value: LLMApiData, use: boolean) => {
                const profiles = loadProfiles();
                const api = {
                    ...value,
                    key: data.key || Zotero.Utilities.generateObjectKey(),
                };
                const index = profiles.findIndex(
                    (entry) => entry.key === api.key,
                );
                if (data.key && index < 0)
                    throw new Error("此配置已被删除，请取消后重新新增。");
                if (index >= 0) profiles[index] = api;
                else profiles.push(api);
                saveProfiles(profiles);
                if (use && (!original || copy))
                    setPref("selectedApiKey", api.key);
                status("apiResult", "");
                refreshProfiles();
            },
        };
        const url = `chrome://${config.addonRef}/content/llmApiEditor.xhtml`;
        const win = Zotero.getMainWindow().openDialog(
            url,
            "",
            "chrome,centerscreen,resizable,dialog=no,width=640,height=710",
            args,
        );
        if (!win) {
            resolve();
            return;
        }
        onDialogClosed(win, url, () => resolve());
    });
}

function openProfileManager() {
    if (managerWindow && !managerWindow.closed) {
        managerWindow.focus();
        return;
    }
    const url = `chrome://${config.addonRef}/content/llmApiManager.xhtml`;
    const win = Zotero.getMainWindow().openDialog(
        url,
        "",
        "chrome,centerscreen,resizable,dialog=no,width=800,height=500",
        {
            list: () =>
                loadProfiles().map((api) => ({
                    key: api.key,
                    label: profileLabel(api),
                    apiUrl: api.apiUrl,
                    current: getPref("selectedApiKey") === api.key,
                })),
            edit: openProfileEditor,
            remove: (key: string) => {
                removeProfile(key);
                status("apiResult", "");
                refreshProfiles();
            },
            top: (key: string) => {
                const profiles = loadProfiles();
                const api = profiles.find((entry) => entry.key === key);
                if (api)
                    saveProfiles([
                        api,
                        ...profiles.filter((entry) => entry.key !== key),
                    ]);
                refreshProfiles();
            },
        },
    );
    if (win) {
        managerWindow = win;
        onDialogClosed(win, url, () => {
            if (managerWindow === win) managerWindow = undefined;
        });
    }
}

async function refreshServerVersion() {
    const url =
        getPref("new_serverip")?.toString().trim().replace(/\/+$/, "") || "";
    const button = element("checkConnection") as HTMLButtonElement | null;
    if (button) button.disabled = true;
    status("serverStatus", "正在检查本地服务…");
    try {
        if (!url) throw new Error();
        const { data } = await axios.get<ServerHealthResponse>(
            `${url}/health`,
            { timeout: 5000 },
        );
        if (!["ok", "degraded"].includes(data.status || "")) throw new Error();
        status("serverVersion", data.version || "未知");
        status(
            "serverStatus",
            data.status === "degraded"
                ? "本地服务已连接，有警告"
                : "本地服务已连接",
        );
        status(
            "connectionResult",
            data.status === "degraded"
                ? "本地服务已连接，请检查工作目录是否可写及磁盘空间。"
                : "本地服务已连接。API 可用性请使用顶部的「测试 API」。",
        );
        element("serverVersionCard")?.setAttribute("data-state", "ok");
    } catch {
        status("serverVersion", "—");
        status("serverStatus", "本地服务无法连接");
        status(
            "connectionResult",
            "请确认 Python 服务已启动，并检查本地服务地址。",
        );
        element("serverVersionCard")?.setAttribute("data-state", "error");
    } finally {
        if (button) button.disabled = false;
    }
}

const lang_map = {
    English: "en",
    "Simplified Chinese": "zh-CN",
    "Traditional Chinese - Hong Kong": "zh-HK",
    "Traditional Chinese - Taiwan": "zh-TW",
    Japanese: "ja",
    Korean: "ko",
    Polish: "pl",
    Russian: "ru",
    Spanish: "es",
    Portuguese: "pt",
    "Brazilian Portuguese": "pt-BR",
    French: "fr",
    Malay: "ms",
    Indonesian: "id",
    Turkmen: "tk",
    "Filipino (Tagalog)": "tl",
    Vietnamese: "vi",
    "Kazakh (Latin)": "kk",
    German: "de",
    Dutch: "nl",
    Irish: "ga",
    Italian: "it",
    Greek: "el",
    Swedish: "sv",
    Danish: "da",
    Norwegian: "no",
    Icelandic: "is",
    Finnish: "fi",
    Ukrainian: "uk",
    Czech: "cs",
    Romanian: "ro",
    Hungarian: "hu",
    Slovak: "sk",
    Croatian: "hr",
    Estonian: "et",
    Latvian: "lv",
    Lithuanian: "lt",
    Belarusian: "be",
    Macedonian: "mk",
    Albanian: "sq",
    "Serbian (Cyrillic)": "sr",
    Slovenian: "sl",
    Catalan: "ca",
    Bulgarian: "bg",
    Maltese: "mt",
    Swahili: "sw",
    Amharic: "am",
    Oromo: "om",
    Tigrinya: "ti",
    "Haitian Creole": "ht",
    Latin: "la",
    Lao: "lo",
    Malayalam: "ml",
    Gujarati: "gu",
    Thai: "th",
    Burmese: "my",
    Tamil: "ta",
    Telugu: "te",
    Oriya: "or",
    Armenian: "hy",
    "Mongolian (Cyrillic)": "mn",
    Georgian: "ka",
    Khmer: "km",
    Bosnian: "bs",
    Luxembourgish: "lb",
    Romansh: "rm",
    Turkish: "tr",
    Sinhala: "si",
    Uzbek: "uz",
    Kyrgyz: "ky",
    Tajik: "tg",
    Abkhazian: "ab",
    Afar: "aa",
    Afrikaans: "af",
    Akan: "ak",
    Aragonese: "an",
    Avaric: "av",
    Ewe: "ee",
    Aymara: "ay",
    Ojibwa: "oj",
    Occitan: "oc",
    Ossetian: "os",
    Pali: "pi",
    Bashkir: "ba",
    Basque: "eu",
    Breton: "br",
    Chamorro: "ch",
    Chechen: "ce",
    Chuvash: "cv",
    Tswana: "tn",
    "Ndebele, South": "nr",
    Ndonga: "ng",
    Faroese: "fo",
    Fijian: "fj",
    "Frisian, Western": "fy",
    Ganda: "lg",
    Kongo: "kg",
    Kalaallisut: "kl",
    "Church Slavic": "cu",
    Guarani: "gn",
    Interlingua: "ia",
    Herero: "hz",
    Kikuyu: "ki",
    Rundi: "rn",
    Kinyarwanda: "rw",
    Galician: "gl",
    Kanuri: "kr",
    Cornish: "kw",
    Komi: "kv",
    Xhosa: "xh",
    Corsican: "co",
    Cree: "cr",
    Quechua: "qu",
    "Kurdish (Latin)": "ku",
    Kuanyama: "kj",
    Limburgan: "li",
    Lingala: "ln",
    Manx: "gv",
    Malagasy: "mg",
    Marshallese: "mh",
    Maori: "mi",
    Navajo: "nv",
    Nauru: "na",
    Nyanja: "ny",
    "Norwegian Nynorsk": "nn",
    Sardinian: "sc",
    "Northern Sami": "se",
    Samoan: "sm",
    Sango: "sg",
    Shona: "sn",
    Esperanto: "eo",
    "Scottish Gaelic": "gd",
    Somali: "so",
    "Southern Sotho": "st",
    Tatar: "tt",
    Tahitian: "ty",
    Tongan: "to",
    Twi: "tw",
    Walloon: "wa",
    Welsh: "cy",
    Venda: "ve",
    Volapük: "vo",
    Interlingue: "ie",
    "Hiri Motu": "ho",
    Igbo: "ig",
    Ido: "io",
    Inuktitut: "iu",
    Inupiaq: "ik",
    "Sichuan Yi": "ii",
    Yoruba: "yo",
    Zhuang: "za",
    Tsonga: "ts",
    Zulu: "zu",
};
