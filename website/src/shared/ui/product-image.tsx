import { BASE } from "@/shared/lib/seo";
import site from "../../../.generated-data/site.json";
export function ProductImage({
  path,
  alt,
  priority = false,
}: {
  path: string;
  alt: string;
  priority?: boolean;
}) {
  const meta = site.screenshots[path as keyof typeof site.screenshots] as
    { width?: number; height?: number } | undefined;
  return (
    <img
      src={`${BASE}/${path}`}
      alt={alt}
      width={meta?.width}
      height={meta?.height}
      loading={priority ? "eager" : "lazy"}
      fetchPriority={priority ? "high" : undefined}
      className="h-auto w-full rounded-lg border border-zinc-200 shadow-sm dark:border-white/15"
    />
  );
}
