import fs from "node:fs";
import { URL } from "node:url";
import { JSDOM } from "jsdom";

export function taskWindowScript() {
    const source = fs.readFileSync(
        new URL("../../addon/content/taskManager.xhtml", import.meta.url),
        "utf8",
    );
    const dom = new JSDOM(source, { contentType: "application/xhtml+xml" });
    try {
        const script = dom.window.document.querySelector("script");
        if (!script) throw new Error("Task window script is missing");
        return script.textContent;
    } finally {
        dom.window.close();
    }
}
