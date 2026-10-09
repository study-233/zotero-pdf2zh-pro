// Production favorites renderer with a mock local library; no Zotero access.
const fs = require("fs"), path = require("path"), assert = require("node:assert/strict");
const ts = require("../plugin/node_modules/typescript");
const { chromium } = require(process.argv[2] || "playwright");
const root = path.resolve(__dirname, ".."), output = path.join(root, ".local-dev/selection-ui");
const modules = {};
for (const name of ["selectionUI", "selectionFavoritesPane"])
    modules["./" + name] = ts.transpileModule(fs.readFileSync(path.join(root, "plugin/src/modules/" + name + ".ts"), "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
fs.mkdirSync(output, { recursive: true });
(async () => {
    const browser = await chromium.launch({ headless: true, channel: "chrome" });
    try {
        for (const fallback of [false, true]) {
            const page = await browser.newPage({ viewport: { width: 460, height: 680 } });
            const errors = [];
            page.on("pageerror", e => errors.push(e.message));
            await page.route("**/*", route => route.abort());
            const fluent = fs.readFileSync(path.join(root, "plugin/addon/locale/zh-CN/addon.ftl"), "utf8");
            const labels = Object.fromEntries([...fluent.matchAll(/^([\w-]+) = (.+)$/gm)].map(m => [m[1], m[2].trim()]));
            await page.setContent('<html><body style="margin:8px;background:Canvas"><item-details><div id="host"></div></item-details></body></html>');
            await page.evaluate(({ modules, labels, fallback }) => {
                if (fallback) HTMLElement.prototype.showPopover = undefined;
                document.querySelector("item-details").tabID = "paper";
                const reader = { type: "pdf", itemID: 1, tabID: "paper" };
                window.entries = []; window.exported = [];
                window.Zotero = {
                    Reader: { getByTabID: () => reader },
                    Items: { get: () => ({ key: "PAPER", libraryID: 1 }) },
                    ItemPaneManager: {
                        registerSection(o) { o.onRender({ body: document.querySelector("#host"), tabType: "reader", paneID: "favorites", setEnabled() {} }); return "favorites"; },
                        unregisterSection() {},
                    },
                };
                window.ztoolkit = { FilePicker: class { async open() { return "mock.json"; } } };
                window.IOUtils = { writeUTF8: async (_path, text) => exported.push(text) };
                const cache = {
                    "../../package.json": { config: { addonRef: "test" } },
                    "../utils/locale": { getString: key => labels[key] || key, getLocaleID: key => key },
                    "./selectionPopup": { selectionElement: (doc, tag) => doc.createElementNS("http://www.w3.org/1999/xhtml", tag) },
                    "./glossaryStore": { addGlossaryEntry: () => "added" },
                    "./selectionFavorites": {
                        listFavorites: async () => entries,
                        watchFavorites: callback => { window.refresh = callback; return () => {}; },
                        favoriteLibrary: () => ({ type: "user" }),
                        exportFavorites: async csv => csv ? "CSV" : "JSON",
                    },
                };
                const require = key => {
                    if (!cache[key]) {
                        const exports = {};
                        new Function("require", "exports", modules[key])(require, exports);
                        cache[key] = exports;
                    }
                    return cache[key];
                };
                window.paneAPI = require("./selectionFavoritesPane");
                paneAPI.registerFavoritesPane();
            }, { modules, labels, fallback });
            const pane = page.locator(".st-favorites");
            await page.waitForFunction(() => document.querySelector(".st-favorites-list").textContent.includes("还没有收藏"));
            await page.evaluate(() => {
                entries.push({ id: "1", word: "transduction", meaning: "转导；信号转导", title: "Attention Is All You Need", attachmentKey: "PAPER", library: { type: "user" }, pageIndex: 1, original: "transduction", query: "transduction", source: "有道在线词典" });
                refresh();
            });
            await pane.locator("article").waitFor();
            await pane.getByRole("searchbox").fill("missing");
            await page.waitForFunction(() => document.querySelector(".st-favorites-list").textContent.includes("没有匹配"));
            await pane.getByRole("searchbox").fill("");
            await pane.locator("article").waitFor();
            for (const theme of ["light", "dark"]) {
                for (const width of [280, 320, 400]) {
                    await page.evaluate(({ width, theme }) => {
                        document.documentElement.style.colorScheme = theme;
                        document.querySelector("#host").style.cssText = "width:" + width + "px;overflow:hidden";
                        document.querySelector(".st-favorites").style.fontSize = "18px";
                    }, { width, theme });
                    assert.ok(await pane.evaluate(el => el.scrollWidth <= el.clientWidth));
                    const trigger = pane.getByRole("button", { name: "更多操作" });
                    await trigger.focus(); await page.keyboard.press("ArrowUp");
                    const menu = pane.getByRole("menu"); await menu.waitFor();
                    assert.equal(await menu.getByRole("menuitem").last().evaluate(el => el === document.activeElement), true);
                    assert.ok(await menu.evaluate(el => { const r = el.getBoundingClientRect(); return r.left >= 0 && r.right <= innerWidth && el.contains(document.elementFromPoint(r.left + 10, r.bottom - 10)); }));
                    await page.keyboard.press("Escape");
                    assert.equal(await menu.isVisible(), false);
                    assert.ok(await trigger.locator("svg").evaluate(el => el.getBBox().width > 0));
                    await pane.screenshot({ path: path.join(output, "favorites-" + width + "-" + theme + (fallback ? "-fallback" : "") + ".png") });
                }
            }
            await pane.getByRole("button", { name: "更多操作" }).click();
            await pane.getByRole("menuitem", { name: "导出 JSON", exact: true }).click();
            await page.waitForFunction(() => exported.includes("JSON"));
            await page.evaluate(() => paneAPI.unregisterFavoritesPane());
            assert.deepEqual(errors, []);
            await page.close();
        }
        console.log("PASS favorites: empty/search, export menu, inline icons, themes, 280/320/400px, enlarged text and menu without Popover API");
    } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
