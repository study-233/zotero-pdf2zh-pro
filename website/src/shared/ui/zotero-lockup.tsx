import { BASE } from "@/shared/lib/seo";
export function ZoteroLockup({ height = 36 }: { height?: number }) {
  return (
    <span
      className="inline-flex items-center gap-2.5 whitespace-nowrap"
      style={{ height }}
    >
      <img
        src={`${BASE}/assets/logo.svg`}
        width={height}
        height={height}
        alt=""
        className="shrink-0"
      />
      <span className="text-[17px] font-semibold tracking-tight">
        Zotero{" "}
        <span className="font-normal text-muted-foreground">PDF2ZH Pro</span>
      </span>
    </span>
  );
}
