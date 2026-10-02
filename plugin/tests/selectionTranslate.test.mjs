import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const formatting = {};
new Function(
    "exports",
    ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/selectionFormatting.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: { module: ts.ModuleKind.CommonJS },
        },
    ).outputText,
)(formatting);

function fixture() {
    const auxiliaryRequests = [];
    const requests = [],
        cards = [],
        timers = new Map(),
        handlers = [],
        watches = [],
        logs = [],
        fallbacks = [];
    const state = {
        entries: [],
        autoDictionary: false,
        auxiliaryRespond: undefined,
        provider: "bing",
        selectionApiKey: "",
        model: true,
        profileReads: 0,
        contextReads: 0,
        contextText: "page context",
        dictionary: undefined,
        dictionaryFail: false,
        createFail: false,
        renderFail: false,
        respond: async () => ({ matched: false }),
    };
    let timerId = 0,
        notify;
    const exports = {};
    const imports = {
        "./selectionFormatting": formatting,
        "../../package.json": { config: { addonID: "test-addon" } },
        "../utils/prefs": {
            getPref: (name) =>
                name === "selectionApiKey"
                    ? state.selectionApiKey
                    : name === "selectionAutoDictionary"
                      ? state.autoDictionary
                      : state.provider,
        },
        "./pdf2zhHelper": {
            PDF2zhHelperFactory: {
                getServerConfig: (includeProfile = true, profileKey) => {
                    if (includeProfile) state.profileReads++;
                    if (includeProfile && profileKey === "deleted")
                        throw new Error("所选划词模型已删除，请重新选择。");
                    return {
                        serverUrl: "http://localhost:8890",
                        sourceLang: "en",
                        targetLang: "zh-CN",
                        apiConfig:
                            includeProfile && state.model
                                ? { model: profileKey || "test-model" }
                                : null,
                    };
                },
            },
        },
        "./selectionContext": {
            getSelectionContext: async () => {
                state.contextReads++;
                return state.contextText;
            },
        },
        "./selectionDictionary": {
            lookupDictionary: async () => {
                if (state.dictionaryFail) throw new Error("broken resource");
                return state.dictionary;
            },
        },
        "./glossaryStore": { loadGlossaryEntries: () => state.entries },
        "./diagnostics": { recordDiagnostic: (event) => logs.push(event) },
        "./selectionEvents": {
            watchSelectionDocuments: (_doc, _owner, callbacks) => {
                watches.push(callbacks);
                return () => {
                    callbacks.cleaned = true;
                };
            },
        },
        "./selectionRequest": {
            createSelectionRequest: () => {
                const request = {
                    aborted: false,
                    abort() {
                        this.aborted = true;
                    },
                    async post(url, body, timeoutMs) {
                        if (
                            url.endsWith("selection-capabilities") ||
                            body.allowGenerate === false
                        ) {
                            auxiliaryRequests.push({
                                url,
                                body,
                                timeoutMs,
                                request,
                            });
                            if (state.auxiliaryRespond)
                                return state.auxiliaryRespond({ url, body });
                            return {
                                ok: true,
                                data: url.endsWith("selection-capabilities")
                                    ? { selectionLearning: true }
                                    : { status: "miss" },
                            };
                        }
                        requests.push({ url, body, timeoutMs, request });
                        const data = await state.respond({ url, body });
                        return { ok: data?.ok !== false, data };
                    },
                };
                return request;
            },
        },
        "./selectionPane": {
            registerSelectionPane() {},
            unregisterSelectionPane() {},
        },
        "./selectionPopup": {
            selectionElement: (doc, tag) => doc.createElement(tag),
        },
        "./selectionView": {
            createSelectionView: (
                _reader,
                _doc,
                _anchor,
                selected,
                action,
                onClose,
            ) => {
                if (state.createFail) throw new Error("constructor failed");
                const popup = {
                    card: { contains: (node) => node === "inside" },
                    alive: true,
                    pinned: false,
                    selected,
                    action,
                    updates: [],
                    setLearning(value) {
                        this.learning = value;
                    },
                    loading(text) {
                        this.updates.push(text);
                    },
                    render(result) {
                        if (state.renderFail) throw new Error("render failed");
                        this.updates.push(result);
                    },
                    updateSelection(text, kind) {
                        this.selected = text;
                        this.action = kind;
                    },
                    close() {
                        this.alive = false;
                        onClose();
                    },
                };
                cards.push(popup);
                return popup;
            },
        },
    };
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/selectionTranslate.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    new Function(
        "require",
        "exports",
        "Zotero",
        "IOUtils",
        "setTimeout",
        "clearTimeout",
        code,
    )(
        (name) => imports[name],
        exports,
        {
            Reader: {
                _readers: [],
                registerEventListener: (_type, h) => handlers.push(h),
                unregisterEventListener: () => handlers.pop(),
            },
            Items: {
                get: () => ({ getFilePathAsync: async () => "/paper.pdf" }),
            },
            getMainWindow: () => ({ document: {} }),
            Notifier: {
                registerObserver: (observer) => {
                    notify = observer.notify;
                    return "observer";
                },
                unregisterObserver() {},
            },
        },
        {
            stat: async () => ({ size: 10, lastModified: 1 }),
            computeHexDigest: async () => "sha256",
        },
        (callback, delay) => {
            const id = ++timerId;
            timers.set(id, { callback, delay });
            return id;
        },
        (id) => timers.delete(id),
    );
    const reader = { type: "pdf", itemID: 7, tabID: "tab1" };
    const doc = {
        querySelectorAll: () => [],
        getElementById: (id) =>
            fallbacks.find((n) => n.id === id && !n.removed),
        body: { append: (node) => fallbacks.push(node) },
        createElement: (tag) => ({
            tag,
            style: {},
            children: [],
            textContent: "",
            setAttribute() {},
            append(...nodes) {
                this.children.push(...nodes);
            },
            remove() {
                this.removed = true;
            },
            addEventListener() {},
        }),
    };
    function select(text, currentReader = reader) {
        let anchor;
        handlers[0]({
            reader: currentReader,
            doc,
            params: {
                annotation: {
                    text,
                    position: { pageIndex: 4, rects: [1, 2, 3, 4] },
                },
            },
            append: (node) => {
                anchor = node;
            },
        });
        return anchor;
    }
    async function flush() {
        for (let i = 0; i < 30; i++) await Promise.resolve();
    }
    async function advance() {
        for (const [id, task] of [...timers]) {
            assert.equal(task.delay, 400);
            timers.delete(id);
            task.callback();
        }
        await flush();
    }
    exports.registerSelectionTranslation();
    return {
        ...exports,
        requests,
        auxiliaryRequests,
        cards,
        state,
        select,
        advance,
        flush,
        timers,
        watches,
        logs,
        fallbacks,
        notify: (...args) => notify(...args),
    };
}
const memory = {
    matched: true,
    source: "Source paragraph",
    translation: "已有译文",
    matchType: "exact",
    page: 5,
};
const last = (f) => f.cards.at(-1)?.updates.at(-1);

