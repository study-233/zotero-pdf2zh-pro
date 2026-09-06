import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { URL } from "node:url";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

const source = fs.readFileSync(
    new URL("../src/modules/apiCompatibility.ts", import.meta.url),
    "utf8",
);
const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext },
}).outputText;
const { prepareApiForServer } = await import(
    `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`
);

test("new servers receive the selected protocol and nested options unchanged", () => {
    const api = {
        apiUrl: "https://gateway/v1/responses",
        apiProtocol: "responses",
        requestOptions: { reasoning: { effort: "low" } },
    };
    assert.deepEqual(
        prepareApiForServer(api, ["chat_completions", "responses"]),
        { api },
    );
});

test("old servers reject Responses and request options before task submission", () => {
    assert.throws(
        () =>
            prepareApiForServer({
                apiUrl: "https://gateway",
                apiProtocol: "responses",
            }),
        /升级/,
    );
    assert.throws(
        () =>
            prepareApiForServer({
                apiUrl: "https://gateway",
                requestOptions: { temperature: 0 },
            }),
        /升级/,
    );
});

test("auto mode on an old server gives a warning without mutating saved configuration", () => {
    const api = {
        apiUrl: "https://gateway/custom/v1/responses/",
        apiProtocol: "auto",
    };
    const prepared = prepareApiForServer(api);
    assert.equal(prepared.api.apiProtocol, "chat_completions");
    assert.equal(prepared.api.apiUrl, "https://gateway/custom/v1");
    assert.match(prepared.warning, /Chat Completions/);
    assert.equal(api.apiProtocol, "auto");
    assert.equal(api.apiUrl, "https://gateway/custom/v1/responses/");
});

test("legacy configurations retain their original behavior", () => {
    const api = { apiUrl: "https://gateway/v1" };
    assert.deepEqual(prepareApiForServer(api), { api });
});
