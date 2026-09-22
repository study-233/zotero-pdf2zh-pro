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
import {
    clearGlossaryEntries,
    importGlossaryCsv,
    loadGlossaryEntries,
} from "./glossaryStore";
import {
    listGlossaryPacks,
    checkGlossaryUpdates,
    downloadGlossaryPack,
    cancelGlossaryDownload,
    removeGlossaryPack,
    getSelectedGlossaryPackIds,
    setSelectedGlossaryPackIds,
    supportsGlossaryPackLanguage,
    type GlossaryPackInfo,
} from "./glossaryPacks";

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
let serverCheckSequence = 0;
let glossaryPackSequence = 0;
let glossaryPackWindow: Window | undefined;
let glossaryPackPoll: number | undefined;
let glossaryPackServerURL = "";
let glossaryPacksSupported = false;
let glossaryPackList: GlossaryPackInfo[] = [];
const glossaryPackBusy = new Set<string>();
const glossaryPackIds = [
    "computing",
    "building",
    "physics",
    "environment",
    "medicine",
];
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
        node.removeAttribute("data-l10n-id");
        node.removeAttribute("data-l10n-args");
        node.textContent = text;
        node.hidden = !text;
    }
}
function report(error: unknown) {
    status("apiResult", error instanceof Error ? error.message : "操作失败");
}

