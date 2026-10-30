import type { MetadataRoute } from 'next';
export const dynamic='force-static';
export default function robots():MetadataRoute.Robots {
 return {rules:{userAgent:'*',allow:'/zotero-pdf2zh-pro/'},sitemap:'https://study-233.github.io/zotero-pdf2zh-pro/sitemap.xml'};
}
