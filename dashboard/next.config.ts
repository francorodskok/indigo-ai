import path from "node:path";
import type { NextConfig } from "next";

// Vercel/Next standalone build no incluye archivos fuera de `dashboard/` por
// default. Subimos el tracing root al monorepo raíz para que las globs puedan
// resolver `pipeline/outputs/**` y `philosophy/**` sin "../" (Turbopack rechaza
// globs que escapan del project root). Ajustar si el pipeline emite nuevos
// artifacts que la dashboard necesita.
const REPO_ROOT = path.resolve(__dirname, "..");

const nextConfig: NextConfig = {
  outputFileTracingRoot: REPO_ROOT,
  outputFileTracingIncludes: {
    "/": [
      "pipeline/outputs/**/*.json",
      "pipeline/outputs/**/*.jsonl",
      "philosophy/**/*.md",
    ],
  },
};

export default nextConfig;
