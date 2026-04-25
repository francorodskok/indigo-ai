# Vercel deploy — dashboard público de Indigo AI

Playbook para deployar `dashboard/` (Next.js 16) a Vercel. El sitio es 100% read-only sobre los outputs de `pipeline/outputs/` y los archivos de `philosophy/`.

---

## Topología

```
┌──────────────┐  cron diario       ┌──────────────┐
│   Fly.io     │ ─────────────────▶ │  /data vol   │  pipeline/outputs/*.json
│ (pipeline)   │                    │  + state/    │  pipeline/state/current_holdings.json
└──────────────┘                    └──────────────┘
        │                                   │
        │ git commit + push                 │ build-time read
        ▼                                   ▼
┌──────────────────────────────────────────────────┐
│       GitHub repo (Indigo-AI)                    │
└──────────────────────────────────────────────────┘
        │
        │ webhook (push to main)
        ▼
┌──────────────┐
│   Vercel     │  build con root=dashboard/
│   (sitio)    │  ISR revalidate=3600
└──────────────┘
```

**Decisiones clave:**

- El sitio NO consume APIs en runtime. Lee JSON desde `pipeline/outputs/` y MD desde `philosophy/` en build/SSR (Next.js server components).
- Los outputs son **públicos por diseño** — todo es auditable. No hay secrets en JSON.
- ISR (`revalidate = 3600` en cada page) regenera páginas cada 1h; no requiere redeploy.
- Cuando llega un nuevo ciclo (cada 20 días), el pipeline commitea los nuevos JSON a `main` y Vercel auto-deploya.

---

## Setup inicial (una sola vez)

### 1. Crear proyecto en Vercel

```bash
# Desde la raíz del repo
cd dashboard
npx vercel
```

En el wizard:

- Project name: `indigo-ai`
- Root Directory: `dashboard` (¡importante! sino no encuentra package.json)
- Framework Preset: Next.js (auto-detected)
- Build Command: `next build` (default)
- Install Command: `npm install`
- Output Directory: `.next` (default)

### 2. Configurar el proyecto para acceder a `../pipeline/outputs/`

Vercel por defecto solo uploadea el Root Directory. Hay que decirle que incluya las carpetas hermanas:

**Opción A — `next.config.ts` con `outputFileTracingIncludes`** (recomendado):

```ts
const nextConfig: NextConfig = {
  outputFileTracingIncludes: {
    "/": [
      "../pipeline/outputs/**/*.json",
      "../pipeline/outputs/**/*.jsonl",
      "../philosophy/**/*.md",
    ],
  },
};
```

**Opción B — script de prebuild** que copia los archivos a `dashboard/data/` antes del build:

```json
"scripts": {
  "prebuild": "node scripts/copy-pipeline-data.js",
  "build": "next build"
}
```

(Si se elige B, actualizar `src/lib/data.ts` para leer de `./data/` en vez de `../pipeline/outputs/`.)

### 3. Variables de entorno en Vercel

Ninguna requerida para producción del sitio público — todo es estático.

Si en el futuro se agrega Neon/PostgreSQL como source-of-truth, agregar:
- `DATABASE_URL`
- (etc.)

### 4. Conectar dominio

En Vercel Dashboard → Project → Settings → Domains:

- `indigo-ai.com` → A record / CNAME according to Vercel instructions
- O subdominio sobre `indigostar.io` si aún no está el dominio propio

---

## Deploys regulares

Triggered automáticamente por push a `main`. No hay paso manual.

Para forzar un redeploy (si ISR no refrescó algo):

```bash
npx vercel --prod
```

---

## Verificación post-deploy

```bash
# Smoke test desde local — debe responder 200 en las 4 rutas:
for path in / /trades /constitution /about; do
  echo "GET $path"
  curl -s -o /dev/null -w "%{http_code}\n" "https://indigo-ai.com${path}"
done
```

Esperado: `200 200 200 200`.

---

## Rollback

Vercel guarda todos los deploys. Para volver atrás:

1. Vercel Dashboard → Project → Deployments
2. Buscar el deploy anterior estable
3. Click "Promote to Production"

Tarda ~10 segundos.

---

## Cómo se actualiza el dashboard cuando corre un ciclo

1. Pipeline (Fly.io) corre cada 20 días.
2. Al final del ciclo, escribe `pipeline/outputs/{analysis,debate,portfolio,execution_report,dashboard}_YYYY-MM-DD.{json,html}`.
3. **Pendiente decidir**: ¿el pipeline commitea esos archivos a `main` automáticamente?
   - Opción A — Sí, con un git commit desde el cron job. Requiere: deploy key con write access + filtrar archivos sensibles del state/.
   - Opción B — No, pipeline escribe a un volumen y un job aparte syncea a un branch público.
   - Opción C — Migrar a Neon: pipeline escribe a DB; dashboard lee de DB; Vercel build no necesita los JSON.

   **Recomendación inicial (12 meses experimento):** Opción A con un script de release que solo committea archivos públicos (`pipeline/outputs/*.json` + `pipeline/outputs/dashboard.html`), excluyendo `pipeline/state/`. Simple, auditable, y cada ciclo queda como commit en la historia de Git.

---

## Costos

Vercel Hobby (free) cubre:
- 100 GB-h serverless / mes
- 100 GB bandwidth / mes
- Custom domain con SSL automático

Para un sitio que se actualiza cada 20 días con tráfico de lectores curiosos (~1000/mes esperado), el plan free alcanza varios años antes de escalar.

Si tráfico explota (>5000/día), Pro tier $20/mes/seat.
