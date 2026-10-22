import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import { JSDOM } from "jsdom";
import { noticeModules } from "./noticeFixture.mjs";

const pkg = JSON.parse(
    fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);
const releases = noticeModules()("./releaseNotes").releaseSummaries;
const template = fs.readFileSync(
    new URL("../addon/content/noticeHelp.xhtml", import.meta.url),
    "utf8",
);
const script = fs.readFileSync(
    new URL("../addon/content/noticeHelp.js", import.meta.url),
    "utf8",
);
const guideUrl =
    "https://github.com/study-233/zotero-pdf2zh-pro/blob/main/docs/user-guide.md";
const changelogUrl =
    "https://github.com/study-233/zotero-pdf2zh-pro/blob/main/CHANGELOG.md";

async function fixture(locale, catalog = releases) {
    const strings = Object.fromEntries(
        [
            ...fs
                .readFileSync(
                    new URL(
                        `../addon/locale/${locale}/addon.ftl`,
                        import.meta.url,
                    ),
                    "utf8",
                )
                .matchAll(/^([\w-]+) = (.+)$/gm),
        ].map((match) => [match[1], match[2]]),
    );
    const dom = new JSDOM(template, {
        contentType: "application/xhtml+xml",
        runScripts: "outside-only",
    });
    const opened = [];
    dom.window.arguments = [
        {
            message: (key) => {
                assert.ok(strings[key], `${locale}: ${key}`);
                return strings[key];
            },
            version: pkg.version,
            releases: catalog,
            guideUrl,
            changelogUrl,
            openLink: (url) => opened.push(url),
        },
    ];
    dom.window.eval(script);
    await new Promise((resolve) =>
        dom.window.addEventListener("DOMContentLoaded", resolve, {
            once: true,
        }),
    );
    return { dom, doc: dom.window.document, opened };
}

for (const locale of ["zh-CN", "en-US"]) {
    test(`${locale}: current notes stay separate from collapsed history; help links open their exact chapters`, async () => {
        const { dom, doc, opened } = await fixture(locale);
        const close = dom.window.close.bind(dom.window);
        try {
            assert.equal(doc.querySelectorAll("#updates li").length, 3);
            assert.ok(
                doc.querySelector("#updates").textContent.includes(pkg.version),
            );
            assert.ok(
                !doc.querySelector("#updates").textContent.includes("1.9.1"),
            );
            const history = doc.querySelector("#history");
            assert.equal(history.hidden, false);
            assert.equal(history.open, false);
            history.querySelector("summary").click();
            assert.equal(history.open, true);
            assert.ok(history.textContent.includes("1.9.1"));
            assert.equal(doc.querySelectorAll("button").length, 0);
            const expected = [
                "usage",
                "selection-translation",
                "api-configuration",
                "troubleshooting",
            ].map((anchor) => `${guideUrl}#${anchor}`);
            const links = [...doc.querySelectorAll("#help a")];
            assert.deepEqual(
                links.map((link) => link.href),
                expected,
            );
            for (const link of doc.querySelectorAll("a")) link.click();
            assert.deepEqual(opened, [...expected, guideUrl, changelogUrl]);
            let closed = false;
            dom.window.close = () => {
                closed = true;
            };
            dom.window.dispatchEvent(
                new dom.window.KeyboardEvent("keydown", { key: "Escape" }),
            );
            assert.equal(closed, true);
        } finally {
            close();
        }
    });
}

test("a release without news shows the empty summary and hides absent history", async () => {
    const { dom, doc } = await fixture("zh-CN", [
        { version: pkg.version, notes: [] },
    ]);
    try {
        assert.equal(doc.querySelector("#history").hidden, true);
        assert.equal(doc.querySelectorAll("#updates li").length, 0);
        assert.match(doc.querySelector("#updates p").textContent, /本版没有/);
    } finally {
        dom.window.close();
    }
});
