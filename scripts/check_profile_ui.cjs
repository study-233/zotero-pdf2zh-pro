// Load the packaged XHTML and its own CSS/JS references; no Zotero or live APIs.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const { createRequire } = require("node:module");
const Zip = createRequire(
  require.resolve("../plugin/node_modules/zotero-plugin-scaffold"),
)("adm-zip");
const ts = require("../plugin/node_modules/typescript");
const { chromium } = require(process.argv[2] || "playwright");
const root = path.resolve(__dirname, "..");
const output = path.join(root, ".local-dev/profile-ui");
const build = path.join(root, "plugin/build/addon");
const zip = new Zip(path.join(root, "plugin/build/zotero-pdf2zh-pro.xpi"));
const markup = zip.readAsText("content/llmApiManager.xhtml");
const resources = [...markup.matchAll(/(?:href|src)="([^"?]+)\?v=(\d+)"/g)];
assert.equal(resources.length, 3, "CSS and both scripts must carry a build ID");
const buildId = markup.match(/data-build="(\d+)"/)?.[1];
assert.ok(buildId);
for (const [_, name, revision] of resources) {
  assert.equal(revision, buildId);
  assert.deepEqual(
    zip.readFile("content/" + name),
    fs.readFileSync(path.join(build, "content", name)),
    "packaged resource differs: " + name,
  );
}
assert.equal(
  markup,
  fs.readFileSync(path.join(build, "content/llmApiManager.xhtml"), "utf8"),
);
assert.equal(
  zip.getEntry("content/profileDialogs.css"),
  null,
  "obsolete stylesheet must not ship",
);
// The previous dialog stylesheet styled controls but had no manager layout rules.
const legacyCSS =
  "html { font: menu; } body { margin: 0; padding: 24px; } input, select { width: 100%; } [hidden] { display: none !important; }";
