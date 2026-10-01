import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import test from "node:test";
import ts from "typescript";

const source = fs.readFileSync(
    new URL("../src/modules/developmentLibrary.ts", import.meta.url),
    "utf8",
);
const code = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText;

function harness({ formal = false, hash = "digest" } = {}) {
    const items = [];
    const writes = [];
    const runtime = "/development";
    let imports = 0;
    const collection = { id: 1, name: "开发测试", getChildItems: () => items };
    const context = {
        exports: {},
        Services: {
            dirsvc: {
                get: () => ({
                    path: formal ? "/formal" : runtime + "/profile",
                }),
            },
        },
        Ci: { nsIFile: {} },
        PathUtils: { join: path.posix.join },
        IOUtils: {
            exists: async () => true,
            computeHexDigest: async () => hash,
            readUTF8: async () =>
                JSON.stringify({
                    papers: [
                        {
                            id: "attention",
                            arxiv: "1706.03762v7",
                            title: "Attention",
                            sha256: "digest",
                        },
                    ],
                }),
            writeUTF8: async (_path, value) => writes.push(JSON.parse(value)),
        },
        Zotero: {
            getMainWindow: () => ({ setInterval: () => 1, clearInterval() {} }),
            DataDirectory: { dir: runtime + "/library" },
            Libraries: { userLibraryID: 1 },
            Collections: { getByLibrary: () => [collection] },
            Attachments: {
                importFromFile: async () => {
                    imports++;
                    const tags = [];
                    const item = {
                        setField() {},
                        addTag: (tag) => tags.push({ tag }),
                        getTags: () => tags,
                        saveTx: async () => {},
                    };
                    items.push(item);
                    return item;
                },
            },
        },
    };
    vm.runInNewContext(code, context);
    return {
        run: () => context.exports.prepareDevelopmentLibrary(runtime),
        writes,
        imports: () => imports,
    };
}

test("developer samples are imported once across plugin reloads", async () => {
    const h = harness();
    await h.run();
    await h.run();
    assert.equal(h.imports(), 1);
    assert.equal(h.writes[0].imported, 1);
    assert.equal(h.writes[1].imported, 0);
});

test("formal profile is rejected before any library write", async () => {
    const h = harness({ formal: true });
    await assert.rejects(h.run(), /目录不匹配/);
    assert.equal(h.imports(), 0);
    assert.equal(h.writes.length, 0);
});

test("tampered samples are rejected before attachment import", async () => {
    const h = harness({ hash: "changed" });
    await assert.rejects(h.run(), /校验失败/);
    assert.equal(h.imports(), 0);
});
