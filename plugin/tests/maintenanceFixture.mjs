import fs from "node:fs";
import { URL } from "node:url";
import ts from "typescript";
const source = fs.readFileSync(
    new URL("../src/modules/diagnostics.ts", import.meta.url),
    "utf8",
);
const compiled = ts.transpileModule(source, {
    compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
    },
}).outputText;
const exports = {};
new Function("require", "exports", compiled)(
    () => ({ config: {}, version: "test" }),
    exports,
);
export const maintenanceFetch = exports.maintenanceFetch;
export const diagnosticFetch = exports.diagnosticFetch;