const code = ts.transpileModule(
  fs.readFileSync(
    path.join(root, "plugin/src/modules/llmApiManager.ts"),
    "utf8",
  ),
  {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  },
).outputText;
const model = {};
new Function("exports", code)(model);
async function checkLayout(page, width, height) {
  const state = await page.evaluate(() => {
    const root = document.documentElement;
    const rect = (selector) => {
      const { x, y, width, height, bottom, right } = document
        .querySelector(selector)
        .getBoundingClientRect();
      return { x, y, width, height, bottom, right };
    };
    const shown = (selector) =>
      document.querySelector(selector).getBoundingClientRect().height > 0;
    return {
      revision: root.dataset.build,
      cssRevision: getComputedStyle(root)
        .getPropertyValue("--profile-ui-build")
        .trim()
        .replaceAll('"', ""),
      layout: getComputedStyle(document.body).display,
      overflow:
        root.scrollWidth > innerWidth || root.scrollHeight > innerHeight,
      list: shown("#list"),
      search: shown("#search"),
      compact: shown(".compact-toolbar"),
      sidebar: rect(".sidebar"),
      main: rect("main"),
      footer: rect("footer"),
      buttons: [...document.querySelectorAll(".actions button")].map(
        (button) => {
          const { x, y, right, bottom } = button.getBoundingClientRect();
          return { x, y, right, bottom };
        },
      ),
    };
  });
  assert.equal(state.cssRevision, state.revision, "missing or stale CSS build");
  assert.equal(state.layout, "grid", "manager layout styles were not loaded");
  assert.equal(state.overflow, false, "window must not overflow");
  assert.equal(state.compact, width < 760, "compact control visibility");
  assert.equal(state.list, width >= 760, "full list visibility");
  assert.equal(state.search, width >= 760, "search visibility");
  if (width >= 760) {
    assert.equal(state.sidebar.width, 260);
    assert.equal(state.main.x, 260);
    assert.equal(state.main.y, state.sidebar.y);
  } else assert.ok(state.main.y >= state.sidebar.bottom);
  assert.ok(state.footer.bottom <= height + 1);
  for (const button of state.buttons)
    assert.ok(
      button.x >= 0 &&
        button.y >= 0 &&
        button.right <= width &&
        button.bottom <= height,
      "action button clipped",
    );
}
(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true, channel: "chrome" });
  try {
    for (const language of ["zh-CN", "en-US"]) {
      const page = await browser.newPage({
        viewport: { width: 1100, height: 780 },
      });
      const errors = [];
      let cssMode = "current";
      const loaded = new Set();
      await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.hostname !== "profile-ui.test") return route.abort();
        const name = url.pathname.slice(1);
        if (name === "favicon.ico") return route.fulfill({ status: 204 });
        const entry = zip.getEntry(name);
        assert.ok(entry, "missing XPI resource: " + name);
        const type = name.endsWith("xhtml")
          ? "application/xhtml+xml"
          : name.endsWith("css")
            ? "text/css"
            : "text/javascript";
        loaded.add(name);
        if (name.endsWith(".css") && cssMode === "missing")
          return route.abort();
        return route.fulfill({
          contentType: type,
          body:
            name.endsWith(".css") && cssMode === "legacy"
              ? legacyCSS
              : zip.readFile(entry),
        });
      });
      page.on("pageerror", (error) => errors.push(error.message));
      const labels = Object.fromEntries(
        [
          ...zip
            .readAsText("locale/" + language + "/pdf2zhpro-addon.ftl")
            .matchAll(/^pdf2zhpro-profile-([\w-]+) = (.*)$/gm),
        ].map((m) => [m[1], m[2].trim()]),
      );
      await page.addInitScript(
        ({ presets, labels }) => {
          window.entries = [
            {
              key: "a",
              name: "Daily",
              service: "openai",
              providerPreset: "openrouter",
              apiUrl: "https://openrouter.ai/api/v1",
              apiKey: "KEY_A",
              model: "vendor/a",
              apiProtocol: "chat_completions",
              needsTest: false,
            },
            {
              key: "b",
              name: "Work",
              service: "deepseek",
              apiUrl: "https://api.deepseek.com/v1",
              apiKey: "KEY_B",
              model: "deepseek-flash",
            },
            {
              key: "legacy",
              name: "Legacy",
              service: "azure",
              apiUrl: "https://old.example/v1",
              apiKey: "OLD_KEY",
              model: "",
              azureRegion: "eastus",
            },
          ];
          window.currentKey = "a";
          if (window.name === "empty") {
            window.entries = [];
            window.currentKey = "";
          }
          window.requests = [];
          window.pending = [];
          window.autoResolve = true;
          window.confirms = [];
          window.confirmResult = true;
          window.confirm = (text) => {
            confirms.push(text);
            return confirmResult;
          };
          const copy = (obj) => JSON.parse(JSON.stringify(obj));
          const perform = (kind, api, signal) => {
            requests.push({ kind, api, signal });
            if (autoResolve)
              return Promise.resolve(
                kind === "models"
                  ? { models: ["vendor/new", "vendor/other"] }
                  : "Test OK",
              );
            return new Promise((resolve) => pending.push(resolve));
          };
          window.arguments = [
            {
              presets,
              resolvePreset: (api) =>
                presets.find(
                  (p) =>
                    p.id === api.providerPreset && p.service === api.service,
                )?.id ||
                (api.service === "openai"
                  ? "custom"
                  : presets.find((p) => p.service === api.service)?.id),
              message: (key, args = {}) => {
                if (!labels[key]) throw Error("Missing locale: " + key);
                return labels[key].replace(
                  /\{ \$(\w+) \}/g,
                  (_, key) => args[key] ?? "",
                );
              },
              list: () => copy(entries),
              current: () => currentKey,
              test: (api, signal) => perform("test", api, signal),
              listModels: (api, signal) => perform("models", api, signal),
              save: (api, use) => {
                const saved = copy({
                  ...api,
                  key: api.key || "saved-" + entries.length,
                });
                const i = entries.findIndex((e) => e.key === saved.key);
                if (i < 0) entries.push(saved);
                else entries[i] = saved;
                if (use) currentKey = saved.key;
                dispatchEvent(new Event("profiles-changed"));
                return saved;
              },
              remove: (key) => {
                entries = entries.filter((e) => e.key !== key);
                if (currentKey === key) currentKey = "";
                dispatchEvent(new Event("profiles-changed"));
              },
              top: (key) => {
                entries = [
                  ...entries.filter((e) => e.key === key),
                  ...entries.filter((e) => e.key !== key),
                ];
                dispatchEvent(new Event("profiles-changed"));
              },
            },
          ];
        },
        {
          labels,
          presets: model.PROVIDER_PRESETS.map((p) => ({
            ...p,
            label: language === "en-US" ? p.labelEn || p.label : p.label,
          })),
        },
      );
      const url = "http://profile-ui.test/content/llmApiManager.xhtml";
      await page.goto(url);
      assert.equal(
        await page.evaluate(() => document.contentType),
        "application/xhtml+xml",
      );
      await checkLayout(page, 1100, 780);
      assert.ok(
        resources.every(([, name]) => loaded.has("content/" + name)),
        "page must load its own resources",
      );
      assert.deepEqual(errors, [], "packaged scripts should initialize");
      // Playwright inputValue/fill assume uppercase HTML node names. XHTML
      // uses lowercase; read the property and exercise real keyboard input.
      const inputValue = (selector) =>
        page.locator(selector).evaluate((node) => node.value);
      const fill = async (selector, value) => {
        const input = page.locator(selector);
        await input.click();
        await page.keyboard.press("ControlOrMeta+A");
        await page.keyboard.press("Backspace");
        await page.keyboard.insertText(value);
      };
      const select = (name) =>
        page.locator(".profile-select").filter({ hasText: name }).click();
      assert.equal(await inputValue("#model"), "vendor/a");
      await select("Work");
      assert.equal(
        await page.evaluate(() => currentKey),
        "a",
        "selection does not activate a profile",
      );
      await fill("#model", "unsaved-model");
      await select("Daily");
      await select("Work");
      assert.equal(
        await inputValue("#model"),
        "unsaved-model",
        "unsaved fields survive profile switches",
      );
      await page.click("#save-only");
      assert.equal(await page.evaluate(() => currentKey), "a");
      await page.click("#save");
      assert.equal(await page.evaluate(() => currentKey), "b");
      await select("Legacy");
      assert.equal(await page.isVisible("#retired-notice"), true);
      assert.equal(await page.isDisabled("#test"), true);
      assert.equal(await inputValue("#apiKey"), "OLD_KEY");
      await page.locator("#advanced summary").click();
      assert.match(await inputValue("#legacy-data"), /eastus/);
      await page.click("#change-platform");
      await page.locator('[data-preset="openrouter"]').click();
      assert.equal(
        await inputValue("#apiKey"),
        "",
        "retired credentials do not enter a new platform",
      );
      await select("Daily");
      await page.click("#add");
      assert.equal(await page.isVisible("#platform-picker"), true);
      assert.equal(await page.evaluate(() => requests.length), 0);
      await page.locator('[data-preset="openrouter"]').click();
      await fill("#apiKey", "NEW_KEY");
      await fill("#model", "vendor/chosen");
      await page.click("#change-platform");
      await page.locator('[data-preset="custom"]').click();
      assert.equal(await inputValue("#apiKey"), "");
      await fill("#apiUrl", "https://relay.example/v1");
      await page.click("#change-platform");
      await page.locator('[data-preset="openrouter"]').click();
      assert.equal(await inputValue("#apiKey"), "NEW_KEY");
      assert.equal(await inputValue("#model"), "vendor/chosen");
      await page.click("#get-models");
      assert.equal(
        await inputValue("#model"),
        "vendor/chosen",
        "catalog must not choose a model",
      );
      await page.click("#test");
      assert.equal(await page.textContent("#status"), "Test OK");
      await fill("#model", "vendor/edited");
      assert.notEqual(await page.textContent("#status"), "Test OK");
      await page.evaluate(() => {
        autoResolve = false;
      });
      await page.click("#get-models");
      await select("Daily");
      assert.equal(
        await page.evaluate(() => requests.at(-1).signal.aborted),
        true,
      );
      await page.evaluate(() => pending.shift()({ models: ["stale-result"] }));
      assert.equal(await inputValue("#model"), "vendor/a");
      assert.equal(await page.locator("#models option").count(), 0);
      await page.evaluate(() => {
        autoResolve = true;
      });
      // Copy, top and deletion through the accessible overflow menu.
      await page
        .locator(".profile-row")
        .filter({ hasText: "Daily" })
        .locator(".more")
        .click();
      await page.locator('[data-action="copy"]').click();
      await fill("#name", "Copied");
      await page.click("#save-only");
      assert.equal(
        await page.evaluate(
          () => entries.find((e) => e.name === "Copied").apiKey,
        ),
        "KEY_A",
      );
      await page
        .locator(".profile-row")
        .filter({ hasText: "Copied" })
        .locator(".more")
        .click();
      await page.locator('[data-action="top"]').click();
      assert.equal(await page.evaluate(() => entries[0].name), "Copied");
      await page
        .locator(".profile-row")
        .filter({ hasText: "Copied" })
        .locator(".more")
        .click();
      await page.locator('[data-action="remove"]').click();
      assert.equal(
        await page.evaluate(() => entries.some((e) => e.name === "Copied")),
        false,
      );
      // Unsaved-close guard and theme/size screenshots.
      await page.evaluate(() => {
        confirmResult = false;
      });
      assert.equal(
        await page.evaluate(() =>
          dispatchEvent(new Event("close", { cancelable: true })),
        ),
        false,
      );
      await select("Daily");
      // Reveal state resets on selection; search and keyboard remain usable.
      await page.click("#reveal");
      assert.equal(await page.getAttribute("#apiKey", "type"), "text");
      await select("Work");
      await select("Daily");
      assert.equal(await page.getAttribute("#apiKey", "type"), "password");
      await fill("#search", "no-such-profile");
      assert.equal(await page.locator(".profile-row").count(), 0);
      assert.equal(
        (await page.textContent("#list")).trim(),
        labels["search-empty"],
      );
      await fill("#search", "");
      await page.locator(".profile-select").first().focus();
      await page.keyboard.press("ArrowDown");
      assert.equal(await page.locator(".profile-select:focus").count(), 1);
      // Capture a fresh form, rather than the deliberately dirty regression fixtures.
      await page.reload();
      for (const width of [1100, 920, 760, 700, 400])
        for (const theme of ["light", "dark"])
          for (const enlarged of [false, true]) {
            await page.setViewportSize({ width, height: 780 });
            await page.emulateMedia({ colorScheme: theme });
            await page.evaluate(
              (enlarged) =>
                (document.documentElement.style.fontSize = enlarged
                  ? "18px"
                  : ""),
              enlarged,
            );
            await checkLayout(page, width, 780);
            const trigger =
              width < 760
                ? page.locator("#compact-more")
                : page.locator(".profile-row.selected .more");
            await trigger.click();
            const menu = await page.locator("#profile-menu").boundingBox();
            assert.ok(
              menu.x >= 0 &&
                menu.x + menu.width <= width &&
                menu.y + menu.height <= 780,
            );
            await page.keyboard.press("ArrowDown");
            await page.keyboard.press("Escape");
            assert.equal(await page.isVisible("#profile-menu"), false);
            await page.locator("#platform-name").click();
            await page.screenshot({
              path: path.join(
                output,
                language +
                  "-" +
                  width +
                  "-" +
                  theme +
                  (enlarged ? "-large" : "") +
                  ".png",
              ),
            });
          }
      // Advanced settings and a long error must scroll without pushing actions away.
      await page.setViewportSize({ width: 700, height: 480 });
      await page.locator("#advanced > summary").click();
      await page.evaluate(() => {
        window.arguments[0].test = async () => {
          throw new Error(
            "Network failed\n" + "Diagnostic detail ".repeat(100),
          );
        };
      });
      await page.click("#test");
      assert.equal(await page.isVisible("#status-details"), true);
      assert.equal(
        await page.locator("#status-details").getAttribute("open"),
        null,
      );
      await page.locator("#status-details > summary").click();
      await checkLayout(page, 700, 480);
      await page.screenshot({
        path: path.join(output, language + "-short-error.png"),
      });
      // Long lists keep their own scroll position while editing a profile.
      await page.setViewportSize({ width: 1100, height: 480 });
      await page.evaluate(() => {
        entries.push(
          ...Array.from({ length: 30 }, (_, i) => ({
            ...entries[0],
            key: "extra-" + i,
            name: "Long profile " + i,
          })),
        );
        dispatchEvent(new Event("profiles-changed"));
        document.querySelector("#list").scrollTop = 500;
      });
      const listScroll = await page
        .locator("#list")
        .evaluate((node) => node.scrollTop);
      await fill("#name", "Updated draft");
      assert.equal(
        await page.locator("#list").evaluate((node) => node.scrollTop),
        listScroll,
      );
      await checkLayout(page, 1100, 480);
      await page.setViewportSize({ width: 700, height: 480 });
      // Deleting the current profile must leave the active source empty.
      await page.evaluate(() => {
        currentKey = "a";
        confirmResult = true;
        dispatchEvent(new Event("profiles-changed"));
      });
      await page.click("#compact-more");
      await page.click('[data-action="remove"]');
      assert.equal(await page.evaluate(() => currentKey), "");
      await page.evaluate(() => {
        window.name = "empty";
      });
      await page.reload();
      assert.equal(await page.isVisible("#platform-picker"), true);
      assert.equal(await page.isVisible("#profile-form"), false);
      assert.equal(await page.evaluate(() => requests.length), 0);
      await page.screenshot({
        path: path.join(output, language + "-empty.png"),
      });
      // A missing/stale stylesheet must be detected, even if JS still works.
      for (cssMode of ["legacy", "missing"]) {
        await page.setViewportSize({ width: 1100, height: 780 });
        await page.reload();
        await assert.rejects(checkLayout(page, 1100, 780), /CSS build/);
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log(
      "Profile UI: packaged XHTML/CSS/JS, draft isolation, activation, retired profiles, cancellation, menus, 40 theme/width/font layouts, short windows and stale/missing CSS detection passed.",
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
