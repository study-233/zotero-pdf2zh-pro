import type { ReactNode } from "react";
export type BreadcrumbItem = { name: string; href: string };
export type FaqItem = { question: string; answer: ReactNode };
export type SiteNavLink = {
  name: string;
  href: string;
  description?: string;
  external?: boolean;
};
export type SiteNavGroup = {
  name: string;
  href?: string;
  items: SiteNavLink[];
};
export const GITHUB_REPO_URL = "https://github.com/study-233/zotero-pdf2zh-pro";
export const SITE_NAV_LINKS: SiteNavLink[] = [
  { name: "使用教程", href: "/guide/" },
  { name: "模型测评", href: "/benchmark/" },
  { name: "更新记录", href: "/changelog/" },
];
export const SITE_NAV_ITEMS: (SiteNavLink | SiteNavGroup)[] = SITE_NAV_LINKS;
export const SITE_NAV_CTA = { name: "下载与安装", href: "/#quick-start" };
export const BASE = "/zotero-pdf2zh-pro";
