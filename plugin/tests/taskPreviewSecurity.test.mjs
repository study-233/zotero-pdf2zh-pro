import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import process from "node:process";
import { fileURLToPath, URL } from "node:url";
import vm from "node:vm";
import test from "node:test";
import { JSDOM } from "jsdom";

test("offline task preview accepts known scenarios without reflecting arbitrary input", async (t) => {
    const child = spawn(
        process.execPath,
        [
            fileURLToPath(
                new URL("../../scripts/preview_task_ui.cjs", import.meta.url),
            ),
            "0",
        ],
        { stdio: ["ignore", "pipe", "pipe"], windowsHide: true },
    );
    t.after(async () => {
        if (child.exitCode === null) {
            const exited = once(child, "exit");
            child.kill();
            await exited;
        }
    });
    const address = await new Promise((resolve, reject) => {
        let output = "";
        child.stdout.on("data", (chunk) => {
            output += chunk;
            const match = output.match(
                /Offline task preview: (http:\/\/127\.0\.0\.1:\d+)\//,
            );
            if (match) resolve(match[1]);
        });
        child.stderr.on("data", (chunk) => {
            output += chunk;
        });
        child.once("error", reject);
        child.once("exit", () => reject(new Error(output)));
    });
    for (const [scenario, expectedCount] of [
        ["active", 1],
        ["empty", 0],
        ["missing", 1],
        ["review", 1],
        ["mixed", 4],
        ["history", 4],
        ["]]></script><script>window.injected=true</script>", 1],
        ["]]><SCRIPT>window.injected=true</SCRIPT>", 1],
        [
            "]]></script><script>window.injected=true</script><script><![CDATA[",
            1,
        ],
    ]) {
        await t.test(scenario, async () => {
            const url = new URL("/task.xhtml", address);
            url.searchParams.set("scenario", scenario);
            const response = await globalThis.fetch(url);
            assert.equal(response.status, 200);
            const markup = await response.text();
            const dom = new JSDOM(markup, {
                contentType: "application/xhtml+xml",
            });
            try {
                const scripts = dom.window.document.querySelectorAll("script");
                assert.equal(scripts.length, 2);
                const window = {};
                vm.runInNewContext(scripts[0].textContent, { window });
                assert.equal(
                    window.arguments[0].getTasks().length,
                    expectedCount,
                );
                assert.equal(window.injected, undefined);
                assert.equal(
                    window.arguments[0].getHistoryPage().total,
                    scenario === "history" ? 62 : 0,
                );
                if (scenario.includes("window.injected"))
                    assert.ok(!markup.includes("window.injected"));
            } finally {
                dom.window.close();
            }
        });
    }
});
