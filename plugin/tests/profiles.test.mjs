import { URL } from "node:url";
import { Buffer } from "node:buffer";
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const compile = (name) =>
    ts.transpileModule(
        fs.readFileSync(
            new URL(`../src/modules/${name}.ts`, import.meta.url),
            "utf8",
        ),
        {
            compilerOptions: { module: ts.ModuleKind.ESNext },
        },
    ).outputText;
const asModule = (code) =>
    import(
        `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
    );
const model = await asModule(compile("llmApiManager"));
const prefs = new Map();
globalThis.__profileTests = {
    ...model,
    getPref: (key) => prefs.get(key),
    setPref: (key, value) => prefs.set(key, value),
};
const store = await asModule(
    "const {getPref, setPref, migrateProfiles, selectedProfile} = globalThis.__profileTests;\n" +
        compile("profileStore").replace(/^import .*;$/gm, ""),
);
const api = (key, service = "openai", activate = false) => ({
    key,
    service,
    activate,
    model: "same-model",
    apiUrl: `https://${key}.invalid/custom/v1`,
    apiKey: "test-key",
    requestOptions: { reasoning: { effort: "low" } },
});

test("migration picks the legacy service's active row and preserves all credentials/options", () => {
    const legacy = [api("a", "openai", true), api("b", "自定义", true)];
    const result = model.migrateProfiles(legacy, "自定义");
    assert.equal(result.selectedApiKey, "b");
    assert.equal(result.profiles[1].service, "openai");
    assert.equal(result.profiles[1].name, "自定义");
    assert.equal(result.profiles[1].needsTest, true);
    assert.equal(result.profiles[1].apiUrl, legacy[1].apiUrl);
    assert.equal(result.profiles[1].apiKey, "test-key");
    assert.deepEqual(
        result.profiles[1].requestOptions,
        legacy[1].requestOptions,
    );
    assert.equal(result.profiles[1].apiProtocol, "chat_completions");
    assert.ok(!("activate" in result.profiles[1]));
    assert.equal(legacy[1].service, "自定义");
});

test("ambiguous or unmatched old activation never selects another provider", () => {
    for (const entries of [
        [api("a"), api("b")],
        [api("a", "openai", true), api("b", "openai", true)],
        [api("a", "gemini", true)],
    ]) {
        assert.equal(
            model.migrateProfiles(entries, "openai").selectedApiKey,
            "",
        );
    }
});

test("built-in default survives migration, incomplete unknown engine needs editing", () => {
    assert.equal(
        model.migrateProfiles([], "siliconflowfree").profiles[0].service,
        "siliconflowfree",
    );
    const result = model.migrateProfiles(
        [{ ...api("a", "unknown", true), apiUrl: "" }],
        "unknown",
    );
    assert.equal(result.profiles[0].service, "unknown");
    assert.equal(result.profiles[0].needsTest, true);
});

test("migration backs up once, persisted selection survives reload, deletion clears only current ID", () => {
    prefs.clear();
    const original = JSON.stringify([api("a", "openai", true), api("b")]);
    prefs.set("llmApis", original);
    prefs.set("service", "openai");
    store.loadProfiles();
    assert.equal(
        JSON.parse(prefs.get("llmApisLegacyBackup")).llmApis,
        original,
    );
    prefs.set("selectedApiKey", "b");
    assert.equal(store.getSelectedProfile().key, "b");
    assert.equal(store.getSelectedProfile().key, "b");
    store.removeProfile("a");
    assert.equal(store.getSelectedProfile().key, "b");
    store.removeProfile("b");
    assert.equal(prefs.get("selectedApiKey"), "");
    assert.equal(store.getSelectedProfile(), null);
    assert.equal(store.loadProfiles().length, 0);
    assert.equal(
        JSON.parse(prefs.get("llmApisLegacyBackup")).llmApis,
        original,
    );
});

test("malformed preferences are never overwritten or marked migrated", () => {
    prefs.clear();
    prefs.set("llmApis", "broken");
    assert.throws(() => store.loadProfiles(), /无法读取/);
    assert.equal(prefs.get("llmApis"), "broken");
    assert.equal(prefs.has("profileSchemaVersion"), false);
});

