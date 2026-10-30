import { notFound } from "next/navigation";
import Link from "next/link";
import {
  EditorialIndexShell,
  EditorialIndexHeader,
} from "@/features/editorial/components/editorial-index";
import { EditorialArticleBody } from "@/features/editorial/components/editorial-article-body";
import releases from "../../../../../.generated-data/releases.json";
import { GITHUB_REPO_URL } from "@/shared/lib/seo";
export function generateStaticParams() {
  return releases.map(({ version }) => ({ version }));
}
export async function generateMetadata({
  params,
}: {
  params: Promise<{ version: string }>;
}) {
  const { version } = await params;
  return { title: `${version} 更新记录` };
}
export default async function Release({
  params,
}: {
  params: Promise<{ version: string }>;
}) {
  const { version } = await params;
  const release = releases.find((r) => r.version === version);
  if (!release) notFound();
  return (
    <EditorialIndexShell>
      <span id={release.anchor} />
      <EditorialIndexHeader
        breadcrumbs={[
          { name: "更新记录", href: "/changelog/" },
          { name: version, href: `/changelog/${version}/` },
        ]}
        eyebrow={release.date}
        title={release.version}
        description={release.summary}
      />
      <EditorialArticleBody>
        <div dangerouslySetInnerHTML={{ __html: release.html }} />
      </EditorialArticleBody>
      <div className="mt-12 flex flex-wrap gap-6 border-t border-border pt-8 text-base">
        <a
          href={`${GITHUB_REPO_URL}/releases/tag/${version}`}
          className="underline underline-offset-4"
        >
          下载此版本 ↗
        </a>
        <Link
          href="/guide/maintenance/"
          className="underline underline-offset-4"
        >
          更新方法
        </Link>
      </div>
    </EditorialIndexShell>
  );
}
