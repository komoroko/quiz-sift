// R-12 / ADR-006: ブラウザからは同一オリジン(localhost:3000)の /api と /files だけを呼び、
// Next.js がそれをローカル API へ転送する。API 側に CORS の許可オリジンを持たせないための構成。
// 既定ポートは src/quiz_sift/server/handler.py の DEFAULT_PORT と同じ値にしておく。
const apiOrigin = process.env.QUIZ_SIFT_API ?? "http://127.0.0.1:8765";

/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiOrigin}/api/:path*` },
      { source: "/files/:path*", destination: `${apiOrigin}/files/:path*` },
    ];
  },
};

export default nextConfig;
