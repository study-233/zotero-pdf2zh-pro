import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { URL } from "node:url";
import vm from "node:vm";
import { noticeModules } from "./noticeFixture.mjs";
import { taskWindowScript } from "./helpers/taskWindowScript.mjs";

const { errorNotice } = noticeModules()("./userNotice");
const script = taskWindowScript();

for (const locale of ["zh-CN", "en-US"]) {
    test(`${locale}: real task error mapping explains validation failures and clears after repair`, () => {
        const resource = fs.readFileSync(
            new URL(`../addon/locale/${locale}/addon.ftl`, import.meta.url),
            "utf8",
        );
        const messages = new Map(
            [...resource.matchAll(/^([\w-]+) = (.+)$/gm)].map((m) => [
                m[1],
                m[2],
            ]),
        );
        const t = (key) => {
            assert.ok(messages.has(key), `Missing ${locale} message: ${key}`);
            return messages.get(key);
        };
        const ui = vm.createContext({
            document: { addEventListener() {} },
            window: { addEventListener() {} },
            notice: (error) => errorNotice(error, t),
        });
        vm.runInContext(script, ui);
        vm.runInContext("api = {errorNotice: notice};", ui);
        const labels = {
            unchanged_translation: "notice-error-unchanged-translation",
            target_language_missing: "notice-error-translation-language",
            placeholder_mismatch: "notice-error-translation-placeholders",
        };
        for (const [validationCode, key] of Object.entries(labels)) {
            // The production API is present, and structured codes must survive
            // without relying on a legacy free-text reason.
            const task = {
                status: "incomplete",
                error: "未完成，剩余 1 段，可补译",
                translationSummary: { failed: 1 },
                failedParagraphs: [
                    { errorType: "InvalidTranslation", validationCode },
                ],
                errorDiagnostics: [],
                importState: "imported",
            };
            const before = JSON.stringify(task);
            assert.equal(ui.getErrorSummary(task), t(key));
            assert.equal(JSON.stringify(task), before);
            assert.equal(
                ui.getErrorSummary({
                    status: "completed",
                    importState: "imported",
                }),
                "",
            );
        }
        for (const [message, key] of [
            [
                "unchanged_translation → InvalidTranslation",
                labels.unchanged_translation,
            ],
            [
                "target_language_missing → InvalidTranslation",
                labels.target_language_missing,
            ],
            [
                "placeholder_mismatch → InvalidTranslation",
                labels.placeholder_mismatch,
            ],
            ["InvalidTranslation", "notice-error-translation-validation"],
        ]) {
            const text = ui.getErrorSummary({
                status: "failed",
                error: message,
            });
            assert.equal(text, t(key));
            assert.doesNotMatch(
                text,
                /InvalidTranslation|unchanged_translation|placeholder_mismatch/,
            );
        }
        assert.equal(
            ui.getErrorSummary({ status: "failed", error: "UnknownFailure" }),
            t("notice-error-unknown"),
        );
        assert.equal(
            ui.getErrorSummary({
                status: "incomplete",
                importState: "failed",
                importError: "UnknownFailure",
                failedParagraphs: [{ validationCode: "unchanged_translation" }],
            }),
            t("notice-error-import"),
        );
    });
}