test("selection automatically opens after 400 ms without action buttons; repeated hooks deduplicate", async () => {
    const f = fixture();
    f.state.respond = async () => memory;
    const anchor = f.select("Selected sentence");
    assert.equal(anchor.tag, "span");
    assert.equal(anchor.children.length, 0);
    assert.equal(f.cards.length, 0);
    assert.equal(f.requests.length, 0);
    f.select("Selected sentence");
    assert.equal(f.timers.size, 1);
    await f.advance();
    assert.equal(f.requests.length, 1);
    assert.equal(last(f).kind, "translation");
    f.select("Selected sentence");
    await f.advance();
    assert.equal(f.requests.length, 1);
});

test("rapid selection changes only query the last selection", async () => {
    const f = fixture();
    f.state.respond = async () => memory;
    f.select("A");
    f.select("B");
    f.select("C");
    await f.advance();
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].body.text, "C");
});

test("offline dictionary and language-specific glossary require no server", async () => {
    const f = fixture();
    f.state.dictionary = {
        text: "a. 无条件的",
        phonetic: "phonetic",
        origin: "离线词典",
    };
    f.select("Unconditional");
    await f.advance();
    assert.equal(last(f).text, "a. 无条件的");
    f.state.entries = [
        { source: "physical devices", target: "wrong", tgt_lng: "ja" },
        { source: "physical devices", target: "物理设备", tgt_lng: "zh-CN" },
    ];
    f.select("PHYSICAL\n devices");
    await f.advance();
    assert.equal(last(f).text, "物理设备");
    assert.equal(f.requests.length, 0);
});

