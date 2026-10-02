import { config } from "../../package.json";
import { getPref, setPref } from "../utils/prefs";
import { getString } from "../utils/locale";
import { importODHDictionary } from "./selectionDictionaryStore";
import {
    dictionaryDownloadState,
    subscribeDictionaryDownload,
    refreshDictionaryDownload,
    downloadDictionary,
    cancelDictionaryDownload,
    dictionaryChoiceChanged,
    dictionaryImported,
} from "./selectionDictionaryDownload";
import { createSelectionRequest } from "./selectionRequest";
import { PDF2zhHelperFactory } from "./pdf2zhHelper";
import { recordDiagnostic } from "./diagnostics";
import { loadProfiles } from "./profileStore";
import { profileLabel } from "./llmApiManager";

export function registerSelectionPreferences(
    window: Window,
    changed: () => void,
) {
    const node = (id: string) =>
        window.document.getElementById(
            `zotero-prefpane-${config.addonRef}-${id}`,
        )!;
    const service = node("selection-provider") as unknown as XULMenuListElement;
    const dictionary = node(
        "selection-dictionary",
    ) as unknown as XULMenuListElement;
    const button = node("selection-import") as HTMLButtonElement;
    const download = node("selection-download") as HTMLButtonElement;
    const status = node("selection-dictionary-status");
    const error = node("selection-download-error");
    const progress = node("selection-download-progress") as HTMLProgressElement;
    if (!service || !dictionary || !button || !status || !download) return;
    const model = node(
        "selection-model",
    ) as unknown as XULMenuListElement | null;
    let previousModel: string | undefined;
    const renderModels = () => {
        if (!model) return;
        const selected = String(getPref("selectionApiKey") || "");
        const popup = model.querySelector("menupopup")!;
        const hint = node("selection-model-status");
        const choices: [string, string][] = [
            [getString("selection-model-follow"), ""],
        ];
        let identity: string;
        let message = "";
        try {
            const profiles = loadProfiles();
            choices.push(
                ...profiles.map((api): [string, string] => [
                    profileLabel(api),
                    api.key,
                ]),
            );
            const effectiveKey =
                selected || String(getPref("selectedApiKey") || "");
            const effective = profiles.find((api) => api.key === effectiveKey);
            if (selected && !effective) {
                choices.push([getString("selection-model-deleted"), selected]);
                message = getString("selection-model-missing");
            }
            // Labels and test badges do not affect in-flight translations.
            const {
                name: _name,
                needsTest: _needsTest,
                ...settings
            } = effective || {};
            identity = JSON.stringify([selected, effectiveKey, settings]);
        } catch {
            if (selected)
                choices.push([getString("selection-model-deleted"), selected]);
            message = getString("selection-model-unreadable");
            identity = `unreadable:${selected}`;
        }
        popup.replaceChildren();
        for (const [label, value] of choices) {
            const item = window.document.createElementNS(
                "http://www.mozilla.org/keymaster/gatekeeper/there.is.only.xul",
                "menuitem",
            );
            item.setAttribute("label", label);
            item.setAttribute("value", value);
            popup.append(item);
        }
        model.value = selected;
        hint.textContent = message;
        hint.hidden = !message;
        if (previousModel !== undefined && previousModel !== identity)
            changed();
        previousModel = identity;
    };
    model?.addEventListener("command", () => {
        const selected = model.value;
        setPref("selectionApiKey", selected);
        changed();
        previousModel = undefined;
        // Let native menus finish closing before rebuilding their items.
        window.setTimeout(() => {
            if (model.isConnected && getPref("selectionApiKey") === selected)
                renderModels();
        }, 0);
    });
    window.addEventListener("profiles-changed", renderModels);
    window.addEventListener(
        "unload",
        () => window.removeEventListener("profiles-changed", renderModels),
        { once: true },
    );
    renderModels();
    const auto = node("selection-auto-dictionary") as HTMLInputElement | null;
    if (auto) {
        auto.checked = getPref("selectionAutoDictionary") !== false;
        auto.addEventListener("change", () => {
            setPref("selectionAutoDictionary", auto.checked);
            changed();
        });
    }
    const clear = node("selection-clear-cache") as HTMLButtonElement | null;
    clear?.addEventListener("click", () => {
        void (async () => {
            clear.disabled = true;
            changed(); // Cancel outstanding Reader actions before clearing.
            const status = node("selection-cache-status");
            try {
                const request = createSelectionRequest();
                const url = PDF2zhHelperFactory.getServerConfig(
                    false,
                ).serverUrl.replace(/\/$/, "");
                const capability = await request.post(
                    `${url}/selection-capabilities`,
                    {},
                    10000,
                );
                if (
                    !capability.ok ||
                    !(capability.data as { selectionLearning?: boolean })
                        ?.selectionLearning
                )
                    throw new Error();
                const result = await request.post(
                    `${url}/selection-cache/clear`,
                    {},
                    10000,
                );
                if (!result.ok) throw new Error();
                status.textContent = getString("selection-cache-cleared");
            } catch {
                status.textContent = getString("selection-cache-clear-failed");
            } finally {
                clear.disabled = false;
            }
        })();
    });
    const msg = (key: string, args?: Record<string, unknown>) =>
        getString(`dictionary-${key}`, { args });
    let importing = false;
    let importError = false;
    const render = () => {
        const state = dictionaryDownloadState();
        const { installed, available, phase } = state;
        const busy = ["checking", "downloading", "installing"].includes(phase);
        const update =
            installed?.version &&
            available &&
            installed.version !== available.version;
        dictionary.value =
            getPref("selectionDictionary") === "collins" ? "collins" : "ecdict";
        node("selection-service-hint").hidden = service.value !== "bing";
        button.disabled = importing || busy;
        download.disabled =
            importing || phase === "checking" || phase === "installing";
        download.textContent =
            phase === "downloading"
                ? msg("cancel")
                : phase === "checking"
                  ? msg("checking")
                  : phase === "installing"
                    ? msg("installing")
                    : phase === "failed"
                      ? msg("retry")
                      : update
                        ? msg("update")
                        : installed
                          ? msg("check")
                          : msg("download");
        const size = available
            ? `${(available.sizeBytes / 1048576).toFixed(1)} MB`
            : "";
        status.textContent = importing
            ? msg("importing")
            : phase === "downloading"
              ? msg("progress", {
                    received: (state.received / 1048576).toFixed(1),
                    total: size,
                })
              : installed
                ? msg("installed", {
                      count: installed.entries.toLocaleString(),
                  }) + (update ? ` · ${msg("available")}` : "")
                : size
                  ? msg("ready", {
                        size,
                        count: available!.entryCount.toLocaleString(),
                    })
                  : msg("builtin");
        progress.hidden = phase !== "downloading";
        progress.value = available
            ? Math.min(100, (state.received * 100) / available.sizeBytes)
            : 0;
        error.textContent = importError
            ? msg("import-error")
            : state.error
              ? msg(
                    state.error === "catalog"
                        ? "catalog-error"
                        : "download-error",
                )
              : "";
        error.hidden = !error.textContent;
        node("selection-dictionary-details").textContent = installed
            ? msg("details", {
                  version: installed.version || msg("local"),
                  count: installed.entries,
                  ai: installed.aiEntries,
                  skipped: installed.skipped,
                  date: new Date(installed.importedAt).toLocaleString(),
                  hash: installed.sha256,
              })
            : msg("details-empty");
    };
    service.value =
        getPref("selectionTranslationProvider") === "profile"
            ? "profile"
            : "bing";
    service.addEventListener("command", () => {
        setPref("selectionTranslationProvider", service.value);
        changed();
        render();
    });
    dictionary.addEventListener("command", () => {
        dictionaryChoiceChanged();
        setPref("selectionDictionary", dictionary.value);
        changed();
        render();
    });
    const unsubscribe = subscribeDictionaryDownload(render);
    window.addEventListener("unload", unsubscribe, { once: true });
    download.addEventListener("click", () => {
        importError = false;
        const state = dictionaryDownloadState();
        if (state.phase === "downloading") cancelDictionaryDownload();
        else if (
            state.error === "catalog" ||
            (state.installed &&
                (!state.available ||
                    !state.installed.version ||
                    state.installed.version === state.available.version))
        )
            void refreshDictionaryDownload();
        else void downloadDictionary();
    });
    button.addEventListener("click", () => {
        void (async () => {
            importing = true;
            importError = false;
            render();
            try {
                const path = await new ztoolkit.FilePicker(
                    msg("choose"),
                    "open",
                    [["JSON", "*.json"]],
                ).open();
                if (!path) return;
                await importODHDictionary(path);
                await dictionaryImported();
                changed();
            } catch {
                recordDiagnostic("selection_dictionary_import_failed");
                importError = true;
            } finally {
                importing = false;
                render();
            }
        })();
    });
    render();
    void refreshDictionaryDownload();
}
