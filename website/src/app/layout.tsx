import type { Metadata } from "next";
import { ThemeProvider } from "next-themes";
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import "../assets/fonts/chinese.css";
import "./global.css";
import "./zotero.css";
export const metadata: Metadata = {
  metadataBase: new URL("https://study-233.github.io"),
  title: {
    default: "Zotero PDF2ZH Pro · 在 Zotero 中翻译论文",
    template: "%s · Zotero PDF2ZH Pro",
  },
  description: "生成双语 PDF，自动导回原条目；支持查词、选段翻译和上下文解释。",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <link
          rel="icon"
          href="/zotero-pdf2zh-pro/assets/logo.svg"
          type="image/svg+xml"
        />
      </head>
      <body className="antialiased">
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
