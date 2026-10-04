import type { NextConfig } from "next";

// The browser talks to /api on this server, which forwards to the backend. So the
// backend needs no CORS setup and its address is not baked into the pages.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${BACKEND_URL}/:path*` }];
  },
};

export default nextConfig;
