import { readFileSync } from "node:fs";

// Turn source GitHub alerts into semantic callouts without changing source line maps.
export function githubAlerts() {
  const labels = {
    TIP: "提示",
    NOTE: "说明",
    IMPORTANT: "重要",
    WARNING: "注意",
    CAUTION: "注意",
  };
  return (tree) => {
    const visit = (node) => {
      if (node.type === "blockquote") {
        const paragraph = node.children?.[0];
        const text = paragraph?.children?.[0];
        const match =
          text?.type === "text" &&
          text.value.match(
            /^\[!(TIP|NOTE|IMPORTANT|WARNING|CAUTION)\](?:\r?\n|$)/,
          );
        if (match) {
          text.value = text.value.slice(match[0].length);
          if (!text.value) paragraph.children.shift();
          if (!paragraph.children.length) node.children.shift();
          node.data = {
            hName: "aside",
            hProperties: {
              className: ["docs-callout", match[1].toLowerCase()],
            },
          };
          node.children.unshift({
            type: "paragraph",
            data: { hProperties: { className: ["callout-label"] } },
            children: [{ type: "text", value: labels[match[1]] }],
          });
        }
      }
      for (const child of node.children ?? []) visit(child);
    };
    visit(tree);
  };
}

export function screenshotAttributes(options = {}) {
  const metadata = JSON.parse(
    readFileSync(
      new URL("../.generated-data/site.json", import.meta.url),
      "utf8",
    ),
  );
  const { base } = metadata;
  const screenshots = options.screenshots ?? metadata.screenshots;
  return (tree) => {
    const visit = (node) => {
      if (node.type === "element" && node.tagName === "img") {
        const source = String(node.properties.src ?? "");
        const metadata =
          screenshots[
            source.startsWith(base) ? source.slice(base.length) : source
          ];
        if (metadata)
          Object.assign(node.properties, {
            width: metadata.width,
            height: metadata.height,
            loading: "lazy",
            decoding: "async",
            ...(metadata.preview
              ? {
                  srcSet:
                    base +
                    metadata.preview +
                    " " +
                    metadata.previewWidth +
                    "w, " +
                    source +
                    " " +
                    metadata.width +
                    "w",
                  sizes: "(min-width: 960px) 800px, 100vw",
                }
              : {}),
          });
      }
      for (const child of node.children ?? []) visit(child);
    };
    visit(tree);
  };
}
