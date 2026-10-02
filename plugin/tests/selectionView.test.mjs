import assert from "node:assert/strict";
import fs from "node:fs";
import { URL } from "node:url";
import test from "node:test";
import ts from "typescript";

const code = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/selectionView.ts", import.meta.url),
        "utf8",
    ),
    {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2022,
        },
    },
).outputText;

function fixture(prefs = new Map()) {
    const cards = [];
    let paneAvailable = true;
    const host = { ownerDocument: {}, replaceChildren() {} };
    const exports = {};
    const imports = {
        "../utils/prefs": {
            getPref: (key) => prefs.get(key),
            setPref: (key, value) => prefs.set(key, value),
        },
        "./selectionPane": {
            emptySelectionPane() {},
            openSelectionPane: async () => (paneAvailable ? host : undefined),
            watchSelectionPane: () => () => {},
        },
        "./selectionPopup": {
            createSelectionPopup: (
                _doc,
                anchor,
                _text,
                _action,
                onClose,
                id,
                _degraded,
                options,
            ) => {
                const rect = {
                    ...(options.position || anchor),
                    width: 360,
                    height: 250,
                    ...options.size,
                };
                const style = {
                    removeProperty(key) {
                        delete this[key];
                    },
                };
                const popup = {
                    options,
                    rect,
                    pinned: options.pinned,
                    card: {
                        id,
                        style,
                        querySelectorAll: () => [],
                        querySelector: () => null,
                        getBoundingClientRect: () => rect,
                    },
                    loading() {},
                    render(value) {
                        this.result = value;
                    },
                    setLearning() {},
                    showNotice() {},
                    updateSelection(_text, _action, next) {
                        if (!this.pinned && !options.host)
                            Object.assign(rect, next);
                    },
                    destroy() {
                        this.destroyed = true;
                    },
                    close: onClose,
                    drag(left, top) {
                        Object.assign(rect, { left, top });
                        options.onMove({ left, top });
                    },
                    pin(value) {
                        this.pinned = value;
                        options.onMove(rect);
                        options.onPinChange(value);
                    },
                };
                cards.push(popup);
                return popup;
            },
        },
    };
    new Function("require", "exports", code)((name) => imports[name], exports);
    return {
        prefs,
        cards,
        host,
        setPaneAvailable: (value) => {
            paneAvailable = value;
        },
        open: (id = "reader") =>
            exports.createSelectionView(
                { tabID: id },
                {},
                { left: 100, top: 200 },
                "word",
                "lookup",
                () => {},
                id,
                () => {},
            ),
        flush: async () => {
            for (let i = 0; i < 12; i++) await Promise.resolve();
        },
    };
}

test("pin, drag and size persist across close, another PDF and a fresh module instance", () => {
    const f = fixture();
    const first = f.open();
    assert.equal(first.pinned, false);
    f.cards.at(-1).pin(true);
    f.cards.at(-1).drag(340, 80);
    f.cards.at(-1).options.onResize({ width: 480, height: 320 });
    first.close();
    const fresh = fixture(f.prefs);
    const second = fresh.open("another-pdf");
    assert.equal(second.pinned, true);
    assert.deepEqual(fresh.cards.at(-1).rect, {
        left: 340,
        top: 80,
        width: 480,
        height: 320,
    });
    second.updateSelection("next", "lookup", { left: 10, top: 10 });
    assert.equal(fresh.cards.at(-1).rect.left, 340);
    fresh.cards.at(-1).pin(false);
    second.updateSelection("unpinned", "lookup", { left: 20, top: 30 });
    assert.equal(fresh.cards.at(-1).rect.left, 20);
    second.close();
    fresh.open();
    assert.equal(fresh.cards.at(-1).rect.left, 100);
    assert.equal(fresh.prefs.get("selectionPopupPinned"), false);
});

test("sidebar survives clearing and reload; floating round trip restores the last geometry", async () => {
    const f = fixture();
    const first = f.open();
    const result = { kind: "translation", text: "result", origin: "test" };
    first.render(result);
    f.cards.at(-1).pin(true);
    f.cards.at(-1).drag(280, 50);
    f.cards.at(-1).options.onSwitch();
    await f.flush();
    assert.equal(first.docked, true);
    assert.deepEqual(f.cards.at(-1).result, result);
    f.cards.at(-1).options.onClear();
    assert.equal(f.prefs.get("selectionDisplayMode"), "sidebar");
    const fresh = fixture(f.prefs);
    const second = fresh.open("other");
    assert.equal(second.card.style.visibility, "hidden");
    await fresh.flush();
    assert.equal(second.docked, true);
    assert.equal(second.card.style.visibility, undefined);
    fresh.cards.at(-1).options.onSwitch();
    await fresh.flush();
    assert.equal(second.docked, false);
    assert.equal(second.pinned, true);
    assert.equal(fresh.cards.at(-1).rect.left, 280);
    assert.equal(fresh.cards.at(-1).rect.top, 50);
});

test("unavailable sidebar falls back visibly without overwriting saved preferences", async () => {
    const f = fixture(
        new Map([
            ["selectionDisplayMode", "sidebar"],
            ["selectionPopupPinned", true],
            ["selectionPopupLeft", 240],
            ["selectionPopupTop", 90],
        ]),
    );
    f.setPaneAvailable(false);
    const view = f.open();
    await f.flush();
    assert.equal(view.docked, false);
    assert.equal(view.card.style.visibility, undefined);
    assert.equal(f.cards.at(-1).rect.left, 240);
    assert.equal(f.prefs.get("selectionDisplayMode"), "sidebar");
});

test("closing while sidebar restoration is pending never remounts a card", async () => {
    const f = fixture(new Map([["selectionDisplayMode", "sidebar"]]));
    const view = f.open();
    view.close();
    await f.flush();
    assert.equal(view.alive, false);
    assert.equal(f.cards.length, 1);
    assert.equal(f.cards[0].destroyed, true);
});
