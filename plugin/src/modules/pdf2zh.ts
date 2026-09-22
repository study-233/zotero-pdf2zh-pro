import { getString } from "../utils/locale";

export class PDF2zhBasicFactory {
    static registerPrefs() {
        Zotero.PreferencePanes.register({
            pluginID: addon.data.config.addonID,
            src: rootURI + "content/preferences.xhtml",
            label: getString("prefs-title"),
            image: `chrome://${addon.data.config.addonRef}/content/icons/favicon.svg`,
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
                    ],
                },
            ],
        });
    }
}
