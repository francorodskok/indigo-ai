import path from "node:path";
import type { NextConfig } from "next";

// Next.js File Tracing — qué archivos incluir en el bundle de producción.
//
// El dashboard lee artifacts del pipeline a request time via `fs.readFile()`
// con paths dinámicos. Next.js no puede detectar esas lecturas
// estáticamente, así que tenemos que listarle los archivos explícitamente.
//
// Dos modos de operación:
//   - **Local dev**: los archivos viven en `<repo>/philosophy/` y
//     `<repo>/pipeline/outputs/`. El tracing root sube al repo root.
//   - **Vercel build**: el Root Directory es `dashboard/`, no hay acceso a
//     `<repo>/` en runtime. El script `scripts/copy-data.js` (corrido como
//     `prebuild`) copia los archivos a `dashboard/.indigo-data/`. El
//     tracing apunta ahí.
//
// Incluimos AMBAS rutas en `outputFileTracingIncludes` para que ambos
// modos funcionen sin tocar config según el entorno. La función
// `paths.ts` decide cuál usar en runtime según cuál exista.

const DASHBOARD_ROOT = __dirname;
const REPO_ROOT = path.resolve(DASHBOARD_ROOT, "..");

const nextConfig: NextConfig = {
  // Tracing root es el dashboard (donde está `.indigo-data/`). Para que el
  // tracing pueda escapar a `../` en local dev, usamos REPO_ROOT pero solo
  // si existe (en Vercel REPO_ROOT no aparece, así que cae al default).
  outputFileTracingRoot: REPO_ROOT,
  outputFileTracingIncludes: {
    // En cada route del app router incluimos:
    "/": [
      // Modo Vercel: archivos copiados por copy-data.js dentro del dashboard.
      ".indigo-data/philosophy/**/*.md",
      ".indigo-data/pipeline/outputs/**/*.json",
      ".indigo-data/pipeline/outputs/**/*.jsonl",
      // Modo dev local: archivos originales en el repo root.
      "../philosophy/**/*.md",
      "../pipeline/outputs/**/*.json",
      "../pipeline/outputs/**/*.jsonl",
    ],
  },
};

export default nextConfig;
