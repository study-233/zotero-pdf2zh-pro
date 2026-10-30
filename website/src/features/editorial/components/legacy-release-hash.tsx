"use client";
import { useEffect } from "react";
import { BASE } from "@/shared/lib/seo";
import releases from "../../../../.generated-data/releases.json";
export function LegacyReleaseHash() {
  useEffect(() => {
    const redirect = () => {
      let hash;
      try {
        hash = decodeURIComponent(location.hash.slice(1));
      } catch {
        return;
      }
      const release = releases.find(
        (r) => r.anchor === hash || r.anchors.some((anchor) => anchor === hash),
      );
      if (release)
        location.replace(
          `${BASE}/changelog/${release.version}/#${encodeURIComponent(hash)}`,
        );
    };
    redirect();
    window.addEventListener("hashchange", redirect);
    return () => window.removeEventListener("hashchange", redirect);
  }, []);
  return null;
}
