"use client";
import { useEffect } from "react";
import { BASE } from "@/shared/lib/seo";
import anchors from "../../../../.generated-data/preparation-anchors.json";
export function LegacyGuideHash() {
  useEffect(() => {
    const redirect = () => {
      let hash;
      try {
        hash = decodeURIComponent(location.hash.slice(1));
      } catch {
        return;
      }
      if (anchors.includes(hash))
        location.replace(
          `${BASE}/guide/preparation/#${encodeURIComponent(hash)}`,
        );
    };
    redirect();
    window.addEventListener("hashchange", redirect);
    return () => window.removeEventListener("hashchange", redirect);
  }, []);
  return null;
}
