import { config } from "../../package.json";
import { getLocaleID, getString } from "../utils/locale";
import { createSelectionMenu, selectionMenuStyle } from "./selectionUI";
import { selectionElement } from "./selectionPopup";
import { addGlossaryEntry } from "./glossaryStore";
import {
    listFavorites,
    watchFavorites,
    deleteFavorite,
    openFavorite,
    exportFavorites,
    importFavorites,
    favoriteLibrary,
    type SelectionFavorite,
} from "./selectionFavorites";

type Reader = _ZoteroTypes.ReaderInstance;
type Details = HTMLElement & {
    tabID?: string;
    render: () => Promise<void>;
    scrollToPane: (id: string, behavior: string) => Promise<void>;
};
const hosts = new Map<
    Reader,
    { body: HTMLElement; details: Details; paneID: string; cleanup: () => void }
>();
let registered: string | undefined;

function mount(body: HTMLElement, reader: Reader) {
    const doc = body.ownerDocument;
    body.replaceChildren();
    const style = selectionElement(doc, "style");
    style.textContent = `${selectionMenuStyle}
        .st-favorites{--st-bg:Canvas;--st-text:CanvasText;--st-soft:ButtonFace;--st-accent:Highlight;--st-border:color-mix(in srgb,CanvasText 16%,transparent);font:13px/1.5 system-ui;color:var(--fill-primary,CanvasText);overflow-wrap:anywhere;padding:8px 12px}
        .st-favorites button,.st-favorites input,.st-favorites select{font:inherit;color:inherit;box-sizing:border-box;max-width:100%;min-width:0;margin:0;padding:4px 6px;border:1px solid var(--st-border);border-radius:4px;background:transparent}
        .st-favorites button{cursor:pointer}.st-favorites button:hover{background:var(--st-soft)}
        .st-favorites .st-menu button{border:0;padding:5px 10px}
        .st-favorites :focus-visible{outline:2px solid Highlight;outline-offset:1px}
        .st-favorites-toolbar{display:flex;gap:6px;align-items:center}.st-favorites-toolbar input{width:0;flex:1}.st-favorites-toolbar select{max-width:38%}
        .st-favorites article{padding:12px 0;border-bottom:1px solid var(--st-border)}.st-favorites article>button{margin:6px 4px 0 0;font-size:12px;border-color:transparent}
        .st-favorites p{white-space:pre-wrap;margin:5px 0}.st-favorites p:empty{display:none}.st-favorites label{display:block}.st-favorites-list:empty{display:none}.st-favorites-list{margin-top:10px}
        `;
    const root = selectionElement(doc, "div");
    root.className = "st-favorites";
    const filter = selectionElement(doc, "select");
    filter.setAttribute("aria-label", getString("selection-favorites-scope"));
    for (const [value, label] of [
        ["paper", getString("selection-favorites-paper")],
        ["all", getString("selection-favorites-all")],
    ]) {
        const option = selectionElement(doc, "option");
        option.value = value;
        option.textContent = label;
        filter.append(option);
    }
    const search = selectionElement(doc, "input");
    search.type = "search";
    search.placeholder = getString("selection-favorites-search");
    search.setAttribute("aria-label", search.placeholder);
    const status = selectionElement(doc, "p");
    status.setAttribute("role", "status");
    const list = selectionElement(doc, "div");
    list.className = "st-favorites-list";
    const toolbar = selectionElement(doc, "div");
    toolbar.className = "st-favorites-toolbar";
    const more = createSelectionMenu(root, getString("selection-more"));
    toolbar.append(filter, search, more.trigger);
    const run = (action: () => Promise<unknown> | unknown) => {
        void Promise.resolve()
            .then(action)
            .catch((error) => {
                status.textContent =
                    error instanceof Error
                        ? error.message
                        : "操作失败，请重试。";
            });
    };
    const button = (
        label: string,
        action: () => Promise<unknown> | unknown,
    ) => {
        const node = selectionElement(doc, "button");
        node.type = "button";
        node.textContent = label;
        node.addEventListener("click", () => run(action));
        return node;
    };
    const exportButton = (csv: boolean) =>
        button(
            getString(csv ? "selection-export-csv" : "selection-export-json"),
            async () => {
                const file = await new ztoolkit.FilePicker(
                    "导出划词收藏",
                    "save",
                    [[csv ? "CSV" : "JSON", csv ? "*.csv" : "*.json"]],
                    `selection-favorites.${csv ? "csv" : "json"}`,
                ).open();
                if (file) {
                    await IOUtils.writeUTF8(file, await exportFavorites(csv));
                    status.textContent = "收藏已导出。";
                }
            },
        );
    more.menu.append(
        exportButton(false),
        exportButton(true),
        button(getString("selection-import-json"), async () => {
            const file = await new ztoolkit.FilePicker("导入划词收藏", "open", [
                ["JSON", "*.json"],
            ]).open();
            if (file) {
                const result = await importFavorites(
                    await IOUtils.readUTF8(file),
                );
                status.textContent = `新增 ${result.added} 条；保留本地冲突记录 ${result.conflicts} 条。`;
            }
        }),
    );
    root.append(toolbar, status, list);
    body.append(style, root);
    function glossaryForm(entry: SelectionFavorite, article: HTMLElement) {
        const form = selectionElement(doc, "div");
        const input = (label: string, value: string) => {
            const wrapper = selectionElement(doc, "label"),
                field = selectionElement(doc, "input");
            wrapper.textContent = label;
            field.value = value;
            wrapper.append(field);
            form.append(wrapper);
            return field;
        };
        const word = input("原词", entry.word),
            meaning = input(
                "选定译法",
                entry.contextMeaning || entry.meaning.split("\n")[0],
            ),
            lang = input("目标语言", entry.targetLang);
        const message = selectionElement(doc, "p");
        const save = (replace = false) => {
            const result = addGlossaryEntry(
                {
                    source: word.value,
                    target: meaning.value,
                    tgt_lng: lang.value,
                },
                replace,
            );
            if (result === "conflict") {
                message.textContent =
                    "术语表已有不同译法。请选择保留原值或替换。";
                if (!form.querySelector(".st-conflict")) {
                    const options = selectionElement(doc, "div");
                    options.className = "st-conflict";
                    options.append(
                        button("保留原值", () => form.remove()),
                        button("替换译法", () => save(true)),
                    );
                    form.append(options);
                }
            } else {
                status.textContent =
                    result === "exists" ? "该术语已存在。" : "已加入术语表。";
                form.remove();
            }
        };
        form.append(
            button("保存术语", () => save()),
            button("取消", () => form.remove()),
            message,
        );
        article.append(form);
    }
    let revision = 0;
    async function render() {
        const current = ++revision;
        const entries = await listFavorites();
        if (current !== revision || !body.isConnected) return;
        const attachment = reader.itemID && Zotero.Items.get(reader.itemID);
        if (!attachment) throw new Error("找不到当前 PDF 附件。");
        const library = favoriteLibrary(attachment.libraryID);
        const needle = search.value.toLowerCase();
        const shown = entries.filter(
            (e) =>
                (filter.value === "all" ||
                    (e.attachmentKey === attachment.key &&
                        JSON.stringify(e.library) ===
                            JSON.stringify(library))) &&
                [e.word, e.meaning, e.contextMeaning, e.title]
                    .join(" ")
                    .toLowerCase()
                    .includes(needle),
        );
        list.replaceChildren();
        if (!shown.length)
            list.textContent = getString(
                entries.length
                    ? "selection-favorites-no-match"
                    : "selection-favorites-empty",
            );
        for (const entry of shown.reverse()) {
            const article = selectionElement(doc, "article"),
                heading = selectionElement(doc, "strong"),
                meaning = selectionElement(doc, "p");
            heading.textContent = entry.word;
            meaning.textContent = entry.contextMeaning || entry.meaning;
            const details = selectionElement(doc, "details"),
                summary = selectionElement(doc, "summary"),
                content = selectionElement(doc, "p");
            summary.textContent = `${entry.title} · 第 ${entry.pageLabel || entry.pageIndex + 1} 页`;
            content.textContent = [
                `来源：${entry.source}`,
                `划选原文：${entry.original}`,
                entry.query !== entry.original
                    ? `修订查询：${entry.query}`
                    : "",
                entry.sentence ? `论文原句：${entry.sentence}` : "",
                `收藏释义：${entry.meaning}`,
            ]
                .filter(Boolean)
                .join("\n");
            details.append(summary, content);
            article.append(
                heading,
                meaning,
                details,
                button("回到原文", async () => {
                    status.textContent = await openFavorite(entry);
                }),
                button("复制", () => {
                    Zotero.Utilities.Internal.copyTextToClipboard(
                        [entry.word, entry.meaning, entry.sentence, entry.title]
                            .filter(Boolean)
                            .join("\n"),
                    );
                }),
                button("加入术语表", () => glossaryForm(entry, article)),
                button("删除收藏", async () => {
                    await deleteFavorite(entry.id);
                    status.textContent = "收藏已删除。";
                }),
            );
            list.append(article);
        }
    }
    filter.addEventListener("change", () => run(render));
    search.addEventListener("input", () => run(render));
    run(render);
    const unwatch = watchFavorites(() => run(render));
    return () => {
        more.destroy();
        unwatch();
    };
}