export async function registerPrefsScripts(window: Window) {
    stopGlossaryPackPolling();
    glossaryPackWindow = window;
    addon.data.prefs = { window, columns: [], rows: [] };
    window.addEventListener(
        "unload",
        () => {
            if (glossaryPackWindow !== window) return;
            stopGlossaryPackPolling();
            glossaryPackSequence++;
            serverCheckSequence++;
            glossaryPacksSupported = false;
            glossaryPackWindow = undefined;
        },
        { once: true },
    );
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
            renderGlossaryPacks();
        });
        element(field)?.addEventListener("change", () => {
            select.value = (element(field) as HTMLInputElement).value;
            renderGlossaryPacks();
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
    element("glossary-import")?.addEventListener("click", () => {
        void importGlossary();
    });
    element("glossary-clear")?.addEventListener("click", () => {
        clearGlossaryEntries();
        refreshGlossarySummary();
        localizedStatus("glossaryResult", "pref-glossary-cleared");
    });
    element("glossary-check-updates")?.addEventListener("click", () => {
        void refreshGlossaryPacks(true);
    });
    refreshGlossarySummary();
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

function stopGlossaryPackPolling() {
    if (glossaryPackPoll !== undefined)
        glossaryPackWindow?.clearTimeout(glossaryPackPoll);
    glossaryPackPoll = undefined;
}

function localizePackNode(
    node: Element,
    key: string,
    args?: Record<string, string | number>,
) {
    node.setAttribute("data-l10n-id", key);
    if (args) node.setAttribute("data-l10n-args", JSON.stringify(args));
    else node.removeAttribute("data-l10n-args");
}

function localizedStatus(
    id: string,
    key: string,
    args?: Record<string, string | number>,
) {
    const node = element(id);
    if (!node) return;
    node.hidden = false;
    node.textContent = "";
    localizePackNode(node, key, args);
}

function packStatus(key: string, args?: Record<string, string | number>) {
    localizedStatus("glossaryPacksResult", key, args);
}

async function preferenceText(key: string) {
    return (
        (await addon.data.prefs?.window.document.l10n?.formatValue(key)) || key
    );
}

function renderGlossaryPacks() {
    const list = element("glossaryPacks");
    const sources = element("glossaryPackSources");
    if (!list || !sources) return;
    const document = list.ownerDocument;
    const activeId = document.activeElement?.id;
    let selected: string[] = [];
    let selectionsReadable = true;
    try {
        selected = getSelectedGlossaryPackIds(glossaryPackServerURL);
    } catch {
        selectionsReadable = false;
    }
    const languageSupported = supportsGlossaryPackLanguage(
        getPref("sourceLang")?.toString() || "en",
        getPref("targetLang")?.toString() || "zh-CN",
    );
    const languageNotice = element("glossaryPackLanguage");
    if (languageNotice) languageNotice.hidden = languageSupported;
    const make = (tag: string, className = "") => {
        const node = document.createElementNS(
            "http://www.w3.org/1999/xhtml",
            tag,
        ) as HTMLElement;
        node.className = className;
        return node;
    };
    list.replaceChildren();
    sources.replaceChildren();
    for (const id of glossaryPackIds) {
        const pack = glossaryPackList.find((entry) => entry.id === id);
        const installed = (pack?.installedVersions.length || 0) > 0;
        const downloading = pack?.status === "downloading";
        const busy = glossaryPackBusy.has(id);
        const row = make("div", "glossary-pack-row");
        const choice = make("div", "toggle-row");
        const checkbox = make("input") as HTMLInputElement;
        checkbox.type = "checkbox";
        checkbox.id = `zotero-prefpane-${config.addonRef}-pack-${id}`;
        checkbox.checked = selected.includes(id);
        checkbox.disabled =
            !selectionsReadable ||
            (!checkbox.checked &&
                (!glossaryPacksSupported ||
                    !languageSupported ||
                    busy ||
                    !installed));
        checkbox.addEventListener("change", () => {
            try {
                const current = getSelectedGlossaryPackIds(
                    glossaryPackServerURL,
                );
                setSelectedGlossaryPackIds(
                    glossaryPackServerURL,
                    checkbox.checked
                        ? [...new Set([...current, id])]
                        : current.filter((entry) => entry !== id),
                );
                renderGlossaryPacks();
            } catch (error) {
                status(
                    "glossaryPacksResult",
                    error instanceof Error ? error.message : "",
                );
                renderGlossaryPacks();
            }
        });
        const label = make("label");
        label.setAttribute("for", checkbox.id);
        localizePackNode(label, `pref-pack-${id}`);
        choice.append(checkbox, label);
        const state = make("span", "glossary-pack-state");
        const stateLabel = make("span");
        const stateKey = downloading
            ? "downloading"
            : pack?.status === "failed"
              ? "failed"
              : pack?.status === "update_available"
                ? "update"
                : installed
                  ? "installed"
                  : "not-downloaded";
        localizePackNode(stateLabel, `pref-pack-state-${stateKey}`);
        if (downloading && pack?.download?.totalBytes) {
            localizePackNode(stateLabel, "pref-pack-state-progress", {
                percent: Math.min(
                    100,
                    Math.floor(
                        ((pack.download.receivedBytes || 0) /
                            pack.download.totalBytes) *
                            100,
                    ),
                ),
            });
        }
        state.append(stateLabel);
        if (pack?.entryCount) {
            const count = make("span");
            localizePackNode(count, "pref-pack-count", {
                count: installed
                    ? pack.installedVersions[0].entryCount || pack.entryCount
                    : pack.entryCount,
            });
            state.append(document.createTextNode(" · "), count);
        }
        const actions = make("div", "glossary-pack-actions");
        const addAction = (
            action: "download" | "cancel" | "remove",
            labelKey: string,
        ) => {
            const button = make("button") as HTMLButtonElement;
            button.id = `zotero-prefpane-${config.addonRef}-pack-${id}-${action}`;
            button.type = "button";
            button.disabled =
                !glossaryPacksSupported ||
                busy ||
                (action === "download" && !pack?.version);
            localizePackNode(button, labelKey);
            button.addEventListener("click", () => {
                void runGlossaryPackAction(id, action);
            });
            actions.append(button);
        };
        if (downloading) addAction("cancel", "pref-pack-cancel");
        else {
            if (
                !installed ||
                pack?.status === "update_available" ||
                pack?.status === "failed"
            )
                addAction(
                    "download",
                    installed ? "pref-pack-update" : "pref-pack-download",
                );
            if (installed) addAction("remove", "pref-pack-remove");
        }
        row.append(choice, state, actions);
        if (pack?.download?.error) {
            const error = make("p", "pref-description glossary-pack-error");
            error.textContent = pack.download.error;
            row.append(error);
        }
        list.append(row);
        if (pack) {
            const details = make("div", "glossary-pack-source");
            const title = make("strong");
            localizePackNode(title, `pref-pack-${id}`);
            const metadata = make("p", "pref-description");
            localizePackNode(metadata, "pref-pack-metadata", {
                version: pack.version || "—",
                count: pack.entryCount || 0,
                size: Math.ceil((pack.sizeBytes || 0) / 1024),
            });
            details.append(title, metadata);
            for (const source of pack.sources || []) {
                const line = make("p", "pref-description");
                for (const [label, address] of [
                    [source.name, source.url],
                    [source.license, source.licenseUrl],
                ]) {
                    if (line.childNodes.length)
                        line.append(document.createTextNode(" · "));
                    const link = make("a") as HTMLAnchorElement;
                    link.textContent = label;
                    try {
                        const url = new URL(address);
                        if (!["https:", "http:"].includes(url.protocol))
                            throw new Error();
                        link.href = url.href;
                        link.addEventListener("click", (event) => {
                            event.preventDefault();
                            Zotero.launchURL(link.href);
                        });
                        line.append(link);
                    } catch {
                        line.append(document.createTextNode(label));
                    }
                }
                details.append(line);
            }
            sources.append(details);
        }
    }
    const check = element("glossary-check-updates") as HTMLButtonElement | null;
    if (check) check.disabled = !glossaryPacksSupported;
    if (activeId)
        (document.getElementById(activeId) as HTMLElement | null)?.focus();
}

async function refreshGlossaryPacks(checkUpdates = false) {
    if (!glossaryPacksSupported) return;
    stopGlossaryPackPolling();
    const sequence = ++glossaryPackSequence;
    const serverURL = glossaryPackServerURL;
    if (checkUpdates) packStatus("pref-pack-checking");
    const button = element(
        "glossary-check-updates",
    ) as HTMLButtonElement | null;
    if (button) button.disabled = true;
    try {
        const result = await (
            checkUpdates ? checkGlossaryUpdates : listGlossaryPacks
        )(serverURL);
        if (
            sequence !== glossaryPackSequence ||
            serverURL !== glossaryPackServerURL
        )
            return;
        glossaryPackList = result.packs;
        status("glossaryPacksResult", "");
        renderGlossaryPacks();
        if (checkUpdates) packStatus("pref-pack-checked");
        if (glossaryPackList.some((pack) => pack.status === "downloading"))
            glossaryPackPoll = glossaryPackWindow?.setTimeout(() => {
                void refreshGlossaryPacks();
            }, 1000);
    } catch (error) {
        if (
            sequence !== glossaryPackSequence ||
            serverURL !== glossaryPackServerURL
        )
            return;
        if (checkUpdates) {
            if (error instanceof Error)
                status("glossaryPacksResult", error.message);
            else packStatus("pref-pack-check-failed");
        } else {
            glossaryPacksSupported = false;
            packStatus("pref-pack-unreachable");
        }
        renderGlossaryPacks();
    } finally {
        if (button && sequence === glossaryPackSequence)
            button.disabled = !glossaryPacksSupported;
    }
}

async function runGlossaryPackAction(
    id: string,
    action: "download" | "cancel" | "remove",
) {
    if (!glossaryPacksSupported || glossaryPackBusy.has(id)) return;
    const serverURL = glossaryPackServerURL;
    const serverSequence = serverCheckSequence;
    glossaryPackBusy.add(id);
    renderGlossaryPacks();
    try {
        if (action === "download") await downloadGlossaryPack(serverURL, id);
        else if (action === "cancel")
            await cancelGlossaryDownload(serverURL, id);
        else {
            await removeGlossaryPack(serverURL, id);
            setSelectedGlossaryPackIds(
                serverURL,
                getSelectedGlossaryPackIds(serverURL).filter(
                    (selected) => selected !== id,
                ),
            );
        }
        if (serverSequence !== serverCheckSequence) return;
        await refreshGlossaryPacks();
    } catch (error) {
        if (serverSequence === serverCheckSequence) {
            if (error instanceof Error)
                status("glossaryPacksResult", error.message);
            else packStatus("pref-pack-action-failed");
        }
    } finally {
        if (serverSequence === serverCheckSequence) {
            glossaryPackBusy.delete(id);
            renderGlossaryPacks();
        }
    }
}

function refreshGlossarySummary() {
    const clear = element("glossary-clear") as HTMLButtonElement | null;
    try {
        const entries = loadGlossaryEntries();
        localizedStatus(
            "glossarySummary",
            entries.length ? "pref-glossary-saved" : "pref-glossary-empty",
            { count: entries.length },
        );
        if (clear) clear.disabled = entries.length === 0;
    } catch (error) {
        status("glossarySummary", String(error));
        if (clear) clear.disabled = false;
    }
}

async function importGlossary(): Promise<void> {
    const button = element("glossary-import") as HTMLButtonElement | null;
    const clear = element("glossary-clear") as HTMLButtonElement | null;
    if (button) button.disabled = true;
    if (clear) clear.disabled = true;
    try {
        const path = await new ztoolkit.FilePicker(
            await preferenceText("pref-glossary-import"),
            "open",
            [["CSV", "*.csv"]],
        ).open();
        if (!path) return;
        const bytes = await IOUtils.read(path);
        let text: string;
        try {
            text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        } catch {
            throw new Error(
                await preferenceText("pref-glossary-invalid-encoding"),
            );
        }
        const entries = importGlossaryCsv(text);
        localizedStatus("glossaryResult", "pref-glossary-imported", {
            count: entries.length,
        });
    } catch (error) {
        if (error instanceof Error) status("glossaryResult", error.message);
        else localizedStatus("glossaryResult", "pref-glossary-import-failed");
    } finally {
        if (button) button.disabled = false;
        refreshGlossarySummary();
    }
}

async function refreshServerVersion() {
    const checkSequence = ++serverCheckSequence;
    const url =
        getPref("new_serverip")?.toString().trim().replace(/\/+$/, "") || "";
    stopGlossaryPackPolling();
    glossaryPackSequence++;
    glossaryPackServerURL = url;
    glossaryPacksSupported = false;
    glossaryPackList = [];
    glossaryPackBusy.clear();
    renderGlossaryPacks();
    packStatus("pref-pack-connecting");
    const button = element("checkConnection") as HTMLButtonElement | null;
    if (button) button.disabled = true;
    localizedStatus("serverStatus", "pref-server-checking");
    status("qualityCapabilities", "");
    try {
        if (!url) throw new Error();
        const { data } = await axios.get<ServerHealthResponse>(
            `${url}/health`,
            { timeout: 5000 },
        );
        if (checkSequence !== serverCheckSequence) return;
        if (!["ok", "degraded"].includes(data.status || "")) throw new Error();
        if (data.version) status("serverVersion", data.version);
        else localizedStatus("serverVersion", "pref-server-unknown");
        localizedStatus(
            "serverStatus",
            data.status === "degraded"
                ? "pref-server-degraded"
                : "pref-server-connected",
        );
        if (data.status === "degraded")
            localizedStatus("connectionResult", "pref-server-check-storage");
        else status("connectionResult", "");
        element("serverVersionCard")?.setAttribute("data-state", "ok");
        if (
            data.capabilities?.glossaryEntries !== true ||
            data.capabilities?.semanticReview !== true
        )
            localizedStatus("qualityCapabilities", "pref-quality-unavailable");
        else status("qualityCapabilities", "");
        if (data.capabilities?.glossaryPacks === true) {
            glossaryPacksSupported = true;
            await refreshGlossaryPacks();
        } else {
            packStatus("pref-pack-upgrade-server");
        }
    } catch {
        if (checkSequence !== serverCheckSequence) return;
        status("serverVersion", "—");
        localizedStatus("serverStatus", "pref-server-unreachable");
        localizedStatus("connectionResult", "pref-server-start");
        element("serverVersionCard")?.setAttribute("data-state", "error");
        status("qualityCapabilities", "");
        packStatus("pref-pack-unreachable");
    } finally {
        if (button && checkSequence === serverCheckSequence)
            button.disabled = false;
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
