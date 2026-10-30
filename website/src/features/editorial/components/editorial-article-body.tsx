import type { ReactNode } from "react";

interface EditorialArticleBodyProps {
  children: ReactNode;
  /** Column wrapper class (e.g. BLOG_ARTICLE_COLUMN_CLASS) so the body aligns with sibling sections. */
  columnClassName?: string;
}

/** Typeset boundary for editorial (blog/changelog) MDX content. */
export function EditorialArticleBody({
  children,
  columnClassName,
}: EditorialArticleBodyProps) {
  return (
    <div className={columnClassName}>
      <article className="typeset typeset-editorial px-1 py-2 sm:px-0">
        {children}
      </article>
    </div>
  );
}
