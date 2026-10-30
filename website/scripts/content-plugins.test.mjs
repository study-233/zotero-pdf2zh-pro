import assert from "node:assert/strict";
import test from "node:test";
import { createMarkdownRenderer } from "fumadocs-core/content/md";
import { renderToStaticMarkup } from "react-dom/server";
import { githubAlerts } from "./content-plugins.mjs";

for (const [name, newline] of [
  ["LF", "\n"],
  ["Windows CRLF", "\r\n"],
]) {
  test(`GitHub alerts preserve their content with ${name}`, async () => {
    const { MarkdownServer } = createMarkdownRenderer({
      remarkPlugins: [githubAlerts],
    });
    for (const kind of ["TIP", "NOTE", "IMPORTANT", "WARNING", "CAUTION"]) {
      const result = {
        code: renderToStaticMarkup(
          await MarkdownServer({
            children: [
              `> [!${kind}]`,
              "> **先备份数据**，再运行 `brew upgrade`。",
              ">",
              "> [查看教程](/guide/maintenance/)",
            ].join(newline),
          }),
        ),
      };
      assert.match(
        result.code,
        new RegExp(`<aside class="docs-callout ${kind.toLowerCase()}">`),
      );
      assert.doesNotMatch(
        result.code,
        /\[!(?:TIP|NOTE|IMPORTANT|WARNING|CAUTION)\]/,
      );
      assert.match(result.code, /<strong>先备份数据<\/strong>/);
      assert.match(result.code, /<code>brew upgrade<\/code>/);
      assert.match(result.code, /href="\/guide\/maintenance\/"/);
    }
  });
}

test("ordinary blockquotes remain blockquotes", async () => {
  const { MarkdownServer } = createMarkdownRenderer({
    remarkPlugins: [githubAlerts],
  });
  const result = {
    code: renderToStaticMarkup(
      await MarkdownServer({
        children: "> 普通引用中提到 [!TIP] 不应变成提示框。",
      }),
    ),
  };
  assert.match(result.code, /<blockquote>/);
  assert.doesNotMatch(result.code, /<aside/);
});
