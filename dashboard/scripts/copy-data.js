// copy-data.js — copia los artifacts del pipeline al deploy del dashboard.
//
// El dashboard de Vercel se buildea con Root Directory = `dashboard`, así
// que el contenido de `philosophy/` y `pipeline/outputs/` del repo raíz
// NO está disponible en runtime de Vercel.
//
// Este script corre como `prebuild` (antes de `next build`) y copia los
// archivos a `dashboard/.indigo-data/`, que SÍ queda dentro del deploy.
//
// El helper `src/lib/paths.ts` detecta `.indigo-data/` y lo prefiere
// sobre el layout local `<dashboard>/../`. Eso significa:
//   - En Vercel: `.indigo-data/` (creado por este script) → datos del build.
//   - En local dev: si corriste `npm run dev` con predev, también ve
//     `.indigo-data/`. Si no, fallback al layout local del repo.
//
// Lo que se copia:
//   philosophy/constitution.md       → .indigo-data/philosophy/constitution.md
//   philosophy/exclusions.md         → .indigo-data/philosophy/exclusions.md
//   pipeline/outputs/*.json          → .indigo-data/pipeline/outputs/*.json
//   pipeline/outputs/*.jsonl         → .indigo-data/pipeline/outputs/*.jsonl
//   pipeline/outputs/social/         → .indigo-data/pipeline/outputs/social/
//
// Lo que NO se copia:
//   pipeline/outputs/archive/        — historial, irrelevante para vista
//   pipeline/outputs/renders/        — imágenes de IG, no se sirven aquí
//   pipeline/outputs/*.log           — logs internos
//   pipeline/outputs/*.csv           — filter raw

const fs = require("node:fs");
const path = require("node:path");

const DASHBOARD_ROOT = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(DASHBOARD_ROOT, "..");
const TARGET = path.join(DASHBOARD_ROOT, ".indigo-data");

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyFileIfExists(src, dest) {
  if (!fs.existsSync(src)) {
    return false;
  }
  ensureDir(path.dirname(dest));
  fs.copyFileSync(src, dest);
  return true;
}

function copyDirRecursive(src, dest, filter) {
  if (!fs.existsSync(src)) return 0;
  const stat = fs.statSync(src);
  if (!stat.isDirectory()) return 0;
  ensureDir(dest);
  let count = 0;
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      // Excluir subdirs especiales
      if (["archive", "renders", "__pycache__"].includes(entry.name)) continue;
      count += copyDirRecursive(srcPath, destPath, filter);
    } else if (entry.isFile()) {
      if (filter && !filter(entry.name)) continue;
      fs.copyFileSync(srcPath, destPath);
      count++;
    }
  }
  return count;
}

function main() {
  console.log(`[copy-data] DASHBOARD_ROOT=${DASHBOARD_ROOT}`);
  console.log(`[copy-data] REPO_ROOT=${REPO_ROOT}`);
  console.log(`[copy-data] TARGET=${TARGET}`);

  // Limpiar target previo para evitar archivos viejos.
  if (fs.existsSync(TARGET)) {
    fs.rmSync(TARGET, { recursive: true, force: true });
  }
  ensureDir(TARGET);

  // 1. philosophy/ — constitución + exclusiones + canon (pero no canon
  // crudo de 1MB+, solo constitution.md por ahora; el canon completo se
  // carga server-side desde el LLM pipeline, no desde el dashboard).
  const philoTarget = path.join(TARGET, "philosophy");
  let philoCount = 0;
  const philoFiles = ["constitution.md", "exclusions.md"];
  for (const f of philoFiles) {
    if (copyFileIfExists(path.join(REPO_ROOT, "philosophy", f), path.join(philoTarget, f))) {
      philoCount++;
    }
  }
  console.log(`[copy-data] philosophy: ${philoCount} archivos`);

  // 2. pipeline/outputs/ — JSON/JSONL del estado del pipeline.
  const outputsSrc = path.join(REPO_ROOT, "pipeline", "outputs");
  const outputsTarget = path.join(TARGET, "pipeline", "outputs");
  const outputsCount = copyDirRecursive(outputsSrc, outputsTarget, (name) => {
    return (
      name.endsWith(".json") ||
      name.endsWith(".jsonl") ||
      name.endsWith(".md")
    );
  });
  console.log(`[copy-data] pipeline/outputs: ${outputsCount} archivos`);

  console.log(`[copy-data] ✓ done`);
}

main();
