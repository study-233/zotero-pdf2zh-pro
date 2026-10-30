import { notFound } from "next/navigation";
import type { ComponentProps } from "react";
import { DocsPage, DocsTitle } from "fumadocs-ui/page";
import defaultMdxComponents from "fumadocs-ui/mdx";
import { ImageZoom } from "fumadocs-ui/components/image-zoom";
import { source } from "@/shared/lib/source";
import { DocsArticleBody } from "@/features/docs/components/docs-article-body";
import { BASE } from "@/shared/lib/seo";
import { OpenHashDetails } from "@/shared/ui/open-hash-details";
export function generateStaticParams() {
  return source.generateParams();
}
const components = {
  ...defaultMdxComponents,
  img: ({ src, ...props }: ComponentProps<"img">) => (
    <ImageZoom
      {...props}
      src={typeof src === "string" ? src : undefined}
      className="rounded-lg"
      rmiz={{
        children: null,
        a11yNameButtonZoom: "放大图片",
        a11yNameButtonUnzoom: "关闭图片",
      }}
    />
  ),
  a: ({ href, ...props }: ComponentProps<"a">) => (
    <defaultMdxComponents.a
      {...props}
      href={href?.startsWith(BASE + "/") ? href.slice(BASE.length) : href}
    />
  ),
};
export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string[] }>;
}) {
  const { slug } = await params;
  const page = source.getPage(slug);
  return { title: page?.data.title, description: page?.data.description };
}
export default async function Page({
  params,
}: {
  params: Promise<{ slug: string[] }>;
}) {
  const { slug } = await params;
  const page = source.getPage(slug);
  if (!page) notFound();
  const MDX = page.data.body;
  const item = (value: { label: string; link: string } | false | undefined) =>
    value
      ? { name: value.label, url: value.link.replace(BASE, "") }
      : undefined;
  const hasNavigation =
    page.data.prev !== undefined || page.data.next !== undefined;
  return (
    <DocsPage
      toc={page.data.toc}
      footer={
        hasNavigation
          ? {
              items: {
                previous: item(page.data.prev),
                next: item(page.data.next),
              },
            }
          : undefined
      }
      editOnGithub={{
        owner: "study-233",
        repo: "zotero-pdf2zh-pro",
        path: `website/src/content/docs/guide/${slug.join("/")}.mdx`,
      }}
    >
      <OpenHashDetails />
      <DocsTitle>{page.data.title}</DocsTitle>
      <DocsArticleBody>
        <MDX components={components} />
      </DocsArticleBody>
    </DocsPage>
  );
}
