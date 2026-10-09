import { cancelDictionaryDownload } from "./modules/selectionDictionaryDownload";
import { stopDevelopmentLibrary } from "./modules/developmentLibrary";
// lifecycle hooks
import { PDF2zhBasicFactory, PDF2zhUIFactory } from "./modules/pdf2zh";
import { PDF2zhTaskManager } from "./modules/pdf2zhTaskManager";
import { initLocale } from "./utils/locale";
import { createZToolkit } from "./utils/ztoolkit";
import { registerPrefsScripts } from "./modules/preferenceScript";
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
    migrateReviewPreference();
    migrateSelectionPreferences(reason);
    repairAttachmentNamingPreferences();
    initLocale();
    PDF2zhBasicFactory.registerPrefs();
    await Promise.all(
        Zotero.getMainWindows().map((win) => onMainWindowLoad(win)),
    );
    registerSelectionTranslation();
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
    await new Promise((resolve) => setTimeout(resolve, 200));
}

async function onPrefsEvent(type: string, data: { [key: string]: any }) {
    if (type === "load") {
        registerPrefsScripts(data.window);
    }
}

async function onMainWindowUnload(_win: Window): Promise<void> {
    ztoolkit.unregisterAll();
    addon.data.dialog?.window?.close();
    PDF2zhTaskManager.closeWindow();
}

function onShutdown(): void {
    stopDevelopmentLibrary();
    cancelDictionaryDownload();
    unregisterSelectionTranslation();
    PDF2zhTaskManager.stop();
    ztoolkit.unregisterAll();
    addon.data.dialog?.window?.close();
    PDF2zhTaskManager.closeWindow();
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

function onDialogEvents(type: string) {
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
