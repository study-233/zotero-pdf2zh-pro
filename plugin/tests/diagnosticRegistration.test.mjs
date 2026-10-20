import assert from "node:assert/strict";
import fs from "node:fs";
import { URL } from "node:url";
import test from "node:test";
import ts from "typescript";
import { diagnosticContract } from "./diagnosticContractFixture.mjs";

function events(expression) {
    if (ts.isStringLiteral(expression)) return [expression.text];
    if (ts.isConditionalExpression(expression))
        return [
            ...events(expression.whenTrue),
            ...events(expression.whenFalse),
        ];
    if (ts.isTemplateExpression(expression))
        return [
            expression.head.text +
                expression.templateSpans
                    .map((span) => "*" + span.literal.text)
                    .join(""),
        ];
    if (
        ts.isBinaryExpression(expression) &&
        expression.operatorToken.kind === ts.SyntaxKind.PlusToken
    )
        return events(expression.left).flatMap((left) =>
            events(expression.right).map((right) => left + right),
        );
    return ["*"];
}

test("new plugin diagnostic events and fields require a shared contract entry", () => {
    const directory = new URL("../src/modules/", import.meta.url);
    const fields = new Set([
        ...diagnosticContract.numeric,
        ...diagnosticContract.flags,
        ...Object.keys(diagnosticContract.enums),
        ...diagnosticContract.identifiers,
        ...diagnosticContract.labels,
        ...diagnosticContract.strings,
        ...diagnosticContract.timestamps,
        ...diagnosticContract.containers,
        ...Object.keys(diagnosticContract.countMaps),
        ...Object.keys(diagnosticContract.opaqueIdentifiers),
        ...Object.keys(diagnosticContract.identifierLists),
        "failedParagraphs",
    ]);
    for (const name of fs
        .readdirSync(directory)
        .filter((name) => name.endsWith(".ts"))) {
        const source = ts.createSourceFile(
            name,
            fs.readFileSync(new URL(name, directory), "utf8"),
            ts.ScriptTarget.Latest,
            true,
        );
        function visit(node) {
            if (
                ts.isCallExpression(node) &&
                node.expression.getText(source) === "recordDiagnostic"
            ) {
                for (const event of events(node.arguments[0]))
                    assert.ok(
                        event.includes("*")
                            ? diagnosticContract.eventPatterns.includes(event)
                            : Object.hasOwn(diagnosticContract.events, event),
                        `Register diagnostic event ${event} in ${name}`,
                    );
                const payload = node.arguments[1];
                if (payload && ts.isObjectLiteralExpression(payload)) {
                    for (const property of payload.properties) {
                        if (
                            !property.name ||
                            ts.isComputedPropertyName(property.name)
                        )
                            continue;
                        const key = property.name
                            .getText(source)
                            .replace(/^["']|["']$/g, "");
                        assert.ok(
                            fields.has(key),
                            `Register diagnostic field ${key} in ${name}`,
                        );
                    }
                }
            }
            ts.forEachChild(node, visit);
        }
        visit(source);
    }
});
