import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import test from "node:test";
import { fileURLToPath, URL } from "node:url";
import ts from "typescript";

const pluginRoot = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
);
const readJson = (relativePath) =>
    JSON.parse(fs.readFileSync(path.join(pluginRoot, relativePath), "utf8"));

const pkg = readJson("package.json");
const expected = {
    name: "zotero-pdf2zh-pro",
    version: pkg.version,
    id: "zotero-pdf2zh-pro@study-233",
    updateUrl:
        "https://github.com/study-233/zotero-pdf2zh-pro/releases/latest/download/update.json",
    strictMinVersion: "8.0",
    strictMaxVersion: "10.0.*",
};

function assertNoUpdateUrl(value, label) {
    if (Array.isArray(value)) {
        for (const item of value) assertNoUpdateUrl(item, label);
        return;
    }
    if (!value || typeof value !== "object") return;

    for (const [key, child] of Object.entries(value)) {
        assert.notEqual(
            key,
            "update_url",
            label + " must not contain update_url",
        );
        assertNoUpdateUrl(child, label);
    }
}

function assertManifestIdentity(manifest, label, templates = false) {
    const zotero = manifest.applications?.zotero;
    assert.ok(zotero, label + " must contain applications.zotero");

    assert.deepEqual(
        {
            name: templates
                ? manifest.name.replace("__addonName__", expected.name)
                : manifest.name,
            version: templates
                ? manifest.version.replace("__buildVersion__", expected.version)
                : manifest.version,
            id: templates
                ? zotero.id.replace("__addonID__", expected.id)
                : zotero.id,
            updateUrl: zotero.update_url,
            strictMinVersion: zotero.strict_min_version,
            strictMaxVersion: zotero.strict_max_version,
        },
        expected,
        label + " identity or compatibility range does not match package.json",
    );
}

function readXpiEntry(entry) {
    const xpi = path.join("build", expected.name + ".xpi");
    const extractor =
        process.platform === "win32"
            ? {
                  command: path.join(
                      process.env.SystemRoot,
                      "System32",
                      "tar.exe",
                  ),
                  args: ["-xOf", xpi, entry],
              }
            : {
                  command: "unzip",
                  args: ["-p", xpi, entry],
              };
    const result = spawnSync(extractor.command, extractor.args, {
        cwd: pluginRoot,
        encoding: "utf8",
        maxBuffer: 8 * 1024 * 1024,
    });
    assert.equal(
        result.status,
        0,
        "Archive reader could not read " +
            entry +
            " from " +
            xpi +
            ": " +
            (result.stderr || result.error?.message),
    );
    return result.stdout;
}

test("plugin release artifacts remain consistent", () => {
    assert.equal(pkg.name, expected.name);
    assert.equal(pkg.config.addonName, expected.name);
    assert.equal(pkg.config.addonID, expected.id);

    const manifest = readJson("addon/manifest.json");
    assertManifestIdentity(manifest, "source manifest", true);
    const builtManifest = readJson("build/addon/manifest.json");
    const xpiManifest = JSON.parse(readXpiEntry("manifest.json"));

    assertManifestIdentity(builtManifest, "built manifest");
    assertManifestIdentity(xpiManifest, "XPI manifest");
    assert.deepEqual(xpiManifest, builtManifest);
    const xpi = fs.readFileSync(
        path.join(pluginRoot, "build", expected.name + ".xpi"),
    );
    const expectedHash =
        "sha512:" + crypto.createHash("sha512").update(xpi).digest("hex");

    for (const relativePath of [
        "build/update.json",
        "build/update-beta.json",
    ]) {
        const updateManifest = readJson(relativePath);
        assertNoUpdateUrl(updateManifest, relativePath);
        assert.deepEqual(Object.keys(updateManifest.addons), [expected.id]);

        const updates = updateManifest.addons[expected.id].updates;
        assert.equal(
            updates.length,
            1,
            relativePath + " must contain one update",
        );
        const update = updates[0];
        assert.equal(update.version, expected.version);
        assert.equal(update.update_hash, expectedHash);
        assert.deepEqual(update.applications?.zotero, {
            strict_min_version: expected.strictMinVersion,
            strict_max_version: expected.strictMaxVersion,
        });

        const updateUrl = new URL(update.update_link);
        assert.equal(
            path.posix.basename(updateUrl.pathname),
            expected.name + ".xpi",
        );
        assert.ok(updateUrl.pathname.includes("/v" + expected.version + "/"));
    }
});

