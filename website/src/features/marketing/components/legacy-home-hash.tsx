"use client";
import { useEffect } from "react";
import { BASE } from "@/shared/lib/seo";
export function LegacyHomeHash() {
  useEffect(() => {
    const redirect = (event?: HashChangeEvent) => {
      const hash = event ? new URL(event.newURL).hash : location.hash;
      if (
        ["#results", "#method", "#compare", "#3d"].includes(hash) ||
        hash.startsWith("#view=")
      )
        location.replace(`${BASE}/benchmark/${hash}`);
    };
    redirect();
    window.addEventListener("hashchange", redirect);
    return () => window.removeEventListener("hashchange", redirect);
  }, []);
  return null;
}