test("exact and incomplete memory keep types and never use external translation", async () => {
    const f = fixture();
    f.state.respond = async () => ({ ...memory, formattingIncomplete: true });
    f.select("unknown");
    await f.advance();
    assert.equal(last(f).kind, "translation");
    assert.equal(last(f).incomplete, true);
    f.state.respond = async () => ({ ...memory, matchType: "exact" });
    f.select("Source paragraph");
    await f.advance();
    assert.equal(last(f).kind, "translation");
    assert.ok(f.requests.every((r) => r.url.endsWith("translation-lookup")));
});

test("miss automatically requests concise translation and accepts cache; HTTP errors are sanitized", async () => {
    const f = fixture();
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { matched: false }
            : { translation: "新译文", provider: "openai", cached: true };
    f.select("new sentence");
    await f.advance();
    assert.equal(f.requests.length, 2);
    assert.equal(f.requests[1].body.mode, "translate");
    assert.equal(f.requests[1].timeoutMs, 50000);
    assert.equal(last(f).origin, "缓存译文");
    for (const code of ["provider_timeout", "invalid_config", "empty_output"]) {
        f.state.respond = async ({ url }) =>
            url.endsWith("translation-lookup")
                ? { matched: false }
                : { ok: false, code, message: "secret-provider-body" };
        f.select(code);
        await f.advance();
        assert.equal(last(f).kind, "error");
        assert.doesNotMatch(last(f).text, /secret-provider-body/);
    }
});

test("memory failure is not a miss; offline glossary still works", async () => {
    const f = fixture();
    f.state.respond = async () => {
        throw new Error("offline");
    };
    f.select("many selected words in sentence");
    await f.advance();
    assert.equal(last(f).kind, "error");
    assert.equal(f.requests.length, 1);
    f.state.entries = [
        {
            source: "many selected words in sentence two",
            target: "术语",
            tgt_lng: "zh-CN",
        },
    ];
    f.select("many selected words in sentence two");
    await f.advance();
    assert.equal(last(f).text, "术语");
    assert.ok(f.requests.every((r) => r.url.endsWith("translation-lookup")));
});

test("broken dictionary may reuse memory but never silently spends tokens on a dictionary failure", async () => {
    const f = fixture();
    f.state.dictionaryFail = true;
    f.state.respond = async () => memory;
    f.select("word");
    await f.advance();
    assert.equal(last(f).text, "已有译文");
    f.state.respond = async () => ({ matched: false });
    f.select("word2");
    await f.advance();
    assert.equal(last(f).kind, "error");
    assert.match(last(f).text, /词典读取失败/);
    assert.ok(f.requests.every((r) => r.url.endsWith("translation-lookup")));
});

test("close during request aborts wait, ignores late result and suppresses same selection until new physical interaction", async () => {
    const f = fixture();
    let finish;
    f.state.respond = () => new Promise((r) => (finish = r));
    f.select("A");
    await f.advance();
    const card = f.cards[0];
    card.close();
    assert.ok(f.requests[0].request.aborted);
    finish(memory);
    await f.flush();
    assert.ok(card.updates.every((x) => typeof x === "string"));
    f.select("A");
    await f.advance();
    assert.equal(f.cards.length, 1);
    f.watches[0].input({ target: "outside" });
    f.state.respond = async () => memory;
    f.select("A");
    await f.advance();
    assert.equal(f.cards.length, 2);
    assert.equal(last(f).text, "已有译文");
});

