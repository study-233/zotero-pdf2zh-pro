import { createMDX } from "fumadocs-mdx/next";
const withMDX = createMDX();
export default withMDX({
  output: "export",
  basePath: "/zotero-pdf2zh-pro",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  reactStrictMode: true,
});
