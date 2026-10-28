import {
    cancelDocumentExport,
    exportSelectedDocument,
    type DocumentExportFormat,
} from "./modules/documentExport";
import { cancelDictionaryDownload } from "./modules/selectionDictionaryDownload";
import { stopDevelopmentLibrary } from "./modules/developmentLibrary";
import { stopDiagnostics } from "./modules/diagnostics";
import { stopPreferenceCards } from "./modules/preferenceCards";
// lifecycle hooks
import { PDF2zhBasicFactory, PDF2zhUIFactory } from "./modules/pdf2zh";
import { PDF2zhTaskManager } from "./modules/pdf2zhTaskManager";
import { initLocale } from "./utils/locale";
import { getPref } from "./utils/prefs";
import { createZToolkit } from "./utils/ztoolkit";
import {
    registerPrefsScripts,
    initializeProfileMigration,
    openChatGPTLogin,
    openProfileManager,
} from "./modules/preferenceScript";
import {
    configureNoticeActions,
    initializeReleaseNotices,
    registerNoticeWindow,
    unregisterNoticeWindow,
    stopPluginNotices,
    openNoticeHelp,
} from "./modules/pluginNotices";
import { migrateReviewPreference } from "./modules/reviewPreferences";
import { repairAttachmentNamingPreferences } from "./modules/attachmentNamingSettings";
import { migrateSelectionPreferences } from "./modules/selectionMigration";
import {
    registerSelectionTranslation,
    unregisterSelectionTranslation,
} from "./modules/selectionTranslate";

async function onStartup(reason?: number) {
    await Promise.all([
        Zotero.initializationPromise,
        Zotero.unlockPromise,
        Zotero.uiReadyPromise,
    ]);
    const existingInstall =
        Number(getPref("selectionPreferenceVersion") || 0) > 0;
    migrateReviewPreference();
    migrateSelectionPreferences(reason);
    repairAttachmentNamingPreferences();
    initLocale();
    await PDF2zhBasicFactory.registerPrefs();
    configureNoticeActions((action) => {
        if (action === "profiles") openProfileManager();
        else if (action === "tasks") PDF2zhTaskManager.openWindow();
        else
            PDF2zhBasicFactory.openPrefs(
                action === "selection" ? "selection-provider" : "new_serverip",
            );
    });
    await Promise.all(
        Zotero.getMainWindows().map((win) => onMainWindowLoad(win)),
    );
    try {
        initializeProfileMigration();
    } catch (error) {
        ztoolkit.log(error);
    }
    registerSelectionTranslation();
    initializeReleaseNotices(reason, existingInstall);
    void PDF2zhTaskManager.start().catch((error) => ztoolkit.log(error));
    if (typeof __devRuntime__ !== "undefined" && __devRuntime__) {
        const { prepareDevelopmentLibrary } =
            await import("./modules/developmentLibrary");
        await prepareDevelopmentLibrary(__devRuntime__);
    }
}

async function onMainWindowLoad(_win: Window): Promise<void> {
    addon.data.ztoolkit = createZToolkit();
    _win.MozXULElement.insertFTLIfNeeded(
        `${addon.data.config.addonRef}-addon.ftl`,
    );
    PDF2zhUIFactory.registerRightClickMenuItem();
    registerNoticeWindow(_win);
    await new Promise((resolve) => setTimeout(resolve, 200));
}

async function onPrefsEvent(type: string, data: { [key: string]: any }) {
    if (type === "load") {
        registerPrefsScripts(data.window);
    }
}

async function onMainWindowUnload(_win: Window): Promise<void> {
    cancelDocumentExport();
    unregisterNoticeWindow(_win);
    ztoolkit.unregisterAll();
    addon.data.dialog?.window?.close();
    PDF2zhTaskManager.closeWindow();
}

async function onShutdown(): Promise<void> {
    cancelDocumentExport();
    stopPreferenceCards();
    stopPluginNotices();
    stopDevelopmentLibrary();
    cancelDictionaryDownload();
    unregisterSelectionTranslation();
    PDF2zhTaskManager.stop();
    ztoolkit.unregisterAll();
    addon.data.dialog?.window?.close();
    PDF2zhTaskManager.closeWindow();
    await stopDiagnostics();
    addon.data.alive = false;
    // @ts-ignore - Plugin instance is not typed
    delete Zotero[addon.data.config.addonInstance];
}

async function onNotify(
    _event: string,
    _type: string,
    _ids: Array<string | number>,
    _extraData: { [key: string]: any },
) {
    return;
}

function onShortcuts(_type: string) {}

function onDialogEvents(type: string, format?: DocumentExportFormat) {
    if (type === "exportDocument" && format)
        void exportSelectedDocument(format);
    if (type === "openNoticeHelp") openNoticeHelp();
    if (type === "reloginChatGPT") openChatGPTLogin();
    if (type === "translatePDF") {
        PDF2zhTaskManager.processWorker();
    }
    if (type === "openTaskManager") {
        PDF2zhTaskManager.openWindow();
    }
}

export default {
    onStartup,
    onShutdown,
    onMainWindowLoad,
    onMainWindowUnload,
    onNotify,
    onPrefsEvent,
    onShortcuts,
    onDialogEvents,
};
