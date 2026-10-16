import assert from "node:assert/strict";
import fs from "node:fs";
import { URL } from "node:url";
import test from "node:test";
import { JSDOM } from "jsdom";
import { noticeModules } from "./noticeFixture.mjs";

const release = noticeModules()("./releaseNotes");
const pkg = JSON.parse(
    fs.readFileSync(new URL("../package.json", import.meta.url), "utf8"),
);
const { pendingReleaseNotes: pending, completeReleaseNotice: complete } =
    release;
const summaries = [
    { version: "1.9.0", notes: [{ id: "a", priority: 2 }] },
    {
        version: "1.10.0",
        notes: [
            { id: "b", priority: 1 },
            { id: "c", priority: 0 },
            { id: "d", priority: 2 },
        ],
    },
    { version: "1.11.0", notes: [] },
];
test("new install, legacy adoption, skipped versions and empty releases", () => {
    assert.deepEqual(pending(summaries, "1.9.0", undefined, true), []);
    assert.deepEqual(
        pending(summaries, "1.10.0", undefined, false).map((n) => n.id),
        ["c", "b", "d"],
    );
    const old = { version: "1.8.0", shown: [] };
    assert.deepEqual(
        pending(summaries, "1.10.0", old, false).map((n) => n.id),
        ["c", "b", "d"],
    );
    assert.deepEqual(
        pending(summaries, "1.11.0", { version: "1.10.0", shown: [] }, false),
        [],
    );
    const state = complete(
        "1.10.0",
        old,
        pending(summaries, "1.10.0", old, false),
    );
    assert.deepEqual(pending(summaries, "1.10.0", state, false), []);
    assert.deepEqual(pending(summaries, "1.9.0", state, false), []);
    assert.equal(complete("1.9.0", state, []).version, "1.10.0");
    assert.equal(release.readReleaseState("broken"), undefined);
    assert.equal(
        release.readReleaseState('{"version":"1.2.3","shown":[42]}'),
        undefined,
    );
});

test("release catalog has reviewed current version, unique IDs, translations and working action types", () => {
    const catalogs = ["zh-CN", "en-US"].map((locale) =>
        Object.fromEntries(
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
            ].map((m) => [m[1], m[2]]),
        ),
    );
    assert.equal(release.releaseSummaries.at(-1).version, pkg.version);
    const ids = new Set();
    for (const entry of release.releaseSummaries) {
        assert.match(entry.version, /^\d+\.\d+\.\d+$/);
        assert.ok(entry.notes.length <= 3);
        for (const note of entry.notes) {
            assert.ok(!ids.has(note.id));
            ids.add(note.id);
            assert.ok([0, 1, 2].includes(note.priority));
            assert.ok(
                !note.action ||
                    ["profiles", "selection", "tasks"].includes(note.action),
            );
            for (const catalog of catalogs)
                assert.ok(catalog[note.message], note.message);
            assert.ok(catalogs[0][note.message].length <= 60);
        }
    }
    const keys = Object.keys(catalogs[0]).filter(
        (key) =>
            key.startsWith("notice-") ||
            key.startsWith("release-") ||
            key.startsWith("selection-error-"),
    );
    for (const key of keys) assert.ok(catalogs[1][key], `English: ${key}`);
});

function fixture(raw) {
    const prefs = new Map(
        raw ? [["releaseNoticeState", JSON.stringify(raw)]] : [],
    );
    const frames = [];
    const doms = [];
    let foreground;
    const load = noticeModules(
        {
            "../../package.json": pkg,
            "../utils/prefs": {
                getPref: (key) => prefs.get(key),
                setPref: (key, value) => prefs.set(key, value),
            },
            "../utils/locale": { getString: (key) => key },
        },
        {
            ztoolkit: { log() {} },
            Zotero: { getMainWindow: () => doms[0]?.window },
        },
    );
    const api = load("./pluginNotices");
    function window() {
        const dom = new JSDOM('<button id="focus">Editor</button>', {
            pretendToBeVisual: true,
        });
        doms.push(dom);
        const win = dom.window;
        win.document.hasFocus = () => foreground === win;
        win.requestAnimationFrame = (fn) => frames.push(fn);
        win.HTMLElement.prototype.getClientRects = () => [{}];
        return win;
    }
    return {
        api,
        prefs,
        window,
        focus(win) {
            foreground = win;
            win.dispatchEvent(new win.Event("focus"));
        },
        paint() {
            while (frames.length) frames.shift()();
        },
        close() {
            api.stopPluginNotices();
            for (const dom of doms) dom.window.close();
        },
    };
}

test("background startup waits; one foreground window renders without stealing focus", () => {
    const f = fixture();
    try {
        const a = f.window(),
            b = f.window();
        a.document.getElementById("focus").focus();
        f.api.registerNoticeWindow(a);
        f.api.registerNoticeWindow(b);
        f.api.initializeReleaseNotices(1);
        assert.equal(a.document.querySelector(".pdf2zh-update-card"), null);
        assert.equal(f.prefs.has("releaseNoticeState"), false);
        f.focus(a);
        assert.ok(a.document.querySelector(".pdf2zh-update-card"));
        assert.equal(a.document.activeElement.id, "focus");
        f.paint();
        assert.equal(
            JSON.parse(f.prefs.get("releaseNoticeState")).version,
            pkg.version,
        );
        f.focus(b);
        assert.equal(b.document.querySelector(".pdf2zh-update-card"), null);
        a.document
            .querySelector(".pdf2zh-notice-actions button:last-child")
            .click();
        f.api.initializeReleaseNotices(1);
        f.focus(a);
        f.paint();
        assert.equal(a.document.querySelector(".pdf2zh-update-card"), null);
    } finally {
        f.close();
    }
});

test("closed or failed render remains pending; a later window retries", () => {
    const f = fixture();
    try {
        const a = f.window(),
            b = f.window();
        f.api.registerNoticeWindow(a);
        f.api.registerNoticeWindow(b);
        f.api.initializeReleaseNotices(1);
        f.focus(a);
        f.api.unregisterNoticeWindow(a);
        f.paint();
        assert.equal(f.prefs.has("releaseNoticeState"), false);
        f.focus(b);
        f.paint();
        assert.ok(f.prefs.has("releaseNoticeState"));
    } finally {
        f.close();
    }
});

test("fresh install stays quiet and shutdown removes cards and listeners", () => {
    const f = fixture();
    try {
        const win = f.window();
        f.api.registerNoticeWindow(win);
        f.focus(win);
        f.api.initializeReleaseNotices(5);
        f.paint();
        assert.equal(win.document.querySelector(".pdf2zh-update-card"), null);
        assert.equal(
            JSON.parse(f.prefs.get("releaseNoticeState")).version,
            pkg.version,
        );
        f.api.stopPluginNotices();
        f.focus(win);
        f.paint();
        assert.equal(win.document.querySelector(".pdf2zh-update-card"), null);
    } finally {
        f.close();
    }
});
