import { RootProvider } from "fumadocs-ui/provider/next";
import { DocsSidebarLayout } from "@/features/docs/components/docs-sidebar-layout";
import { source } from "@/shared/lib/source";
import { ZoteroLockup } from "@/shared/ui/zotero-lockup";
import { BASE, GITHUB_REPO_URL } from "@/shared/lib/seo";
import { zhTranslations } from "@/shared/lib/zh-translations";
import "./docs.css";
const theme = { enabled: false };
const search = {
  options: { type: "static" as const, api: `${BASE}/search-index.json` },
};
const i18n = { locale: "zh", translations: zhTranslations };
export default function GuideLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <RootProvider theme={theme} search={search} i18n={i18n}>
      <DocsSidebarLayout
        tree={source.getPageTree()}
        baseOptions={{
          nav: { title: <ZoteroLockup />, url: "/" },
          githubUrl: GITHUB_REPO_URL,
          links: [
            { text: "返回首页", url: "/" },
            { text: "更新记录", url: "/changelog/" },
          ],
        }}
      >
        {children}
      </DocsSidebarLayout>
    </RootProvider>
  );
}