test("selection uses ID even for identical models and returns isolated nested data", () => {
    const profiles = [api("a"), api("b")];
    assert.notEqual(
        model.profileLabel(profiles[0]),
        model.profileLabel(profiles[1]),
    );
    const snapshot = model.selectedProfile(profiles, "a");
    profiles[0].requestOptions.reasoning.effort = "high";
    assert.equal(snapshot.requestOptions.reasoning.effort, "low");
    assert.equal(model.selectedProfile(profiles, "missing"), null);
});

globalThis.__profileTests.getSelectedProfile = store.getSelectedProfile;
const { PDF2zhHelperFactory: helper } = await asModule(
    "const {getPref, getSelectedProfile, SERVICE_NAMES} = globalThis.__profileTests;\n" +
        compile("pdf2zhHelper").replace(/^import .*;$/gm, ""),
);
test("batch request construction keeps the captured profile after selection and edits", () => {
    prefs.clear();
    prefs.set("profileSchemaVersion", 1);
    store.saveProfiles([api("a"), api("b")]);
    prefs.set("selectedApiKey", "a");
    const config = helper.getServerConfig();
    prefs.set("selectedApiKey", "b");
    store.saveProfiles([{ ...api("a"), apiKey: "changed" }, api("b")]);
    for (const fileName of ["first.pdf", "second.pdf"]) {
        const body = helper.buildTaskRequestBody(
            { fileName, base64: "AA==" },
            config,
        );
        assert.equal(body.llm_api.key, "a");
        assert.equal(body.llm_api.apiKey, "test-key");
        assert.equal(body.service, "openai");
    }
    assert.equal(helper.getServerConfig().apiConfig.key, "b");
    prefs.set("selectedApiKey", "deleted");
    assert.throws(
        () =>
            helper.buildTaskRequestBody(
                { fileName: "none.pdf" },
                helper.getServerConfig(),
            ),
        /选择翻译配置/,
    );
});

const requests = [];
const http = {
    get: async () => ({
        data: {
            supportedApiProtocols: ["auto", "chat_completions", "responses"],
        },
    }),
    post: async (url, body) => {
        requests.push({ url, body });
        return {
            data: {
                liveTest: { ok: true },
                resolvedProtocol: "responses",
                models: ["model-a"],
            },
        };
    },
    isAxiosError: (error) => !!error?.isAxiosError,
};
globalThis.__profileTests.axios = http;
const compatibility = await asModule(compile("apiCompatibility"));
globalThis.__profileTests.prepareApiForServer =
    compatibility.prepareApiForServer;
const client = await asModule(
    "const {axios, getPref, prepareApiForServer} = globalThis.__profileTests;\n" +
        compile("profileApiClient").replace(/^import .*;$/gm, ""),
);
test("testing a draft forwards its credentials/options without changing saved selection", async () => {
    prefs.clear();
    prefs.set("new_serverip", "http://localhost:8890/");
    prefs.set("selectedApiKey", "a");
    const draft = { ...api("unsaved"), apiProtocol: "responses" };
    assert.match(await client.testProfile(draft), /API 测试成功.*Responses/);
    assert.equal(requests.at(-1).url, "http://localhost:8890/validate-config");
    assert.deepEqual(requests.at(-1).body.llm_api, draft);
    assert.equal(prefs.get("selectedApiKey"), "a");
    assert.deepEqual(await client.fetchProfileModels(draft), ["model-a"]);
});
test("old servers without discovery fall back to manual model entry", async () => {
    http.post = async () => {
        throw { isAxiosError: true, response: { status: 404 } };
    };
    await assert.rejects(
        client.fetchProfileModels(api("a")),
        /升级服务端或手动填写/,
    );
});
test("failed live tests are not called successful and never show a supplied secret", async () => {
    http.post = async () => ({
        data: { liveTest: { ok: false, message: "bad test-key" } },
    });
    await assert.rejects(
        client.testProfile(api("a")),
        (error) =>
            !error.message.includes("test-key") &&
            error.message.includes("已隐藏"),
    );
});
