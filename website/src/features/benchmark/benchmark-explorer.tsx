"use client";
import { useEffect, useRef } from "react";
import { BASE } from "@/shared/lib/seo";
import { mountBenchmark } from "./runtime";
let plotly: Promise<void> | undefined;
function loadPlotly() {
  return (plotly ??= new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${BASE}/site/vendor/plotly/plotly-gl3d-4.1.2.min.js`;
    script.onload = () => resolve();
    script.onerror = () => {
      plotly = undefined;
      script.remove();
      reject(new Error("图表资源加载失败，请刷新重试。"));
    };
    document.head.append(script);
  }));
}
export function BenchmarkExplorer({ html }: { html: string }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    let dispose: (() => void) | undefined;
    loadPlotly()
      .then(() => {
        if (active) dispose = mountBenchmark();
      })
      .catch((error) => {
        const status = root.current?.querySelector("#chart-error");
        if (active && status) {
          status.textContent = error.message;
          status.removeAttribute("hidden");
        }
      });
    return () => {
      active = false;
      dispose?.();
    };
  }, []);
  return (
    <div
      ref={root}
      className="benchmark-explorer"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
