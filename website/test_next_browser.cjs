const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const run = require("../scripts/workspace.cjs").newRun("website-next-browser");
const origin = process.env.WEBSITE_ORIGIN || "http://127.0.0.1:8898";
const base = "/zotero-pdf2zh-pro/";
const results = [],
  errors = [];
const before = { TEMP: process.env.TEMP, TMP: process.env.TMP };
process.env.TEMP = process.env.TMP = run.work;
(async () => {
  let browser,
    passed = false;
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
      args: ["--enable-unsafe-swiftshader"],
    });
    for (const theme of ["light", "dark"]) {
      const context = await browser.newContext({
        viewport: { width: 1440, height: 1000 },
        reducedMotion: "reduce",
        permissions: ["clipboard-read", "clipboard-write"],
      });
      await context.addInitScript((theme) => {
        if (!localStorage.getItem("theme"))
          localStorage.setItem("theme", theme);
      }, theme);
      const page = await context.newPage();
      page.on("pageerror", (e) => errors.push(String(e)));
      page.on("console", (m) => {
        if (m.type() === "error") errors.push(m.text());
      });
      page.on("response", (r) => {
        if (r.status() >= 400) errors.push(`${r.status()} ${r.url()}`);
      });
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 1000 });
        for (const [name, route] of [
          ["home", ""],
          ["guide", "guide/"],
          ["windows", "guide/windows/"],
          ["changelog", "changelog/"],
          ["release", "changelog/v1.9.4/"],
          ["benchmark", "benchmark/"],
        ]) {
          assert.equal(
            (
              await page.goto(origin + base + route, {
                waitUntil: "networkidle",
              })
            ).status(),
            200,
          );
          await page.evaluate(async () => {
            await document.fonts.ready;
            await Promise.all(
              [...document.images].map((i) => {
                i.loading = "eager";
                return i.decode().catch(() => {});
              }),
            );
          });
          if (name === "benchmark")
            await page.waitForFunction(
              () =>
                document.getElementById("benchmark-chart")?.data?.length > 0,
            );
          const geometry = await page.evaluate(() => ({
            width: innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            theme: document.documentElement.className,
            h1: document.querySelectorAll("h1").length,
            broken: [...document.images]
              .filter((i) => i.checkVisibility() && !i.naturalWidth)
              .map((i) => i.src),
          }));
          await page.screenshot({
            path: path.join(run.report, `${name}-${theme}-${width}.png`),
            fullPage: true,
          });
          results.push({ name, ...geometry });
          assert.ok(
            geometry.scrollWidth <= width + 1,
            `${name}/${theme}/${width}: overflow ${geometry.scrollWidth}`,
          );
          assert.equal(geometry.h1, 1);
          assert.ok(geometry.theme.includes(theme));
          assert.deepEqual(geometry.broken, []);
        }
      }
      if (theme === "light") {
        for (const width of [320, 360, 768, 1024, 1280, 1920]) {
          await page.setViewportSize({ width, height: 900 });
          await page.goto(origin + base, { waitUntil: "networkidle" });
          const measurement = await page.evaluate(() => ({
            scrollWidth: document.documentElement.scrollWidth,
            header: document
              .querySelector("header")
              .getBoundingClientRect()
              .toJSON(),
            title: document
              .querySelector("h1")
              .getBoundingClientRect()
              .toJSON(),
          }));
          assert.ok(measurement.scrollWidth <= width + 1);
          assert.equal(measurement.header.height, 56);
          await page.screenshot({
            path: path.join(run.report, `home-width-${width}.png`),
            fullPage: true,
          });
          results.push({ width, ...measurement });
        }
        await page.setViewportSize({ width: 390, height: 844 });
        await page.goto(origin + base);
        const menu = page.getByRole("button", { name: "打开导航菜单" });
        await menu.click();
        await page.keyboard.press("Escape");
        assert.equal(await menu.getAttribute("aria-expanded"), "false");
        await menu.click();
        await page
          .locator("#site-mobile-navigation")
          .getByRole("link", { name: "使用教程", exact: true })
          .click();
        await page.waitForURL(origin + base + "guide/");
        await page.setViewportSize({ width: 1440, height: 1000 });
        await page.goto(origin + base + "guide/windows/");
        await page.keyboard.press("Control+k");
        const input = page.locator('[role="dialog"] input');
        await input.waitFor();
        for (const query of [
          "术语库",
          "安装失败",
          "补译",
          "划词",
          "API",
          "Windows",
        ]) {
          await input.fill(query);
          await page.waitForFunction(
            () =>
              document.querySelectorAll('[role="dialog"] button[aria-selected]')
                .length > 0,
          );
          await page.waitForTimeout(450);
          const options = await page
            .locator('[role="dialog"] button[aria-selected]')
            .allTextContents();
          assert.ok(options.length, query);
          results.push({ query, options: options.slice(0, 3) });
        }
        await input.fill("术语库");
        await page.waitForTimeout(600);
        await page.keyboard.press("ArrowDown");
        await page.keyboard.press("Enter");
        await page.waitForURL((url) =>
          url.pathname.startsWith(base + "guide/"),
        );
        await page.keyboard.press("Escape");
        await page.goto(origin + base + "guide/macos/");
        assert.ok(
          await page.getByRole("button", { name: /Copy|复制/ }).count(),
        );
        await page.goto(origin + base + "changelog/#v194---2026-10-25");
        await page.waitForURL(
          (url) => url.pathname === base + "changelog/v1.9.4/",
        );
        await page.goto(origin + base + "guide/#需要安装哪些东西");
        await page.waitForURL(
          (url) => url.pathname === base + "guide/preparation/",
        );
        await page.goto(origin + base + "#macos");
        await page.getByRole("tab", { name: "macOS" }).waitFor();
        await page.waitForFunction(
          () =>
            document
              .getElementById("tab-macos")
              .getAttribute("aria-selected") === "true",
        );
        await page
          .getByRole("button", { name: "复制 Homebrew 安装命令" })
          .click();
        assert.match(
          await page.evaluate(() => navigator.clipboard.readText()),
          /brew services start zotero-pdf2zh-pro/,
        );
        await page.getByRole("tab", { name: "macOS" }).focus();
        await page.keyboard.press("ArrowLeft");
        assert.equal(
          await page
            .getByRole("tab", { name: "Windows" })
            .getAttribute("aria-selected"),
          "true",
        );
        for (const anchor of ["results", "method", "compare"]) {
          await page.goto(origin + base + "#" + anchor);
          await page.waitForURL((url) => url.pathname === base + "benchmark/");
          await page.waitForFunction(
            () => document.querySelector(".benchmark-explorer")?.dataset.view,
          );
          if (anchor === "method")
            assert.equal(
              await page.locator("#method").evaluate((e) => e.open),
              true,
            );
          if (anchor === "compare")
            assert.equal(await page.locator("#compare").isVisible(), true);
        }
        await page.goto(origin + base + "benchmark/");
        await page.waitForFunction(
          () => document.getElementById("benchmark-chart")?.data?.length > 0,
        );
        for (const mode of ["cost", "value", "table", "speed"]) {
          await page.locator("#tab-" + mode).click();
          assert.equal(
            await page.locator("#tab-" + mode).getAttribute("aria-selected"),
            "true",
          );
          if (mode === "value") {
            await page.waitForFunction(
              () =>
                document.getElementById("benchmark-chart").data?.[0]?.type ===
                "scatter3d",
            );
            await page.screenshot({
              path: path.join(run.report, "benchmark-3d.png"),
            });
          }
          if (mode === "table") {
            await page.getByRole("button", { name: /P50/ }).click();
            assert.equal(
              await page
                .locator('[data-model-sort="p50"]')
                .evaluate((e) => e.closest("th").getAttribute("aria-sort")),
              "ascending",
            );
          }
        }
        const toggle = page.locator("[data-model-toggle]").first();
        await toggle.click();
        assert.equal(await toggle.getAttribute("aria-pressed"), "false");
        await page.locator("#models-all").click();
        assert.equal(await toggle.getAttribute("aria-pressed"), "true");
        assert.equal(
          (
            await context.request.get(
              origin + base + "benchmark/data/comparison.csv",
            )
          ).status(),
          200,
        );
        // Exercise persistent Next layouts and benchmark cleanup with client navigation.
        for (let i = 0; i < 2; i++) {
          await page
            .locator("header nav")
            .getByRole("link", { name: "更新记录", exact: true })
            .click();
          await page.waitForURL(origin + base + "changelog/");
          await page
            .locator("header nav")
            .getByRole("link", { name: "模型测评", exact: true })
            .click();
          await page.waitForURL(origin + base + "benchmark/");
          await page.waitForFunction(
            () => document.getElementById("benchmark-chart")?.data?.length > 0,
          );
        }
        await page.getByRole("button", { name: "切换到深色", exact: true }).click();
        await page.reload({ waitUntil: "networkidle" });
        assert.equal(
          await page.evaluate(() =>
            document.documentElement.classList.contains("dark"),
          ),
          true,
        );
        results.push({
          search: true,
          keyboardNavigation: true,
          clipboard: true,
          legacyAnchors: true,
          benchmarkViews: true,
          clientNavigation: true,
          themePersistence: true,
        });
      }
      await context.close();
    }
    assert.deepEqual(errors, []);
    passed = true;
  } finally {
    await browser?.close();
    for (const [key, value] of Object.entries(before)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fs.writeFileSync(
      path.join(run.report, "results.json"),
      JSON.stringify({ passed, results, errors }, null, 2),
    );
  }
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
