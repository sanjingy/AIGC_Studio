import type { Metadata } from "next";
import { ThemeProvider } from "next-themes";

import "./globals.css";

export const metadata: Metadata = {
  title: "AIGC Studio",
  description: "AI 内容生产操作系统",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      {/* 字体栈在 globals.css 的 --font-sans / --font-mono 上，不在这里内联：
          两处各写一份必然分叉。全部是系统字体，不下载任何 Web 字体。 */}
      <body>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
