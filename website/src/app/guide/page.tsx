import anchors from "../../../.generated-data/preparation-anchors.json";
import { DocsPage, DocsTitle, DocsDescription } from "fumadocs-ui/page";
import { Cards, Card } from "fumadocs-ui/components/card";
import { MonitorIcon, AppleIcon, BookOpenIcon, SlidersHorizontalIcon } from "lucide-react";
import Link from "next/link";
import { LegacyGuideHash } from "@/features/docs/components/legacy-guide-hash";
export const metadata = { title: "使用教程" };
export default function Guide() {
  return (
    <DocsPage toc={[]} footer={{ enabled: false }}>
      <LegacyGuideHash />
      <div hidden>
        {anchors.map((id) => (
          <span id={id} key={id} />
        ))}
      </div>
      <DocsTitle>开始使用</DocsTitle>
      <DocsDescription>
        安装插件并准备本地引擎，配置翻译来源后先试译几页。
      </DocsDescription>
      <h2 className="mt-6 text-xl font-semibold">1. 安装插件并配置翻译</h2>
      <Cards>
        <Card
          icon={<BookOpenIcon />}
          title="安装 Zotero 插件"
          href="/guide/installation/"
        >
          下载 XPI，再在「翻译服务」中点击「安装本地引擎」。
        </Card>
        <Card
          icon={<SlidersHorizontalIcon aria-hidden="true" />}
          title="配置翻译来源"
          href="/guide/api-configuration/"
        >
          填写服务地址、API Key 和模型。
        </Card>
      </Cards>
      <h2 className="mt-6 text-xl font-semibold">2. 按页试译，再翻译全文</h2>
      <Link
        className="mt-6 inline-flex text-lg font-medium underline underline-offset-4"
        href="/guide/usage/"
      >
        试译第一篇 PDF →
      </Link>
      <h2 className="mt-10 text-xl font-semibold">需要独立本地服务？</h2>
      <p className="text-muted-foreground">
        Windows x64、Windows 11 ARM64 和 Apple Silicon Mac 可直接使用插件引擎。已有服务可沿用原地址和数据，也可按下方方式独立安装。
      </p>
      <Cards>
        <Card icon={<MonitorIcon />} title="Windows" href="/guide/windows/">
          Windows 10/11 x64 独立安装器
        </Card>
        <Card icon={<AppleIcon />} title="macOS" href="/guide/macos/">
          Apple Silicon · Homebrew
        </Card>
      </Cards>
      <p className="mt-6 text-sm text-muted-foreground">
        <Link
          href="/guide/preparation/"
          className="underline underline-offset-4"
        >
          安装前准备
        </Link>{" "}
        ·{" "}
        <Link href="/guide/advanced/" className="underline underline-offset-4">
          Linux 与 Docker
        </Link>{" "}
        ·{" "}
        <Link
          href="/guide/troubleshooting/"
          className="underline underline-offset-4"
        >
          遇到问题
        </Link>
      </p>
    </DocsPage>
  );
}
