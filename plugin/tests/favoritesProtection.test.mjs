import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const file = path.join("/data", "test", "selection-favorites-v1.json");
const entry = {
    version: 1,
    id: "one",
    word: "attention",
    sourceLang: "en",
    targetLang: "zh-CN",
    meaning: "注意力",
    source: "test",
    original: "attention",
    query: "attention",
    title: "Paper",
    attachmentKey: "ABCDEFGH",
    library: { type: "user" },
    pageIndex: 0,
    createdAt: "2026-10-18",
    updatedAt: "2026-10-18",
};
const snapshot = (entries = [entry]) => JSON.stringify({ version: 1, entries });
function fixture(initial = []) {
    const files = new Map(initial);
    let fail = false;
    const api = {};
    const code = ts.transpileModule(
        fs.readFileSync(
            new URL("../src/modules/selectionFavorites.ts", import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        },
    ).outputText;
    new Function("require", "exports", "Zotero", "PathUtils", "IOUtils", code)(
        () => ({ config: { addonRef: "test" } }),
        api,
        { DataDirectory: { dir: "/data" } },
        { ...path, parent: path.dirname },
        {
            exists: async (p) => files.has(p),
            readUTF8: async (p) => {
                if (!files.has(p)) throw Error("missing");
                return files.get(p);
            },
            makeDirectory: async () => {},
            writeUTF8: async (p, value) => {
                if (fail && p === file) throw Error("disk full");
                files.set(p, value);
            },
        },
    );
    return {
        api,
        files,
        fail: () => {
            fail = true;
        },
    };
}
test("missing or damaged primary uses backup read-only and preserves both originals", async () => {
    for (const primary of [
        undefined,
        "broken JSON",
        snapshot([{ ...entry, pageIndex: -1 }]),
    ]) {
        const initial = [[file + ".bak", snapshot()]];
        if (primary !== undefined) initial.push([file, primary]);
        const f = fixture(initial);
        assert.equal((await f.api.listFavorites()).length, 1);
        await assert.rejects(
            f.api.saveFavorite({ ...entry, id: "two", word: "new" }),
            /收藏/,
        );
        assert.equal(f.files.get(file), primary);
        assert.equal(f.files.get(file + ".bak"), snapshot());
    }
});
test("only an entirely new library may start empty", async () => {
    const f = fixture();
    await f.api.saveFavorite(entry);
    assert.equal(JSON.parse(f.files.get(file)).entries.length, 1);
    const damaged = fixture([[file + ".bak", "broken backup"]]);
    await assert.rejects(damaged.api.saveFavorite(entry));
    assert.equal(damaged.files.get(file + ".bak"), "broken backup");
    assert.equal(damaged.files.has(file), false);
});

test("a valid primary remains readable when its backup is damaged, with writes paused", async () => {
    const f = fixture([
        [file, snapshot()],
        [file + ".bak", "damaged backup"],
    ]);
    assert.equal((await f.api.listFavorites()).length, 1);
    await assert.rejects(f.api.deleteFavorite("one"), /收藏/);
    assert.equal(f.files.get(file), snapshot());
    assert.equal(f.files.get(file + ".bak"), "damaged backup");
});
test("external edits and deletions after loading block writes without replacing either file", async () => {
    for (const target of [file, file + ".bak"]) {
        for (const changed of [
            undefined,
            snapshot([{ ...entry, id: "external" }]),
        ]) {
            const f = fixture([
                [file, snapshot()],
                [file + ".bak", snapshot([])],
            ]);
            await f.api.listFavorites();
            if (changed === undefined) f.files.delete(target);
            else f.files.set(target, changed);
            const before = [...f.files];
            await assert.rejects(
                f.api.saveFavorite({ ...entry, id: "two", word: "new" }),
                /收藏/,
            );
            assert.deepEqual([...f.files], before);
        }
    }
});
test("failed primary save retains validated backup and leaves cached library unchanged", async () => {
    const f = fixture([
        [file, snapshot()],
        [file + ".bak", snapshot([])],
    ]);
    await f.api.listFavorites();
    f.fail();
    await assert.rejects(
        f.api.saveFavorite({ ...entry, id: "two", word: "new" }),
    );
    assert.equal(f.files.get(file), snapshot());
    assert.equal(f.files.get(file + ".bak"), snapshot());
    assert.equal((await f.api.listFavorites()).length, 1);
});
