import type { MetadataRoute } from 'next';
import { source } from '@/shared/lib/source';
import releases from '../../.generated-data/releases.json';
const origin='https://study-233.github.io/zotero-pdf2zh-pro';
export const dynamic='force-static';
export default function sitemap():MetadataRoute.Sitemap {
 return ['/', '/guide/', '/changelog/', '/benchmark/', ...source.getPages().map(page=>page.url+'/'), ...releases.map(release=>`/changelog/${release.version}/`)].map(route=>({url:origin+route}));
}
