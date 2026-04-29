"""
approve.py — mueve un draft de `drafts/` a `approved/` desde Python/CLI.

Mismo gate que `dashboard/src/lib/social.ts → approveDraft`:

  - El draft debe tener `regulatory.status ∈ {green, yellow}`.
  - `red` o `pending` se rechazan (RED requiere edición; PENDING requiere
    correr el reviewer primero).
  - Anti path-traversal: el filename debe ser un basename `post_*.json`
    de la carpeta drafts/.

Diseñado para que el operador no necesite abrir el dashboard. El flujo
CLI-only es:

    py -m pipeline.social --type didactico --concept moat --review
    py -m pipeline.social --notify pipeline/outputs/social/drafts/post_<fecha>_didactico.json
    # vos lo leés en Slack, te gusta:
    py -m pipeline.social --approve pipeline/outputs/social/drafts/post_<fecha>_didactico.json
    py -m pipeline.social --publish-ready pipeline/outputs/social/approved/post_<fecha>_didactico.json
    # copy-paste a X
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Mismas paths que copy_generator. No hardcodeamos string para que si alguien
# mueve el output dir, todo se mantenga en sync.
from pipeline.social.copy_generator import DRAFTS_DIR, SOCIAL_OUTPUTS

APPROVED_DIR = SOCIAL_OUTPUTS / "approved"

VALID_APPROVE_STATUS = {"green", "yellow"}
INVALID_APPROVE_STATUS = {"red", "pending"}


class ApproveError(Exception):
    """Indica que el approve fue rechazado por validación o IO."""


def _is_safe_filename(name: str) -> bool:
    """Anti path-traversal. Solo basenames `post_*.json`."""
    if "/" in name or "\\" in name or ".." in name:
        return False
    if not name.startswith("post_") or not name.endswith(".json"):
        return False
    return True


def _resolve_input(path_or_name: str | Path) -> Path:
    """
    Acepta:
      - basename: `post_2026-04-25_didactico.json` → resolve contra DRAFTS_DIR
      - path completo: `pipeline/outputs/social/drafts/post_*.json` → tal cual

    Devuelve un Path absoluto al draft de origen. NO valida que exista — eso
    lo hace approve_draft_file().
    """
    p = Path(path_or_name)
    if p.is_absolute() or p.parent != Path("."):
        return p.resolve()
    return (DRAFTS_DIR / p.name).resolve()


def _load_draft(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ApproveError(f"draft no encontrado: {path}")
    text = path.read_text(encoding="utf-8")
    sanitized = re.sub(r"\bNaN\b", "null", text)
    try:
        return json.loads(sanitized)
    except json.JSONDecodeError as e:
        raise ApproveError(f"draft no es JSON válido: {e}") from e


def approve_draft_file(
    path_or_name: str | Path,
    *,
    drafts_dir: Path | None = None,
    approved_dir: Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Mueve un draft a approved/ tras validar el gate regulatorio.

    Args:
        path_or_name: basename `post_*.json` o ruta completa al draft.
        drafts_dir / approved_dir: override (usados en tests).
        force: si True, permite mover incluso con status=yellow sin warning extra.
            (yellow ya es aprobable por default — el flag está para futuro.)

    Returns:
        dict con `{ok, source, dest, status, fileName}`.

    Raises:
        ApproveError: si el filename no es seguro, el draft no existe,
            no parsea, o el status es 'pending' o 'red'.
    """
    drafts_dir = drafts_dir or DRAFTS_DIR
    approved_dir = approved_dir or APPROVED_DIR

    # Resolución: si te pasaron path completo, lo usamos; si te pasaron basename,
    # lo buscamos en drafts_dir.
    p_input = Path(path_or_name)
    if p_input.is_absolute() or p_input.parent != Path("."):
        source = p_input.resolve()
        file_name = source.name
    else:
        file_name = p_input.name
        source = (drafts_dir / file_name).resolve()

    if not _is_safe_filename(file_name):
        raise ApproveError(
            f"filename inválido: {file_name!r} "
            "(solo permitido `post_*.json` sin separadores de path)"
        )

    if not source.exists():
        raise ApproveError(f"draft no encontrado: {source}")

    draft = _load_draft(source)
    status = (draft.get("regulatory") or {}).get("status", "pending")

    if status == "pending":
        raise ApproveError(
            f"draft sin review regulatorio (status=pending). "
            f"Correr `py -m pipeline.social --review {source}` antes de aprobar."
        )
    if status == "red":
        raise ApproveError(
            f"draft en estado RED — no se puede aprobar. "
            f"Editar y re-revisar antes. Mirá violations en {source.name}."
        )
    if status not in VALID_APPROVE_STATUS:
        # Status raro (no en green/yellow/red/pending): bloqueamos defensivamente.
        raise ApproveError(
            f"draft con status desconocido: {status!r}. Esperado: green o yellow."
        )

    approved_dir.mkdir(parents=True, exist_ok=True)
    dest = (approved_dir / file_name).resolve()

    if dest.exists() and not force:
        # Idempotencia: si ya está en approved, asumimos que ya se aprobó.
        # Borramos el de drafts/ (si todavía existe) y devolvemos el de approved.
        if source != dest and source.exists():
            source.unlink()
        log.info("Draft ya estaba en approved/: %s (idempotente)", file_name)
        return {
            "ok": True,
            "source": str(source),
            "dest": str(dest),
            "status": status,
            "fileName": file_name,
            "already_approved": True,
        }

    # Move atómico: shutil.move funciona cross-device (rename puede fallar entre
    # discos distintos en Windows).
    shutil.move(str(source), str(dest))
    log.info("Aprobado: %s → %s (status=%s)", file_name, dest, status)

    return {
        "ok": True,
        "source": str(source),
        "dest": str(dest),
        "status": status,
        "fileName": file_name,
        "already_approved": False,
    }


def approve_and_notify(
    path_or_name: str | Path,
    *,
    drafts_dir: Path | None = None,
    approved_dir: Path | None = None,
    webhook_url: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Aprueba el draft y manda notif a Slack del archivo aprobado.

    Útil para flujo CLI-only: un solo comando aprueba y te avisa al celu
    que ya está listo para publicar.
    """
    result = approve_draft_file(
        path_or_name,
        drafts_dir=drafts_dir,
        approved_dir=approved_dir,
        force=force,
    )
    # Importamos lazy para no forzar dependencias del slack notifier en tests
    # que solo prueban approve.
    from pipeline.social.slack_notifier import notify_draft_file

    notif = notify_draft_file(result["dest"], webhook_url=webhook_url, dry_run=dry_run)
    result["slack_sent"] = notif["sent"]
    result["slack_status_code"] = notif["status_code"]
    return result
