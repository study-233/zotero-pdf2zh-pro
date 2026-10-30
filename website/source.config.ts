import {
  githubAlerts,
  screenshotAttributes,
} from "./scripts/content-plugins.mjs";
import {
  defineConfig,
  defineDocs,
  frontmatterSchema,
} from "fumadocs-mdx/config";
import { z } from "zod";
import screenshots from "./screenshots.json";
export const docs = defineDocs({
  dir: "src/content/docs/guide",
  docs: {
    schema: frontmatterSchema.extend({
      editUrl: z.string().optional(),
      prev: z.any().optional(),
      next: z.any().optional(),
    }),
    postprocess: { includeProcessedMarkdown: true },
  },
});
export default defineConfig({
  mdxOptions: {
    remarkImageOptions: false,
    remarkPlugins: [githubAlerts],
    rehypePlugins: [[screenshotAttributes, { screenshots }]],
  },
});