export function registerFavoritesPane() {
    if (registered || !Zotero.ItemPaneManager?.registerSection) return;
    const render = ({
        body,
        tabType,
        setEnabled,
        paneID,
    }: _ZoteroTypes.ItemPaneManagerSection.SectionHookArgs) => {
        const details = body.closest("item-details") as Details | null;
        const tabID = details?.tabID || details?.getAttribute("data-tab-id");
        const reader = tabID ? Zotero.Reader.getByTabID(tabID) : undefined;
        setEnabled(tabType === "reader" && reader?.type === "pdf");
        if (
            !reader ||
            !details ||
            tabType !== "reader" ||
            hosts.get(reader)?.body === body
        )
            return;
        hosts.get(reader)?.cleanup();
        hosts.set(reader, {
            body,
            details,
            paneID,
            cleanup: mount(body, reader),
        });
    };
    registered =
        Zotero.ItemPaneManager.registerSection({
            paneID: "selection-favorites",
            pluginID: config.addonID,
            header: {
                l10nID: getLocaleID("selection-favorites-header"),
                icon: `chrome://${config.addonRef}/content/icons/selection-translate.svg`,
            },
            sidenav: {
                l10nID: getLocaleID("selection-favorites-nav"),
                icon: `chrome://${config.addonRef}/content/icons/selection-translate.svg`,
            },
            onInit: render,
            onItemChange: render,
            onRender: render,
            onDestroy({ body }) {
                for (const [reader, host] of hosts)
                    if (host.body === body) {
                        host.cleanup();
                        hosts.delete(reader);
                    }
            },
        }) || undefined;
}
export function unregisterFavoritesPane() {
    if (registered) Zotero.ItemPaneManager.unregisterSection(registered);
    registered = undefined;
    hosts.forEach((host) => host.cleanup());
    hosts.clear();
}
export async function openFavoritesPane(reader: Reader) {
    const win = reader._window || Zotero.getMainWindow();
    if (win.ZoteroContextPane) win.ZoteroContextPane.collapsed = false;
    const context =
        win.ZoteroContextPane?.context ||
        win.document.querySelector("context-pane");
    if (context) (context as HTMLElement & { mode: string }).mode = "item";
    const details = Array.from(
        win.document.querySelectorAll("item-details"),
    ).find(
        (node) =>
            (node as Details).tabID === reader.tabID ||
            (node as HTMLElement).getAttribute("data-tab-id") === reader.tabID,
    ) as Details | undefined;
    await details?.render();
    const host = hosts.get(reader);
    if (!host) throw new Error("此窗口无法打开收藏侧栏。");
    await host.details.scrollToPane(host.paneID, "instant");
}
