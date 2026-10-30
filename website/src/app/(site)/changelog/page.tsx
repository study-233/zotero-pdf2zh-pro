import {
  EditorialIndexShell,
  EditorialIndexHeader,
  EditorialList,
  EditorialListItem,
} from "@/features/editorial/components/editorial-index";
import releases from "../../../../.generated-data/releases.json";
import { LegacyReleaseHash } from "@/features/editorial/components/legacy-release-hash";
export const metadata = { title: "更新记录" };
export default function Changelog() {
  return (
    <EditorialIndexShell>
      <LegacyReleaseHash />
      <div hidden>
        <span id="更新记录" />
        {releases.map((r) => (
          <span id={r.anchor} key={r.anchor} />
        ))}
      </div>
      <EditorialIndexHeader
        eyebrow="CHANGELOG"
        title="更新记录"
        description="每一次改进，都在这里。"
      />
      <EditorialList>
        {releases.slice(0, 10).map((release, index) => (
          <EditorialListItem
            key={release.version}
            href={`/changelog/${release.version}/`}
            title={release.version}
            labels={index === 0 ? [{ label: "最新版本", tone: "accent" }] : []}
            meta={<time dateTime={release.date}>{release.date}</time>}
            description={release.summary}
            actionLabel="查看更新"
          />
        ))}
      </EditorialList>
      <details className="mt-12 border-t border-border pt-8">
        <summary className="cursor-pointer text-lg font-medium">
          更早的版本
        </summary>
        <div className="mt-8">
          <EditorialList>
            {releases.slice(10).map((release) => (
              <EditorialListItem
                key={release.version}
                href={`/changelog/${release.version}/`}
                title={release.version}
                meta={<time dateTime={release.date}>{release.date}</time>}
                description={release.summary}
                actionLabel="查看更新"
              />
            ))}
          </EditorialList>
        </div>
      </details>
    </EditorialIndexShell>
  );
}
