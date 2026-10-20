import fs from "node:fs";
import { Buffer } from "node:buffer";
import ts from "typescript";
import { URL } from "node:url";

const compiled = ts.transpileModule(
    fs.readFileSync(
        new URL("../src/modules/diagnosticContract.ts", import.meta.url),
        "utf8",
    ),
    { compilerOptions: { module: ts.ModuleKind.ESNext } },
).outputText;
export const { diagnosticContract } = await import(
    "data:text/javascript;base64," + Buffer.from(compiled).toString("base64")
);
