import fs from "node:fs";
import { URL } from "node:url";

// Task messages use single-line Fluent patterns with named arguments.
export function taskWindowMessage(locale = "zh-CN") {
    const source = fs.readFileSync(
        new URL(`../../addon/locale/${locale}/addon.ftl`, import.meta.url),
        "utf8",
    );
    const messages = new Map(
        [
            ...source.matchAll(
                /^((?:task-ui-|attachment-type-)[\w-]+) = (.+)$/gm,
            ),
        ].map((match) => [match[1], match[2]]),
    );
    return (key, options) => {
        if (!key.startsWith("task-ui-") && !key.startsWith("attachment-type-"))
            return key;
        if (!messages.has(key))
            throw new Error(`Missing ${locale} message: ${key}`);
        return messages.get(key).replace(/\{\s*\$(\w+)\s*\}/g, (_, name) => {
            if (options?.args?.[name] === undefined)
                throw new Error(`Missing ${key} argument: ${name}`);
            return options.args[name];
        });
    };
}
