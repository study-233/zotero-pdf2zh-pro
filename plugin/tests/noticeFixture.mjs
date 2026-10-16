import fs from "node:fs";
import { URL } from "node:url";
import ts from "typescript";

export function noticeModules(overrides = {}, globals = {}) {
    const cache = new Map(Object.entries(overrides));
    return function load(name) {
        if (cache.has(name)) return cache.get(name);
        const source = fs.readFileSync(
            new URL(
                `../src/modules/${name.replace(/^\.\//, "")}.ts`,
                import.meta.url,
            ),
            "utf8",
        );
        const code = ts.transpileModule(source, {
            compilerOptions: {
                module: ts.ModuleKind.CommonJS,
                target: ts.ScriptTarget.ES2022,
            },
        }).outputText;
        const exports = {};
        cache.set(name, exports);
        new Function(
            "require",
            "exports",
            ...Object.keys(globals),
            code + `\n//# sourceURL=${name}.fixture.js`,
        )(load, exports, ...Object.values(globals));
        return exports;
    };
}