test("selection B invalidates A without stacking cards; native menu rerender doesn't cancel pending result", async () => {
    const f = fixture();
    let finish;
    f.state.respond = () => new Promise((r) => (finish = r));
    f.select("A");
    await f.advance();
    f.select("A");
    assert.equal(f.requests.length, 1);
    f.select("B");
    assert.ok(f.requests[0].request.aborted);
    f.state.respond = async () => ({ ...memory, translation: "B" });
    await f.advance();
    finish({ ...memory, translation: "old A" });
    await f.flush();
    assert.equal(last(f).text, "B");
    assert.equal(f.cards.length, 1);
});

test("pending selection is cancelled by outside click, Escape or tab change", async () => {
    for (const mode of ["outside", "escape", "tab"]) {
        const f = fixture();
        f.select("A");
        if (mode === "outside") f.watches[0].input({ target: "outside" });
        if (mode === "escape") f.watches[0].escape();
        if (mode === "tab") f.notify("select", "tab", ["other"]);
        await f.advance();
        assert.equal(f.requests.length, 0);
        assert.equal(f.cards.length, 0);
    }
});

test("pin keeps one card and next selection updates it; Escape always closes", async () => {
    const f = fixture();
    f.state.respond = async () => memory;
    f.select("A");
    await f.advance();
    f.cards[0].pinned = true;
    f.watches[0].dismiss({ target: "outside" });
    assert.equal(f.cards[0].alive, true);
    f.watches[0].input({ target: "inside" });
    f.select("A");
    await f.advance();
    assert.equal(f.requests.length, 1);
    f.select("B");
    await f.advance();
    assert.equal(f.cards.length, 1);
    assert.equal(f.cards[0].selected, "B");
    assert.equal(f.cards[0].pinned, true);
    f.watches[0].escape();
    assert.equal(f.cards[0].alive, false);
});

test("constructor and rendering failures leave a nonempty closeable fallback without changing native menu", async () => {
    for (const mode of ["createFail", "renderFail"]) {
        const f = fixture();
        f.state[mode] = true;
        f.state.respond = async () => memory;
        const anchor = f.select("A");
        await f.advance();
        assert.equal(anchor.textContent, "");
        assert.ok(f.fallbacks.at(-1).textContent);
        assert.equal(f.fallbacks.at(-1).children.at(-1).textContent, "关闭");
        f.watches[0].escape();
        assert.equal(f.fallbacks.at(-1).removed, true);
        assert.ok(f.cards.every((c) => !c.alive));
        assert.ok(f.logs.some((e) => e.endsWith("failed")));
    }
});

test("malformed successful server response is an error, not a blank result", async () => {
    const f = fixture();
    f.state.respond = async () => ({ ...memory, translation: "" });
    f.select("A");
    await f.advance();
    assert.equal(last(f).kind, "error");
    assert.equal(f.requests.length, 1);
});

test("Reader disposal and plugin shutdown cancel and unregister", async () => {
    const f = fixture();
    f.select("A");
    f.watches[0].dispose();
    await f.advance();
    assert.equal(f.cards.length, 0);
    assert.ok(f.watches[0].cleaned);
    f.select("B");
    f.unregisterSelectionTranslation();
    await f.advance();
    assert.equal(f.requests.length, 0);
    assert.ok(f.watches.every((w) => w.cleaned));
});

