import fs from "node:fs";
import { URL } from "node:url";
import assert from "node:assert/strict";
import test from "node:test";
import ts from "typescript";
const source = fs.readFileSync(
    new URL("../src/modules/backendInstallation.ts", import.meta.url),
    "utf8",
);
const js = ts.transpileModule(source, {
    compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
    },
}).outputText;
function fixture({ windows = true, owner = "", custom = "" } = {}) {
    const exports = {},
        paths = [];
    const prefs = { managedEngineRoot: custom };
    const root = "D:/译文";
    const registry = {
        open() {},
        close() {},
        readStringValue() {
            return root;
        },
    };
    const io = {
        async readUTF8(p) {
            paths.push(p);
            return p.endsWith(".json")
                ? JSON.stringify({ root: custom, owner: "a".repeat(48) })
                : p.endsWith("owner")
                  ? owner
                  : "1.9.8";
        },
        async exists(p) {
            return p.endsWith(".json") ? !!custom : true;
        },
    };
    new Function(
        "require",
        "exports",
        "Zotero",
        "PathUtils",
        "Services",
        "Cc",
        "Ci",
        "IOUtils",
        js,
    )(
        () => ({ getPref: (k) => prefs[k] }),
        exports,
        { isWin: windows },
        { join: (...p) => p.join("/"), isAbsolute: () => true },
        { dirsvc: { get: () => ({ path: "profile" }) } },
        {
            "@mozilla.org/windows-registry-key;1": {
                createInstance: () => registry,
            },
        },
        { nsIWindowsRegKey: {} },
        io,
    );
    return { identify: exports.identifyBackend, paths };
}
const local = "http://localhost:8890";
test("remote and development addresses never inspect installations", async () => {
    const { identify, paths } = fixture();
    for (const url of [
        "https://host",
        "http://localhost:8891",
        "http://localhost:8890/other",
    ])
        assert.equal((await identify(url)).kind, "external");
    assert.deepEqual(paths, []);
});
test("ownership distinguishes Windows data, foreign listeners and competing stopped instances", async () => {
    const { identify } = fixture({ owner: "here" });
    assert.equal(
        (await identify(local, { managedOwner: "here" })).kind,
        "managed",
    );
    assert.equal(
        (await identify(local, { managedOwner: "other" })).kind,
        "external",
    );
    assert.equal(
        (await identify(local, { workspace: { path: "D:\\译文\\data" } })).kind,
        "windows",
    );
    assert.equal(
        (await identify(local, { workspace: { path: "D:/elsewhere/data" } }))
            .kind,
        "external",
    );
    assert.equal((await identify(local)).kind, "conflict");
    assert.equal((await fixture().identify(local)).kind, "windows");
});
test("missing custom disk never silently becomes a fresh install", async () => {
    assert.equal(
        (await fixture({ windows: false, custom: "E:/engine" }).identify(local))
            .kind,
        "missing",
    );
    assert.equal(
        (await fixture({ windows: false }).identify(local)).kind,
        "uninstalled",
    );
});
