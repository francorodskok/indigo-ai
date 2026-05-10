// paths.ts — resolución de paths para data del backend.
//
// El dashboard lee artifacts del pipeline (philosophy/, pipeline/outputs/).
// En localhost esos archivos viven en `<repo>/philosophy/` y
// `<repo>/pipeline/outputs/`, accesibles desde `dashboard/` con `..`.
//
// En Vercel (con Root Directory = `dashboard`), solo el contenido de
// `dashboard/` está en el deploy — `..` queda vacío. Para resolver esto,
// el script `scripts/copy-data.js` (corrido como prebuild + predev) copia
// los archivos necesarios a `dashboard/.indigo-data/` antes de cada build.
//
// Esta función `resolveDataRoot()` elige cuál usar:
//   1. Si existe `<cwd>/.indigo-data/` → es build de Vercel (o dev con
//      prebuild corrido). Lee de ahí.
//   2. Sino → asume layout local original `<cwd>/../` (legacy / dev raw).

import fs from "node:fs";
import path from "node:path";

let _cached: string | null = null;

export function resolveDataRoot(): string {
  if (_cached) return _cached;
  const cwd = process.cwd();
  const indigoData = path.join(cwd, ".indigo-data");
  try {
    if (fs.existsSync(indigoData)) {
      _cached = indigoData;
      return indigoData;
    }
  } catch {
    // Fallthrough al legacy.
  }
  // Layout local: dashboard/.. es la raíz del repo.
  _cached = path.resolve(cwd, "..");
  return _cached;
}

export function outputsDir(): string {
  return path.join(resolveDataRoot(), "pipeline", "outputs");
}

export function philosophyDir(): string {
  return path.join(resolveDataRoot(), "philosophy");
}

export function socialDir(): string {
  return path.join(outputsDir(), "social");
}
