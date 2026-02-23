import type { NextConfig } from "next";

// Standalone output is only needed for Docker/production builds.
// Enabling it locally causes webpack to resolve modules from the git root
// instead of client/, breaking Tailwind v4 CSS imports.
const nextConfig: NextConfig = {
  ...(process.env.NEXT_STANDALONE === "1" ? { output: "standalone" } : {}),
};

export default nextConfig;
