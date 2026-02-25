import type { NextConfig } from "next";

// Standalone output is only needed for Docker/production builds.
// Enabling it locally causes webpack to resolve modules from the git root
// instead of client/, breaking Tailwind v4 CSS imports.
const nextConfig: NextConfig = {
  ...(process.env.NEXT_STANDALONE === "1" ? { output: "standalone" } : {}),

  // Proxy /api/* → FastAPI server so the /transfer page's fetch() calls work
  // without needing to hardcode http://localhost:8000.
  async rewrites() {
    const apiBase = (process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws")
      .replace(/^ws/, "http")
      .split("/ws")[0];
    return [
      {
        source: "/api/:path*",
        destination: `${apiBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
