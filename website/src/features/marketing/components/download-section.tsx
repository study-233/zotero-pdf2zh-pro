"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRightIcon, DownloadIcon } from "lucide-react";
import { ChapterHeader } from "./chapter-header";
import { HomeOwnCommand } from "./home-own-command";
import { GITHUB_REPO_URL } from "@/shared/lib/seo";
export function DownloadSection({ version }: { version: string }) {
  const [platform, setPlatform] = useState("windows");
  useEffect(() => {
    const sync = () => {
      if (["#macos", "#windows"].includes(location.hash))
        setPlatform(location.hash.slice(1));
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
    };
  }, []);
  const choose = (value: string) => {
    setPlatform(value);
    history.replaceState(null, "", `#${value}`);
  };
  return (
    <section id="quick-start" className="py-16 sm:py-20 lg:py-24">
      <div className="site-container">
        <ChapterHeader
          eyebrow={`v${version}`}
          title="下载与安装"
          description="安装插件，在 Zotero 中检查并准备本地引擎。"
        />
        <div className="mx-auto mt-12 max-w-[54rem]">
          <div className="download-row">
            <div>
              <h3>Zotero 插件</h3>
              <p>Zotero 8–10 · XPI</p>
            </div>
            <a
              className="download-action"
              href={`${GITHUB_REPO_URL}/releases/latest/download/zotero-pdf2zh-pro.xpi`}
            >
              <DownloadIcon />
              下载插件
            </a>
          </div>
          <p className="mt-4 text-base text-muted-foreground">
            Windows x64、Windows 11 ARM64 和 Apple Silicon Mac 用户可在「翻译服务」中安装本地引擎。已有服务可沿用原地址和数据。
          </p>
          <h3 className="mt-10 mb-3 text-lg font-medium">需要独立服务？</h3>
          <div
            role="tablist"
            aria-label="安装平台"
            className="flex gap-7 border-b border-border"
          >
            {["windows", "macos"].map((p, index) => (
              <button
                key={p}
                id={`tab-${p}`}
                role="tab"
                aria-selected={platform === p}
                aria-controls={p}
                tabIndex={platform === p ? 0 : -1}
                className="platform-tab"
                onClick={() => choose(p)}
                onKeyDown={(e) => {
                  if (
                    ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)
                  ) {
                    e.preventDefault();
                    const next =
                      e.key === "Home"
                        ? "windows"
                        : e.key === "End"
                          ? "macos"
                          : index === 0
                            ? "macos"
                            : "windows";
                    choose(next);
                    document.getElementById(`tab-${next}`)?.focus();
                  }
                }}
              >
                {p === "windows" ? "Windows" : "macOS"}
              </button>
            ))}
          </div>
          <div
            id="windows"
            role="tabpanel"
            aria-labelledby="tab-windows"
            hidden={platform !== "windows"}
          >
            <div className="download-row">
              <div>
                <h3>独立本地服务</h3>
                <p>Windows 10/11 x64 · Windows 11 ARM64</p>
              </div>
              <a
                className="download-action"
                href={`${GITHUB_REPO_URL}/releases/latest/download/zotero-pdf2zh-pro-windows-x64-setup.exe`}
              >
                <DownloadIcon />
                下载 x64 版
              </a>
              <a
                className="download-action"
                href={`${GITHUB_REPO_URL}/releases/latest/download/zotero-pdf2zh-pro-windows-arm64-setup.exe`}
              >
                <DownloadIcon />
                下载 ARM64 版
              </a>
            </div>
          </div>
          <div
            id="macos"
            role="tabpanel"
            aria-labelledby="tab-macos"
            hidden={platform !== "macos"}
          >
            <div className="download-row">
              <div>
                <h3>独立本地服务</h3>
                <p>Apple Silicon · Homebrew</p>
              </div>
            </div>
            <HomeOwnCommand />
            <p className="mt-3 text-sm text-muted-foreground">
              此安装方式适用于 Apple Silicon；Intel Mac 可连接已有服务。
              <Link
                className="underline underline-offset-4"
                href="/guide/macos/"
              >
                安装说明
              </Link>
            </p>
          </div>
          <div className="mt-4 flex items-center justify-between gap-4 text-base">
            <Link
              href="/guide/"
              className="inline-flex items-center gap-2 underline underline-offset-4"
            >
              查看安装教程
              <ArrowRightIcon className="size-4" />
            </Link>
            <Link
              id="other-platforms"
              href="/guide/advanced/"
              className="text-muted-foreground hover:text-foreground"
            >
              其他平台
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
