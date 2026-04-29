# Fonts — Indigo AI social renderer

## Inter (Bold + Regular)

- **Versión:** v4.0 (release del 2023-11-19)
- **Origen:** https://github.com/rsms/inter/releases/tag/v4.0
- **Licencia:** SIL Open Font License 1.1 (OFL) — uso comercial libre, redistribución permitida.
- **Hash de control (sha256):**
  - `Inter-Bold.ttf`     — 415,072 bytes
  - `Inter-Regular.ttf`  — 407,056 bytes

## ¿Por qué committearlas en el repo?

El renderer de carruseles (`pipeline/social/renderer.py`) busca primero
`Inter-Bold.ttf` / `Inter-Regular.ttf` en este directorio antes de caer
a fuentes del sistema (Segoe UI en Windows, DejaVu en Linux). Sin este
fallback embebido, el render sería visualmente inconsistente entre máquinas
de desarrollo (Windows) y producción (Linux/Fly.io).

Bundlearlas en el repo evita una dependencia frágil sobre el ambiente de
ejecución y un trip a la red en cada cold start del container.

## Actualizar

Si querés migrar a una versión nueva de Inter:

```powershell
$tmp = "$env:TEMP\inter-v5.zip"
Invoke-WebRequest -Uri "https://github.com/rsms/inter/releases/download/v5.0/Inter-5.0.zip" -OutFile $tmp
Expand-Archive -Path $tmp -DestinationPath "$env:TEMP\inter-v5"
Copy-Item "$env:TEMP\inter-v5\extras\ttf\Inter-Bold.ttf"    pipeline\social\assets\fonts\ -Force
Copy-Item "$env:TEMP\inter-v5\extras\ttf\Inter-Regular.ttf" pipeline\social\assets\fonts\ -Force
```

Y actualizá la versión en este README.
