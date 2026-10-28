import { getString } from "../utils/locale";
import { preparePreferenceTarget } from "./preferenceCards";

export class PDF2zhBasicFactory {
    static paneID = "pdf2zhpro-preferences";
    static async registerPrefs() {
        await Zotero.PreferencePanes.register({
            id: this.paneID,
            pluginID: addon.data.config.addonID,
            src: rootURI + "content/preferences.xhtml",
            label: getString("prefs-title"),
            image: `chrome://${addon.data.config.addonRef}/content/icons/favicon.svg`,
        });
    }

    static openPrefs(target = "new_serverip") {
        preparePreferenceTarget(target);
        Zotero.Utilities.Internal.openPreferences(this.paneID, {
            scrollTo: `#zotero-prefpane-${addon.data.config.addonRef}-${target}`,
        });
    }
}

export class PDF2zhUIFactory {
    private static registeredMenuID: string | false = false;

    static registerRightClickMenuItem() {
        if (this.registeredMenuID) return;
        const menuIcon = `chrome://${addon.data.config.addonRef}/content/icons/favicon@0.5x.svg`;
        const menuPrefix = `zotero-itemmenu-${addon.data.config.addonRef}`;
        this.registeredMenuID = Zotero.MenuManager.registerMenu({
            menuID: menuPrefix,
            pluginID: addon.data.config.addonID,
            target: "main/library/item",
            menus: [
                {
                    menuType: "submenu",
                    icon: menuIcon,
                    onShowing: (_event, context) => {
                        context.menuElem.setAttribute(
                            "label",
                            "zotero-pdf2zh-pro",
                        );
                    },
                    menus: [
                        {
                            menuType: "menuitem",
                            icon: menuIcon,
                            onShowing: (_event, context) => {
                                context.menuElem.setAttribute(
                                    "label",
                                    `zotero-pdf2zh-pro: ${getString("prefs-menu-translate")}`,
                                );
                            },
                            onCommand: () =>
                                addon.hooks.onDialogEvents("translatePDF"),
                        },
                        {
                            menuType: "menuitem",
                            icon: menuIcon,
                            onShowing: (_event, context) => {
                                context.menuElem.setAttribute(
                                    "label",
                                    `zotero-pdf2zh-pro: ${getString("prefs-menu-tasks")}`,
                                );
                            },
                            onCommand: () =>
                                addon.hooks.onDialogEvents("openTaskManager"),
                        },
                        {
                            menuType: "menuitem",
                            onShowing: (_event, context) => {
                                context.menuElem.setAttribute(
                                    "label",
                                    getString("notice-help-title"),
                                );
                            },
                            onCommand: () =>
                                addon.hooks.onDialogEvents("openNoticeHelp"),
                        },
                    ],
                },
            ],
        });
    }
}
