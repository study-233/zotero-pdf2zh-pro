// node scripts/check_notices_ui.cjs <playwright module path>
// Uses packaged UI assets and real notice modules; never calls a translation API.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const ts = require("../plugin/node_modules/typescript");
const { chromium } = require(process.argv[2] || "playwright");
const root = path.resolve(__dirname, "..");
const report = require("./workspace.cjs").newReport("notices-ui");
const build = path.join(root, "plugin/build/addon");
const pkg = require("../plugin/package.json");
const modules = {};
for (const name of ["userNotice", "releaseNotes", "pluginNotices"]) {
    modules[`./${name}`] = ts.transpileModule(fs.readFileSync(path.join(root, `plugin/src/modules/${name}.ts`), "utf8"), {
        compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    }).outputText;
}
const locales = Object.fromEntries(["zh-CN", "en-US"].map(locale => [locale, Object.fromEntries(
    [...fs.readFileSync(path.join(build, `locale/${locale}/${pkg.config.addonRef}-addon.ftl`), "utf8").matchAll(/^([\w-]+) = (.+)$/gm)]
        .map(match => [match[1].replace(`${pkg.config.addonRef}-`, ""), match[2]]),
)]));

(async () => {
    const browser = await chromium.launch({headless: true, channel: "chrome"});
    let checks = 0;
    try {
        for (const locale of Object.keys(locales)) for (const theme of ["light", "dark"]) for (const width of [360, 900]) {
            const page = await browser.newPage({viewport: {width, height: 720}, colorScheme: theme});
            const errors = [];
            page.on("pageerror", error => errors.push(error.message));
            await page.route("**/*", route => {
                const url = new URL(route.request().url());
                const file = path.basename(url.pathname);
                if (["noticeHelp.xhtml", "noticeHelp.js", "notices.css"].includes(file)) {
                    return route.fulfill({body: fs.readFileSync(path.join(build, "content", file)), contentType: file.endsWith("xhtml") ? "application/xhtml+xml" : file.endsWith("css") ? "text/css" : "text/javascript"});
                }
                return route.abort();
            });
            await page.addInitScript(({modules, pkg, strings}) => {
                window.noticeActions = [];
                window.noticeLinks = [];
                window.noticeState = {};
                const t = (key, options) => {
                    if (!strings[key]) throw new Error(`Missing packaged message: ${key}`);
                    return strings[key].replace(/\{\s*\$(\w+)\s*\}/g, (_, arg) => options?.args?.[arg] ?? "");
                };
                const cache = {
                    "../../package.json": pkg,
                    "../utils/locale": {getString: t},
                    "../utils/prefs": {getPref: key => window.noticeState[key], setPref: (key, value) => {window.noticeState[key] = value;}},
                };
                function load(name) {
                    if (cache[name]) return cache[name];
                    const exports = {};
                    cache[name] = exports;
                    new Function("exports", "require", modules[name])(exports, load);
                    return exports;
                }
                window.ztoolkit = {log: error => {throw error;}};
                window.noticeAPI = load("./pluginNotices");
                window.noticeAPI.configureNoticeActions(action => window.noticeActions.push(action));
                window.noticeRender = load("./userNotice").renderNotice;
                window.arguments = [{
                    message: t,
                    releases: load("./releaseNotes").releaseSummaries,
                    action: action => window.noticeActions.push(action),
                    guide: anchor => window.noticeLinks.push(anchor),
                    changelog: () => window.noticeLinks.push("changelog"),
                }];
            }, {modules, pkg, strings: locales[locale]});
            await page.goto("http://notice.test/noticeHelp.xhtml");
            await page.waitForSelector("#help button");
            assert.equal(await page.locator("#updates li").count(), 3);
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
            await page.locator("#updates li button").first().click();
            assert.deepEqual(await page.evaluate(() => window.noticeActions), ["profiles"]);
            await page.screenshot({path: path.join(report, `${locale}-${theme}-${width}-help.png`), fullPage: true});
            await page.evaluate(() => {
                const button = document.querySelector("#help button");
                button.focus();
                window.focusBeforeNotice = button;
                window.noticeAPI.registerNoticeWindow(window);
                window.noticeAPI.initializeReleaseNotices(1);
                const link = document.querySelector('.pdf2zh-update-card link');
                if (link) link.href = "http://notice.test/notices.css";
            });
            await page.waitForSelector(".pdf2zh-update-card");
            await page.waitForFunction(() => Boolean(window.noticeState.releaseNoticeState));
            assert.equal(await page.evaluate(() => document.activeElement === window.focusBeforeNotice), true);
            const box = await page.locator(".pdf2zh-update-card").boundingBox();
            assert.ok(box.x >= 0 && box.x + box.width <= width);
            await page.screenshot({path: path.join(report, `${locale}-${theme}-${width}-card.png`)});
            await page.locator(".pdf2zh-notice-actions button").last().click();
            await page.evaluate(() => window.dispatchEvent(new Event("focus")));
            assert.equal(await page.locator(".pdf2zh-update-card").count(), 0);
            await page.evaluate(() => window.noticeAPI.stopPluginNotices());
            assert.deepEqual(errors, []);
            checks++;
            await page.close();
        }
        fs.writeFileSync(path.join(report, "result.json"), JSON.stringify({passed: true, combinations: checks, scope: "packaged help, update card, localization, themes, narrow layout, focus, dismissal and actions"}, null, 2));
        console.log(`Passed ${checks} notice UI combinations.`);
    } finally { await browser.close(); }
})().catch(error => {console.error(error); process.exitCode = 1;});
