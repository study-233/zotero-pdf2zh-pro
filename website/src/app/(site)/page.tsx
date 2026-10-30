import Link from "next/link";
import { ArrowUpRightIcon } from "lucide-react";
import { HeroSection } from "@/features/marketing/components/hero-section";
import { ChapterHeader } from "@/features/marketing/components/chapter-header";
import { DownloadSection } from "@/features/marketing/components/download-section";
import { LegacyHomeHash } from "@/features/marketing/components/legacy-home-hash";
import { PaperReader } from "@/features/marketing/components/paper-reader";
import { ProductImage } from "@/shared/ui/product-image";
import site from "../../../.generated-data/site.json";
export default function Home() {
  return (
    <>
      <LegacyHomeHash />
      <HeroSection
        version={site.version}
        title={
          <>
            论文翻译，
            <br />
            就在 Zotero。
          </>
        }
        subtitle="生成双语 PDF，保留原文排版，自动导回原条目。"
        primaryCta={{ text: "下载与安装", href: "/#quick-start" }}
        secondaryCta={{ text: "使用教程", href: "/guide/" }}
      >
        <PaperReader />
      </HeroSection>
      <section id="preview-selection" className="py-16 sm:py-20 lg:py-24">
        <div className="site-container flex flex-col items-center">
          <ChapterHeader eyebrow="划词精读" title="从一个词，读懂上下文。" />
          <div className="mt-12 w-full max-w-[54rem] sm:mt-14">
            <ProductImage
              path="assets/guide/selection-reading.webp"
              alt="论文阅读时打开 attention 的词典卡片，显示释义、发音和例句"
            />
          </div>
          <p className="mt-10 text-center text-lg leading-8 text-muted-foreground">
            选词查义，选句翻译，按需解释上下文。
          </p>
          <Link
            href="/guide/selection-translation/"
            className="mt-4 text-base underline underline-offset-4"
          >
            了解划词精读
          </Link>
        </div>
      </section>
      <nav
        id="features"
        className="bg-stone-50/70 py-10 dark:bg-zinc-900/35"
        aria-label="更多功能"
      >
        <div className="site-container grid grid-cols-2 gap-6 sm:grid-cols-4">
          {[
            ["批量翻译", "task-management"],
            ["中断补译", "task-management"],
            ["术语库", "glossary"],
            ["文档导出", "export"],
          ].map(([name, slug]) => (
            <Link
              key={name}
              href={`/guide/${slug}/`}
              className="flex items-center justify-between gap-3 text-lg font-medium"
            >
              {name}
              <ArrowUpRightIcon className="size-4 text-muted-foreground" />
            </Link>
          ))}
        </div>
      </nav>
      <DownloadSection
        version={site.version}
        min={site.compatibility.strict_min_version.split(".")[0]}
        max={site.compatibility.strict_max_version.split(".")[0]}
      />
    </>
  );
}