test("contained memory is folded reference, never a sentence result; free requests omit model and context", async () => {
    const f = fixture();
    f.state.model = false;
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { ...memory, matchType: "contained", formattingIncomplete: true }
            : { translation: "仅所选句子", provider: "bing" };
    f.select("A key challenge is how to train models.");
    await f.advance();
    assert.equal(last(f).text, "仅所选句子");
    assert.equal(last(f).reference.text, "已有译文");
    assert.equal(last(f).reference.incomplete, true);
    const body = f.requests[1].body;
    assert.equal(body.selectionProvider, "bing");
    assert.equal(body.memoryPolicy, "exact");
    assert.equal(body.context, "");
    assert.equal(body.service, "bing");
    assert.equal("llm_api" in body, false);
    assert.equal(f.state.profileReads, 1);
    assert.equal(f.state.contextReads, 0);
});

test("legacy MyMemory preference now requests Bing without model credentials", async () => {
    const f = fixture();
    f.state.provider = "mymemory";
    f.state.model = false;
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { matched: false }
            : { translation: "自注意力", provider: "bing", cached: false };
    f.select("self-attention");
    await f.advance();
    assert.equal(f.requests[1].body.selectionProvider, "bing");
    assert.equal(f.requests[1].body.service, "bing");
    assert.equal("llm_api" in f.requests[1].body, false);
    assert.equal(last(f).text, "自注意力");
    assert.equal(last(f).origin, "必应 · 在线翻译");
    assert.equal(f.state.profileReads, 1);
});

test("free failure retains folded paragraph reference and never falls back to profile", async () => {
    const f = fixture();
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { ...memory, matchType: "contained" }
            : { ok: false, code: "provider_quota" };
    f.select("A key challenge is how to train models.");
    await f.advance();
    assert.equal(last(f).kind, "error");
    assert.equal(last(f).reference.text, "已有译文");
    assert.equal(f.requests.length, 2);
    assert.equal(f.state.profileReads, 1);
});

test("manual profile selection uses existing config; settings change cancels pending work", async () => {
    const f = fixture();
    f.state.provider = "profile";
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { matched: false }
            : { translation: "模型选句", provider: "openai" };
    f.select("A key challenge is how to train models.");
    await f.advance();
    assert.equal(f.requests[1].body.llm_api.model, "test-model");
    assert.equal(f.requests[1].body.context, "page context");
    f.select("Another selected sentence.");
    f.resetSelectionTranslation();
    await f.advance();
    assert.equal(f.requests.length, 2);
    assert.equal(f.cards[0].alive, false);
});

test("old server contained response is an upgrade error instead of falsely aligned text", async () => {
    const f = fixture();
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { matched: false }
            : {
                  ...memory,
                  matchType: "contained",
                  provider: "translation-memory",
              };
    f.select("A key challenge is how to train models.");
    await f.advance();
    assert.equal(last(f).kind, "error");
    assert.match(last(f).text, /更新服务端/);
});

test("selection, dictionary, context and refresh use the independent model", async () => {
    const f = fixture();
    f.state.selectionApiKey = "selection-model";
    f.state.provider = "profile";
    const bodies = [];
    const request = {
        post: async (url, body) => {
            if (url.endsWith("selection-capabilities"))
                return { ok: true, data: { selectionLearning: true } };
            bodies.push(body);
            return {
                ok: true,
                data: {
                    translation: "result",
                    model: "selection-model",
                    ...(body.mode === "dictionary"
                        ? { entry: personalEntry }
                        : {}),
                },
            };
        },
    };
    for (const options of [
        {},
        { mode: "dictionary" },
        { mode: "context" },
        { refresh: true },
        { mode: "context", refresh: true },
    ])
        await f.translateSelection(
            request,
            "paper",
            "bank",
            1,
            "context",
            options,
        );
    assert.equal(bodies.length, 5);
    assert.ok(bodies.every((body) => body.llm_api.model === "selection-model"));
    f.state.provider = "bing";
    await f.translateSelection(request, "paper", "sentence", 1, "context");
    assert.equal(bodies.at(-1).llm_api, undefined);
    assert.equal(bodies.at(-1).context, "");
    f.state.provider = "profile";
    f.state.selectionApiKey = "deleted";
    await assert.rejects(
        f.translateSelection(request, "paper", "sentence", 1, "context"),
        /已删除/,
    );
    assert.equal(bodies.length, 6);
});

