"use client";
import type * as PageTree from "fumadocs-core/page-tree";
import { DocsLayout } from "fumadocs-ui/layouts/docs";
import type { BaseLayoutProps } from "fumadocs-ui/layouts/shared";
import type { ReactNode } from "react";
// Adapted from Memos: this site has one guide tree, without API versions.
export function DocsSidebarLayout({
  children,
  baseOptions,
  tree,
}: {
  children: ReactNode;
  baseOptions: BaseLayoutProps;
  tree: PageTree.Root;
}) {
  return (
    <DocsLayout tree={tree} {...baseOptions} sidebar={{ tabs: false }}>
      {children}
    </DocsLayout>
  );
}
