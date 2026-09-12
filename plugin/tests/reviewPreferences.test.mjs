import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import ts from "typescript";

const prefs = new Map();
let failKey;
globalThis.__reviewPrefsTest = {
    getPref: (key) => prefs.get(key),
    setPref: (key, value) => {
        if (key === failKey) throw new Error("write failed");
        prefs.set(key, value);
    },
};
const source = fs.readFileSync(
    new URL("../src/modules/reviewPreferences.ts", import.meta.url),
    "utf8",
);
const compiled = ts
    .transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.ESNext },
    })
    .outputText.replace(/^import .*;$/gm, "");
const { migrateReviewPreference } = await import(
    `data:text/javascript;base64,${Buffer.from(
        "const {getPref,setPref}=globalThis.__reviewPrefsTest;\n" + compiled,
    ).toString("base64")}`
);

test("first install and old enabled preferences opt out once, then retain manual opt-in", () => {
    for (const previous of [undefined, true, false]) {
        prefs.clear();
        if (previous !== undefined) prefs.set("semanticReview", previous);
        migrateReviewPreference();
        assert.equal(prefs.get("semanticReview"), false);
        assert.equal(prefs.get("reviewPreferenceMigrationVersion"), 1);
        prefs.set("semanticReview", true);
        migrateReviewPreference();
        assert.equal(prefs.get("semanticReview"), true);
    }
});

test("failed preference or marker writes leave migration retryable", () => {
    for (const key of ["semanticReview", "reviewPreferenceMigrationVersion"]) {
        prefs.clear();
        prefs.set("semanticReview", true);
        failKey = key;
        try {
            assert.throws(migrateReviewPreference, /write failed/);
            assert.equal(prefs.has("reviewPreferenceMigrationVersion"), false);
        } finally {
            failKey = undefined;
        }
        migrateReviewPreference();
        assert.equal(prefs.get("semanticReview"), false);
        assert.equal(prefs.get("reviewPreferenceMigrationVersion"), 1);
    }
});

test("migration runs before preferences and menus are registered", () => {
    const hooks = fs.readFileSync(
        new URL("../src/hooks.ts", import.meta.url),
        "utf8",
    );
    assert.ok(
        hooks.indexOf("migrateReviewPreference();") <
            hooks.indexOf("registerPrefs();"),
    );
    assert.ok(
        hooks.indexOf("migrateReviewPreference();") <
            hooks.indexOf("onMainWindowLoad(win)"),
    );
});