const personalEntry = {
    headword: "bank",
    senses: [
        {
            chinese: "银行；河岸",
            pos: "n.",
            examples: [{ english: "a river bank", chinese: "河岸" }],
        },
    ],
    usage: "根据搭配区分金融机构与河岸。",
    aiGenerated: true,
};
const personalResult = {
    translation: "银行；河岸",
    entry: personalEntry,
    provider: "personal-dictionary",
    model: "model-a",
    saved: true,
};

test("missing word is enriched once, context waits for click, and context refresh preserves the dictionary", async () => {
    const f = fixture();
    f.state.autoDictionary = true;
    f.state.respond = async ({ body }) =>
        body.mode === "dictionary"
            ? personalResult
            : { translation: "这里指河岸", model: "model-a", saved: true };
    f.select("bank");
    await f.advance();
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].body.mode, "dictionary");
    assert.equal(f.requests[0].body.context, "");
    assert.equal(f.state.contextReads, 0);
    assert.equal(last(f).origin, "AI 补充 · model-a");
    const card = f.cards[0];
    card.learning.context.onAction();
    await f.flush();
    assert.equal(f.requests[1].body.mode, "context");
    assert.equal(f.requests[1].body.context, "page context");
    assert.equal(card.learning.context.text, "这里指河岸");
    assert.equal(last(f).text.includes("银行"), true);
    card.learning.context.onAction();
    await f.flush();
    assert.equal(f.requests[2].body.cachePolicy, "refresh");
});

test("saved personal dictionary is read without current model and without generation", async () => {
    const f = fixture();
    f.state.model = false;
    f.state.autoDictionary = true;
    f.state.auxiliaryRespond = async ({ url, body }) => ({
        ok: true,
        data: url.endsWith("selection-capabilities")
            ? { selectionLearning: true }
            : body.mode === "dictionary"
              ? { ...personalResult, cached: true }
              : { status: "miss" },
    });
    f.select("bank");
    await f.advance();
    assert.equal(f.requests.length, 0);
    assert.equal(f.state.profileReads, 1);
    assert.equal(last(f).origin, "AI 补充 · model-a · 本地词典");
});

test("dictionary hit shows no supplement and makes no backend request", async () => {
    const f = fixture();
    f.state.dictionary = { text: "原始词典内容", origin: "ECDICT 离线词典" };
    f.state.autoDictionary = true;
    f.select("bank");
    await f.advance();
    assert.equal(f.cards[0].learning.refreshHidden, true);
    f.cards[0].learning.onRefresh();
    await f.flush();
    assert.equal(last(f).text, "原始词典内容");
    assert.equal(f.requests.length, 0);
    assert.equal(f.auxiliaryRequests.length, 0);
});

test("refresh failure retains old result; duplicate refresh is suppressed", async () => {
    const f = fixture();
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? memory
            : { translation: "重翻结果", saved: true };
    f.select("A complete sentence from the paper.");
    await f.advance();
    const before = last(f).text;
    let finish;
    f.state.respond = () =>
        new Promise((resolve) => {
            finish = resolve;
        });
    f.cards[0].learning.onRefresh();
    f.cards[0].learning.onRefresh();
    await f.flush();
    assert.equal(
        f.requests.filter((r) => r.body.cachePolicy === "refresh").length,
        1,
    );
    assert.equal(last(f).text, before);
    finish({ ok: false, code: "provider_error" });
    await f.flush();
    assert.equal(last(f).text, before);
    assert.match(f.cards[0].learning.error, /失败|不可用/);
    assert.equal(f.cards[0].learning.busy, false);
});

