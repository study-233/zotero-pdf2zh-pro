import Link from "next/link";
const COMPACT_LINKS = [
  { href: "/guide/", label: "使用教程", external: false },
  { href: "/changelog/", label: "更新记录", external: false },
  {
    href: "https://github.com/study-233/zotero-pdf2zh-pro",
    label: "GitHub",
    external: true,
  },
];
export function Footer() {
  return (
    <footer
      id="community"
      className="mt-auto border-t border-zinc-200 bg-white dark:border-white/10 dark:bg-zinc-950"
    >
      <div className="site-container py-12">
        <div className="grid gap-8 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
          <div>
            <p className="text-sm font-semibold tracking-[0.18em] text-zinc-950 uppercase dark:text-zinc-100">
              Zotero PDF2ZH Pro
            </p>
            <p className="mt-3 max-w-md text-sm leading-7 text-zinc-600 dark:text-zinc-400">
              在 Zotero 中翻译论文。
            </p>
          </div>
          <nav
            className="flex flex-wrap gap-x-5 gap-y-3 text-sm font-medium text-zinc-600 dark:text-zinc-300"
            aria-label="页脚导航"
          >
            {COMPACT_LINKS.map((link) =>
              link.external ? (
                <a
                  key={link.href}
                  href={link.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="transition-colors hover:text-zinc-950 dark:hover:text-zinc-100"
                >
                  {link.label}
                </a>
              ) : (
                <Link
                  key={link.href}
                  href={link.href}
                  prefetch={false}
                  className="transition-colors hover:text-zinc-950 dark:hover:text-zinc-100"
                >
                  {link.label}
                </Link>
              ),
            )}
          </nav>
        </div>
        <div className="mt-8 flex flex-wrap items-center justify-between gap-4 border-t border-zinc-200 pt-6 dark:border-white/10">
          <p className="max-w-2xl text-xs leading-6 text-muted-foreground">
            基于{" "}
            <a
              className="underline underline-offset-4"
              href="https://github.com/NightWatcher314/zotero-pdf2zh-next"
            >
              zotero-pdf2zh-next
            </a>{" "}
            继续开发，源自{" "}
            <a
              className="underline underline-offset-4"
              href="https://github.com/guaguastandup/zotero-pdf2zh"
            >
              zotero-pdf2zh
            </a>
            。
            <a
              className="ml-2 underline underline-offset-4"
              href="https://github.com/study-233/zotero-pdf2zh-pro/blob/main/LICENSE"
            >
              AGPL-3.0
            </a>
          </p>
        </div>
      </div>
    </footer>
  );
}
