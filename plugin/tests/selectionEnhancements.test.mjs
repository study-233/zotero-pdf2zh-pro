import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { URL } from "node:url";
import { TextEncoder, TextDecoder } from "node:util";
import ts from "typescript";
import { JSDOM } from "jsdom";

const { DOMParser } = new JSDOM("").window;

function load(name, imports = {}, globals = {}) {
    globals = {
        Zotero: { getMainWindow: () => ({ DOMParser }) },
        ...globals,
    };
    const exports = {};
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL(`../src/modules/${name}.ts`, import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    new Function("require", "exports", ...Object.keys(globals), code)(
        (key) => {
            if (!(key in imports)) throw new Error(`Missing import ${key}`);
            return imports[key];
        },
        exports,
        ...Object.values(globals),
    );
    return exports;
}
const config = { config: { addonRef: "test", prefsPrefix: "test" } };
const diagnostics = { recordDiagnostic() {} };
const fixture = (word) =>
    JSON.parse(
        fs.readFileSync(
            new URL(
                `fixtures/selection-dictionaries/youdao-${word}.json`,
                import.meta.url,
            ),
            "utf8",
        ),
    );

test("real Youdao responses cover words, morphology, phrases, missing and malformed data", () => {
    const api = load("selectionOnlineDictionary", {
        "../../package.json": config,
        "./diagnostics": diagnostics,
    });
    for (const word of ["apple", "learning", "machine-learning"]) {
        const entry = api.parseYoudao(fixture(word), word);
        assert.ok(entry.senses.length);
        assert.ok(entry.examples.length);
        assert.ok(
            entry.examples.every((e) => !/[<>]/.test(e.english + e.chinese)),
        );
    }
    assert.equal(
        api.parseYoudao(fixture("learning"), "learning").forms[0].word,
        "learn",
    );
    assert.equal(
        api.parseYoudao(fixture("zzzxxyynotaword"), "zzzxxyynotaword"),
        undefined,
    );
    for (const data of [
        {},
        { code: 429, input: "word" },
        { input: "word", ec: { word: { trs: [] } } },
        "<html>error</html>",
    ])
        assert.throws(() => api.parseYoudao(data, "word"));
    assert.equal(api.safeDictionaryAudio("javascript:alert(1)"), undefined);
    assert.equal(api.safeDictionaryAudio("https://evil.test/a.mp3"), undefined);
});

test("selected online never touches offline; only a genuine offline miss queries fallback", async () => {
    const calls = [],
        prefs = new Map([["selectionDictionary", "youdao"]]);
    let result,
        fail = false;
    const api = load("selectionDictionaryService", {
        "../utils/prefs": { getPref: (k) => prefs.get(k) },
        "./selectionDictionary": {
            lookupDictionary: async () => {
                calls.push("offline");
                if (fail) throw Error();
                return result;
            },
        },
        "./selectionOnlineDictionary": {
            createOnlineDictionaryRequest: () => ({
                abort() {},
                lookup: async (source) => {
                    calls.push(source);
                    return { status: "miss", source };
                },
            }),
        },
    });
    await api.createDictionaryRequest().lookup("word", "en", "zh-CN");
    assert.deepEqual(calls, ["youdao"]);
    calls.length = 0;
    prefs.set("selectionDictionary", "ecdict");
    result = { text: "词", origin: "offline" };
    assert.equal(
        (await api.createDictionaryRequest().lookup("word", "en", "zh-CN"))
            .status,
        "hit",
    );
    assert.deepEqual(calls, ["offline"]);
    calls.length = 0;
    result = undefined;
    await api.createDictionaryRequest().lookup("word", "en", "zh-CN");
    assert.deepEqual(calls, ["offline", "youdao"]);
    calls.length = 0;
    fail = true;
    assert.equal(
        (await api.createDictionaryRequest().lookup("word", "en", "zh-CN"))
            .status,
        "error",
    );
    assert.deepEqual(calls, ["offline"]);
    calls.length = 0;
    prefs.set("selectionDictionary", "bing");
    await api.createDictionaryRequest().lookup("word", "en", "zh-CN");
    assert.deepEqual(calls, ["bing"]);
});

test("online cache refresh, cancellation and network errors do not poison later queries", async () => {
    let calls = 0,
        fail = false,
        resolve;
    const api = load(
        "selectionOnlineDictionary",
        { "../../package.json": config, "./diagnostics": diagnostics },
        {
            PathUtils: path,
            IOUtils: {
                readUTF8: async () => {
                    throw Error();
                },
                makeDirectory: async () => {},
                writeUTF8: async () => {},
            },
            Zotero: {
                getMainWindow: () => ({ DOMParser }),
                DataDirectory: { dir: "/data" },
                HTTP: {
                    request: async () => {
                        calls++;
                        if (fail) throw Error();
                        if (resolve !== undefined)
                            return new Promise((r) => {
                                resolve = r;
                            });
                        return {
                            responseText: JSON.stringify(fixture("learning")),
                        };
                    },
                },
            },
        },
    );
    assert.equal(
        (await api.createOnlineDictionaryRequest().lookup("youdao", "learning"))
            .cached,
        false,
    );
    assert.equal(
        (await api.createOnlineDictionaryRequest().lookup("youdao", "Learning"))
            .cached,
        true,
    );
    assert.equal(calls, 1);
    await api
        .createOnlineDictionaryRequest()
        .lookup("youdao", "learning", true);
    assert.equal(calls, 2);
    fail = true;
    assert.equal(
        (await api.createOnlineDictionaryRequest().lookup("youdao", "new"))
            .status,
        "error",
    );
    fail = false;
    assert.equal(
        (await api.createOnlineDictionaryRequest().lookup("youdao", "new"))
            .status,
        "hit",
    );
    resolve = null;
    const request = api.createOnlineDictionaryRequest();
    const pending = request.lookup("youdao", "late");
    for (let i = 0; i < 8; i++) await Promise.resolve();
    request.abort();
    resolve({ responseText: JSON.stringify(fixture("learning")) });
    assert.equal((await pending).status, "error");
    await api.clearOnlineDictionaryCache();
    resolve = undefined;
    assert.equal(
        (await api.createOnlineDictionaryRequest().lookup("youdao", "learning"))
            .cached,
        false,
    );
});

test("dictionary HTML is extracted as inert text with scripts and styles removed", () => {
    const api = load("selectionOnlineDictionary", {
        "../../package.json": config,
        "./diagnostics": diagnostics,
    });
    const parser = new DOMParser();
    for (const html of [
        '<b>word</b>&nbsp;&amp;释义<script>throw Error("execute")</script><style>secret</style>',
        '<b onclick="throw Error()">word</b>&nbsp;&amp;释义<SCRIPT src="https://evil.test/x">bad</SCRIPT>',
    ]) {
        assert.equal(api.dictionaryPlainText(html, parser), "word &释义");
    }
    assert.equal(
        api.dictionaryPlainText("&lt;script&gt;literal&lt;/script&gt;", parser),
        "<script>literal</script>",
    );
    assert.equal(
        api.dictionaryPlainText(
            '<img src="x" onerror="evil()"><b>safe',
            parser,
        ),
        "safe",
    );
    assert.equal(api.dictionaryPlainText(null, parser), "");
});

test("preference migration distinguishes new installs, implicit old defaults, explicit choices and restart", () => {
    for (const [reason, explicit, expected] of [
        [5, undefined, "youdao"],
        [7, undefined, "ecdict"],
        [undefined, undefined, "ecdict"],
        [5, "collins", "collins"],
        [7, "youdao", "youdao"],
    ]) {
        const prefs = new Map(
            explicit ? [["selectionDictionary", explicit]] : [],
        );
        const api = load(
            "selectionMigration",
            {
                "../../package.json": config,
                "../utils/prefs": {
                    getPref: (k) => prefs.get(k),
                    setPref: (k, v) => prefs.set(k, v),
                },
            },
            { Services: { prefs: { prefHasUserValue: () => !!explicit } } },
        );
        api.migrateSelectionPreferences(reason);
        assert.equal(prefs.get("selectionDictionary"), expected);
        api.migrateSelectionPreferences(5);
        assert.equal(prefs.get("selectionDictionary"), expected);
    }
});

test("SSE accepts split UTF-8, CRLF, multiline data, heartbeats and ignores obsolete IDs", () => {
    const { createSelectionStreamParser } = load("selectionStream");
    const frame = (kind, seq, other = {}) =>
        `event: ${kind}\r\ndata: ${JSON.stringify({ requestId: "current", seq, ...other })}\r\n\r\n`;
    const wire =
        frame("start", 1) +
        ": heartbeat\r\n\r\n" +
        frame("delta", 2, { text: "学习🧪" }) +
        frame("delta", 50, { requestId: "old", text: "wrong" }) +
        'event: done\r\ndata: {"requestId":"current",\r\ndata: "seq":3,"translation":"学习🧪"}\r\n\r\n';
    const bytes = new TextEncoder().encode(wire);
    for (let size = 1; size < 40; size++) {
        const chunks = [],
            parser = createSelectionStreamParser("current", (t) =>
                chunks.push(t),
            ),
            decoder = new TextDecoder();
        for (let i = 0; i < bytes.length; i += size)
            parser.feed(
                decoder.decode(bytes.slice(i, i + size), { stream: true }),
            );
        parser.feed(decoder.decode());
        assert.equal(parser.finish().translation, "学习🧪");
        assert.equal(chunks.join(""), "学习🧪");
    }
    const parser = createSelectionStreamParser("current", () => {});
    parser.feed(frame("start", 1) + frame("delta", 2, { text: "partial" }));
    assert.throws(() => parser.finish(), /未完成/);
});

function favorite(overrides = {}) {
    return {
        version: 1,
        id: "first",
        word: "learning",
        sourceLang: "en",
        targetLang: "zh-CN",
        meaning: "学习",
        source: "有道",
        original: "Learning",
        query: "learning",
        title: "Paper",
        attachmentKey: "ABCDEFGH",
        library: { type: "user" },
        pageIndex: 3,
        rects: [[1, 2, 3, 4]],
        createdAt: "2026-10-09",
        updatedAt: "2026-10-09",
        ...overrides,
    };
}
test("favorites serialize writes, dedupe only identical sources, recover after restart and merge without overwriting", async () => {
    const files = new Map();
    let fail = false;
    const globals = {
        PathUtils: { ...path, parent: path.dirname },
        IOUtils: {
            exists: async (p) => files.has(p),
            readUTF8: async (p) => {
                if (!files.has(p)) throw Error();
                return files.get(p);
            },
            makeDirectory: async () => {},
            writeUTF8: async (p, v) => {
                if (fail && !p.endsWith(".bak")) throw Error("disk full");
                files.set(p, v);
            },
        },
        Zotero: { DataDirectory: { dir: "/data" } },
    };
    const create = () =>
        load("selectionFavorites", { "../../package.json": config }, globals);
    let api = create();
    await Promise.all([
        api.saveFavorite(favorite()),
        api.saveFavorite(favorite({ id: "duplicate" })),
        api.saveFavorite(favorite({ id: "paper2", attachmentKey: "IJKLMNOP" })),
    ]);
    assert.equal((await api.listFavorites()).length, 2);
    api = create();
    assert.equal((await api.listFavorites()).length, 2);
    const imported = await api.importFavorites(
        JSON.stringify({
            version: 1,
            entries: [
                favorite({ meaning: "changed" }),
                favorite({ id: "new", pageIndex: 8 }),
            ],
        }),
    );
    assert.deepEqual(imported, { added: 1, conflicts: 1 });
    assert.equal((await api.listFavorites())[0].meaning, "学习");
    fail = true;
    await assert.rejects(() =>
        api.saveFavorite(favorite({ id: "failed", pageIndex: 9 })),
    );
    assert.equal((await api.listFavorites()).length, 3);
    fail = false;
    await api.saveFavorite(favorite({ meaning: "updated" }), true);
    assert.equal((await api.listFavorites())[0].meaning, "updated");
    await api.deleteFavorite("first");
    assert.equal((await api.listFavorites()).length, 2);
    const csv = await api.exportFavorites(true);
    assert.match(csv, /word,headword/);
    assert.throws(
        () =>
            api.parseFavorites(
                JSON.stringify({
                    version: 1,
                    entries: [favorite({ rects: [[1, 2, 3, "x"]] })],
                }),
            ),
        /坐标/,
    );
});

test("group-library excerpts link the exact PDF, keep printed labels and treat both texts as plain text", async () => {
    const window = new JSDOM("").window;
    const pdf = {
        libraryID: 37,
        key: "GROUPPDF",
        parentItem: { id: 1, key: "ARTICLE", getNotes: () => [] },
        isPDFAttachment: () => true,
    };
    let html;
    const api = load(
        "selectionNote",
        {
            "../utils/locale": {
                getString: (key, options) => options?.args.page ?? key,
            },
        },
        {
            Zotero: {
                getString: (key) => (key.endsWith("openingQMark") ? "“" : "”"),
                Items: { get: () => pdf, getAsync: async () => [] },
                URI: {
                    getItemURI: () =>
                        "http://zotero.org/groups/111/items/GROUPPDF",
                },
                Libraries: {
                    userLibraryID: 1,
                    get: () => ({ editable: true }),
                },
                Groups: {
                    getGroupIDFromLibraryID: (id) => {
                        assert.equal(id, 37);
                        return 111;
                    },
                },
                DB: { executeTransaction: (fn) => fn() },
                getMainWindow: () => window,
                Item: class {
                    addTag() {}
                    setNote(value) {
                        html = value;
                    }
                    async save() {}
                },
            },
        },
    );
    const excerpt = {
        original: "<b>exact</b> & other",
        query: "edited input",
        translation: "<img src=x> 样例\n第二行",
        position: {
            pageIndex: 2,
            rects: [[1, 2, 3, 4]],
            nextPageRects: [[5, 6, 7, 8]],
        },
        pageLabel: "S1",
    };
    await api.saveSelectionNote({ itemID: 12 }, excerpt);
    const doc = new JSDOM(html).window.document;
    const link = doc.querySelector("a");
    assert.equal(
        link.getAttribute("href"),
        "zotero://open-pdf/groups/111/items/GROUPPDF?page=3",
    );
    assert.equal(link.textContent, "S1");
    assert.equal(
        doc.querySelector(".highlight").textContent,
        "“" + excerpt.original + "”",
    );
    assert.deepEqual(
        JSON.parse(
            decodeURIComponent(
                doc.querySelector(".highlight").dataset.annotation,
            ),
        ).position,
        excerpt.position,
    );
    assert.ok(doc.body.textContent.includes("edited input"));
    assert.ok(doc.body.textContent.includes("<img src=x> 样例"));
    assert.ok(doc.querySelector("br"));
    assert.equal(doc.querySelector("img, b"), null);
    await assert.rejects(
        api.saveSelectionNote(
            { itemID: 12 },
            { ...excerpt, position: { pageIndex: -1 } },
        ),
        /location/,
    );
});

for (const active of [false, true]) {
    test(`open note ${active ? "at the current block" : "in the background"} waits for persistence and propagates editor errors`, async () => {
        const window = new JSDOM("").window;
        const pdf = {
            libraryID: 1,
            key: "SOURCE",
            parentItem: { id: 1, key: "ARTICLE", getNotes: () => [3] },
            isPDFAttachment: () => true,
        };
        const note = {
            id: 3,
            parentID: 1,
            libraryID: 1,
            isNote: () => true,
            hasTag: () => true,
        };
        let html = "<p>Unsaved draft</p>",
            finish,
            completed = false;
        const core = {
            view: {
                state: {
                    doc: {},
                    selection: {
                        to: 12,
                        $to: {
                            depth: 2,
                            after: (depth) => {
                                assert.equal(depth, 1);
                                return 20;
                            },
                        },
                    },
                },
            },
            insertHTML(position, value) {
                assert.equal(position, active ? 20 : -1);
                html += value;
                this.view.state.doc = {};
            },
        };
        const editor = {
            _item: note,
            _initPromise: Promise.resolve(),
            _isReadOnly: () => false,
            _ensureNoteCreated: async () => {},
            _iframeWindow: {
                wrappedJSObject: {
                    _currentEditorInstance: { _editorCore: core },
                    getDataSync: () => ({ html, state: { draft: true } }),
                },
            },
            _save: (data, skipDateModifiedUpdate) =>
                new Promise((resolve) => {
                    assert.equal(skipDateModifiedUpdate, false);
                    assert.ok(data.html.includes("Unsaved draft"));
                    assert.ok(data.html.includes("译文"));
                    finish = resolve;
                }),
        };
        window.Zotero_Tabs = { selectedID: "reader" };
        window.ZoteroContextPane = {
            collapsed: false,
            activeEditor: active
                ? { item: note, mode: "edit", getCurrentInstance: () => editor }
                : undefined,
        };
        const api = load(
            "selectionNote",
            {
                "../utils/locale": {
                    getString: () => "page",
                },
            },
            {
                Zotero: {
                    getMainWindow: () => window,
                    getString: () => '"',
                    Items: { get: () => pdf, getAsync: async () => [note] },
                    URI: {
                        getItemURI: () =>
                            "http://zotero.org/users/local/items/SOURCE",
                    },
                    Libraries: {
                        userLibraryID: 1,
                        get: () => ({ editable: true }),
                    },
                    Notes: { _editorInstances: [editor] },
                    DB: { executeTransaction: (fn) => fn() },
                },
            },
        );
        const reader = { itemID: 12, tabID: "reader", _window: window };
        const excerpt = {
            original: "Original sentence.",
            query: "Original sentence.",
            translation: "译文",
            position: { pageIndex: 2 },
        };
        const saving = api.saveSelectionNote(reader, excerpt).then(() => {
            completed = true;
        });
        for (let i = 0; i < 20 && !finish; i++) await Promise.resolve();
        assert.equal(typeof finish, "function");
        assert.equal(completed, false);
        finish();
        await saving;
        assert.equal(completed, true);
        core.insertHTML = () => {
            throw new RangeError("Invalid editor position");
        };
        await assert.rejects(
            api.saveSelectionNote(reader, excerpt),
            /Invalid editor position/,
        );
    });
}