test("refresh result is read from persistent cache before old full-document memory", async () => {
    const f = fixture();
    f.state.auxiliaryRespond = async ({ url }) => ({
        ok: true,
        data: url.endsWith("selection-capabilities")
            ? { selectionLearning: true }
            : {
                  translation: "已保存的重翻结果",
                  provider: "bing",
                  saved: true,
                  cached: true,
              },
    });
    f.select("A complete sentence from the paper.");
    await f.advance();
    assert.equal(last(f).text, "已保存的重翻结果");
    assert.equal(f.requests.length, 0);
});

test("closing during context request aborts and ignores late response", async () => {
    const f = fixture();
    f.state.dictionary = { text: "通用释义", origin: "ECDICT" };
    f.select("bank");
    await f.advance();
    let finish;
    f.state.respond = () =>
        new Promise((resolve) => {
            finish = resolve;
        });
    const card = f.cards[0];
    card.learning.context.onAction();
    await f.flush();
    const request = f.requests[0].request;
    card.close();
    assert.equal(request.aborted, true);
    finish({ translation: "迟到的解释" });
    await f.flush();
    assert.equal(card.learning.context.text, undefined);
    assert.equal(card.alive, false);
});

test("older backend cannot silently satisfy refresh or trigger an enrichment request", async () => {
    const f = fixture();
    f.state.dictionary = { text: "词典结果", origin: "ECDICT" };
    f.state.auxiliaryRespond = async () => ({ ok: false, data: {} });
    f.select("bank");
    await f.advance();
    f.cards[0].learning.context.onAction();
    await f.flush();
    assert.equal(f.requests.length, 0);
    assert.match(f.cards[0].learning.context.error, /升级/);
    assert.equal(last(f).text, "词典结果");
});

test("unavailable context does not invoke a model or change generic meaning", async () => {
    const f = fixture();
    f.state.dictionary = { text: "银行；河岸", origin: "词典" };
    f.state.contextText = "bank";
    f.select("bank");
    await f.advance();
    f.cards[0].learning.context.onAction();
    await f.flush();
    assert.equal(f.requests.length, 0);
    assert.match(f.cards[0].learning.context.error, /上下文/);
    assert.equal(last(f).text, "银行；河岸");
});

test("failed automatic dictionary generation does not silently issue a second model translation", async () => {
    const f = fixture();
    f.state.autoDictionary = true;
    f.state.provider = "profile";
    f.state.respond = async () => ({ ok: false, code: "provider_timeout" });
    f.select("bank");
    await f.advance();
    assert.equal(f.requests.length, 1);
    assert.equal(f.requests[0].body.mode, "dictionary");
    assert.equal(last(f).kind, "error");
});

test("missing model falls back to Bing basic translation, never a dictionary entry", async () => {
    const f = fixture();
    f.state.autoDictionary = true;
    f.state.provider = "profile";
    f.state.model = false;
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { matched: false }
            : { translation: "银行", provider: "bing", saved: true };
    f.select("bank");
    await f.advance();
    const generated = f.requests.find((r) => r.url.endsWith("translate-text"));
    assert.equal(generated.body.selectionProvider, "bing");
    assert.equal(generated.body.mode, "translate");
    assert.equal("llm_api" in generated.body, false);
    assert.equal(last(f).kind, "translation");
});

test("credential-free dictionary lookup never serializes a null model configuration", async () => {
    const f = fixture();

    f.state.model = false;
    f.state.auxiliaryRespond = async ({ url, body }) => {
        if (url.endsWith("selection-capabilities"))
            return { ok: true, data: { selectionLearning: true } };
        const wire = JSON.parse(JSON.stringify(body));
        assert.equal(wire.mode, "dictionary");
        assert.equal(wire.allowGenerate, false);
        assert.equal("llm_api" in wire, false);
        return { ok: true, data: { ...personalResult, cached: true } };
    };
    f.select("mechanism");
    await f.advance();
    assert.match(last(f).text, /银行/);
    assert.equal(f.cards[0].learning.error, undefined);
    assert.equal(f.state.profileReads, 1);
});