test("final XPI dynamic preference IDs resolve in both packaged locales", async () => {
    const namespace = pkg.config.addonRef;
    const xhtml = readXpiEntry("content/preferences.xhtml");
    assert.ok(xhtml.includes(`href="${namespace}-preferences.ftl"`));
    const script = ts.createSourceFile(
        "packaged.js",
        readXpiEntry(`content/scripts/${namespace}.js`),
        ts.ScriptTarget.Latest,
        true,
        ts.ScriptKind.JS,
    );
    const helpers = new Map();
    const helperNames = ["getLocaleID", "localizePackNode", "preferenceText"];
    const dynamicKeys = new Set([
        ...["computing", "building", "physics", "environment", "medicine"].map(
            (id) => `pref-pack-${id}`,
        ),
        ...[
            "downloading",
            "failed",
            "update",
            "installed",
            "not-downloaded",
        ].map((state) => `pref-pack-state-${state}`),
    ]);
    function inspect(node) {
        if (
            ts.isFunctionDeclaration(node) &&
            helperNames.includes(node.name?.text)
        ) {
            assert.equal(
                helpers.has(node.name.text),
                false,
                "Ambiguous packaged localization helper",
            );
            helpers.set(node.name.text, node.getText(script));
        }
        if (
            ts.isStringLiteral(node) &&
            /^pref-(pack|glossary|server|quality)-/.test(node.text)
        ) {
            dynamicKeys.add(node.text);
        }
        ts.forEachChild(node, inspect);
    }
    inspect(script);
    assert.deepEqual([...helpers.keys()].sort(), [...helperNames].sort());
    for (const locale of ["zh-CN", "en-US"]) {
        const ftl = readXpiEntry(
            `locale/${locale}/${namespace}-preferences.ftl`,
        );
        const messages = new Map(
            [...ftl.matchAll(/^([\w-]+)[ \t]*=[ \t]*(.*)$/gm)].map(
                ([, id, value]) => [id, value.trim()],
            ),
        );
        const formatValue = async (id) => {
            assert.ok(
                messages.get(id),
                `${locale}: missing or empty packaged Fluent ID ${id}`,
            );
            return messages.get(id);
        };
        // Execute the shipped helpers, not copies of the source implementation.
        const runtime = new Function(
            "config",
            "addon",
            `${[...helpers.values()].join("\n")}\nreturn {localizePackNode, preferenceText};`,
        )(pkg.config, {
            data: {
                prefs: { window: { document: { l10n: { formatValue } } } },
            },
        });
        for (const key of dynamicKeys) {
            const attributes = new Map();
            runtime.localizePackNode(
                {
                    setAttribute: (name, value) => attributes.set(name, value),
                    removeAttribute: (name) => attributes.delete(name),
                },
                key,
                { count: 2, percent: 50, version: pkg.version, size: 10 },
            );
            const id = attributes.get("data-l10n-id");
            assert.equal(id, `${namespace}-${key}`);
            await formatValue(id);
            assert.equal(JSON.parse(attributes.get("data-l10n-args")).count, 2);
        }
        for (const key of [
            "pref-glossary-import",
            "pref-glossary-invalid-encoding",
        ]) {
            assert.equal(
                await runtime.preferenceText(key),
                messages.get(`${namespace}-${key}`),
            );
        }
    }
});
