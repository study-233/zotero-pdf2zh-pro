"use client";
import { useEffect } from "react";
import { BASE } from "@/shared/lib/seo";
export function LegacyHomeHash() {
  useEffect(() => {
    const redirect = () => {
      if (
        ["#results", "#method", "#compare", "#3d"].includes(location.hash) ||
        location.hash.startsWith("#view=")
      )
        location.replace(`${BASE}/benchmark/${location.hash}`);
    };
    redirect();
    window.addEventListener("hashchange", redirect);
    return () => window.removeEventListener("hashchange", redirect);
  }, []);
  return null;
}
