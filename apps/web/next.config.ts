import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // 构建与浏览器验收各用独立目录，避免共用 .next 时覆盖开发服务的模块清单。
  distDir: process.env.NEXT_BUILD_DIR || ".next",
  outputFileTracingRoot: path.resolve(__dirname, "../.."),
  async rewrites() {
    // 前端统一打 /api/*，由 Next 转发到 FastAPI，避免浏览器端跨域
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_ORIGIN ?? "http://localhost:8000"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