test("empty native popup after scrolling preserves pending and completed results", async () => {
    const f = fixture();
    f.state.respond = async () => memory;
    f.select("word");
    f.select("");
    await f.advance();
    assert.equal(last(f).text, "已有译文");
    f.select("");
    assert.equal(f.cards[0].alive, true);
    f.select("word");
    await f.advance();
    assert.equal(f.requests.length, 1);
});

test("pinned and sidebar views keep pending requests across outside clicks", async () => {
    for (const property of ["pinned", "docked"]) {
        const f = fixture();
        let finish;
        f.state.respond = () =>
            new Promise((resolve) => {
                finish = resolve;
            });
        f.select("word");
        await f.advance();
        f.cards[0][property] = true;
        f.watches[0].input({ target: "outside" });
        f.select("");
        if (property === "docked") {
            f.watches[0].escape();
            f.notify("select", "tab", ["other"]);
        }
        assert.equal(f.requests[0].request.aborted, false);
        finish(memory);
        await f.flush();
        assert.equal(last(f).text, "已有译文");
        assert.equal(f.cards[0].alive, true);
    }
});

test("unconfigured profile falls back to Bing for sentences and refresh, without context or credentials", async () => {
    const f = fixture();
    f.state.provider = "profile";
    f.state.model = false;
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { matched: false }
            : { translation: "基础译文", provider: "bing" };
    f.select("A sentence with no configured model.");
    await f.advance();
    f.cards[0].learning.onRefresh();
    f.cards[0].learning.context.onAction();
    await f.flush();
    assert.equal(f.cards[0].learning.context.actionLabel, undefined);
    assert.match(f.cards[0].learning.context.error, /配置模型/);
    assert.equal(f.state.contextReads, 0);
    assert.ok(
        f.requests
            .filter((r) => r.body.mode === "translate")
            .every(
                (r) =>
                    r.body.selectionProvider === "bing" &&
                    !r.body.llm_api &&
                    !r.body.context,
            ),
    );
});

test("disabled automatic dictionary uses basic translation even with a model", async () => {
    const f = fixture();
    f.state.autoDictionary = false;
    f.state.provider = "profile";
    f.state.respond = async ({ url }) =>
        url.endsWith("translation-lookup")
            ? { matched: false }
            : { translation: "基础译文", provider: "openai" };
    f.select("unlistedword");
    await f.advance();
    assert.ok(f.requests.every((r) => r.body.mode !== "dictionary"));
    f.cards[0].learning.onRefresh();
    await f.flush();
    assert.equal(f.requests.at(-1).body.mode, "translate");
});

test("structured context is validated and failures preserve dictionary and prior context", async () => {
    const f = fixture();
    f.state.dictionary = { text: "银行；河岸", origin: "词典" };
    f.state.respond = async () => ({
        translation: "legacy fallback",
        contextMeaning: {
            pos: "N-COUNT",
            meaning: "河岸",
            explanation: "前文描述了河流。",
        },
    });
    f.select("bank");
    await f.advance();
    const card = f.cards[0];
    card.learning.context.onAction();
    await f.flush();
    assert.equal(card.learning.context.text, "n. [C] 河岸\n前文描述了河流。");
    assert.equal(card.learning.context.contextMeaning.pos, "n. [C]");
    f.state.respond = async () => ({
        translation: "raw provider result",
        contextMeaning: { meaning: "", explanation: 1 },
    });
    card.learning.context.onAction();
    await f.flush();
    assert.match(card.learning.context.error, /语境格式/);
    assert.equal(card.learning.context.contextMeaning.meaning, "河岸");
    assert.equal(last(f).text, "银行；河岸");
});

test("personal dictionary usage is separate from failure notices", async () => {
    const f = fixture();
    f.state.autoDictionary = true;
    f.state.respond = async () => ({ ...personalResult, saved: false });
    f.select("bank");
    await f.advance();
    assert.equal(last(f).usage, personalEntry.usage);
    assert.equal(last(f).notice, "本次释义未能保存到本地。");
});
