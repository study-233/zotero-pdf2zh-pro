"use client";
import { useEffect } from "react";
import { usePathname } from "next/navigation";
export function OpenHashDetails() {
  const pathname = usePathname();
  useEffect(() => {
    const reveal = () => {
      if (!location.hash) return;
      let id;
      try {
        id = decodeURIComponent(location.hash.slice(1));
      } catch {
        return;
      }
      const target = document.getElementById(id);
      if (!target) return;
      const parents = [];
      let node: Element | null = target;
      while (node) {
        if (node instanceof HTMLDetailsElement) {
          node.open = true;
          parents.push(node);
        }
        node = node.parentElement;
      }
      if (parents.length) requestAnimationFrame(() => target.scrollIntoView());
    };
    reveal();
    window.addEventListener("hashchange", reveal);
    return () => window.removeEventListener("hashchange", reveal);
  }, [pathname]);
  return null;
}
