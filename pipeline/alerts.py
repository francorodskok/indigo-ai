"""
alerts.py — alertas por email cuando algo material pasa en un ciclo.

Diseño: el orchestrator corre desatendido. Si la pipeline falla, si hay drifts
materiales en la ejecución, o si los kill switches saltan, queremos un email
para revisar manualmente. No es streaming — es "che, mirá esto".

Disparadores:
  1. Una etapa del pipeline falla (filter/analyst/debate/constructor/executor)
  2. El execution_report reporta n_material_drifts > 0, missing, o unexpected
  3. Caller manual (ej. desde killswitch.py si bloquea por budget)

Configuración por env vars (todas requeridas para que funcione, si falta una
se loggea warning y la alerta queda no-op):

    INDIGO_ALERT_SMTP_HOST       smtp.gmail.com
    INDIGO_ALERT_SMTP_PORT       587
    INDIGO_ALERT_SMTP_USER       sender@gmail.com
    INDIGO_ALERT_SMTP_PASSWORD   app_password (NO la contraseña de cuenta)
    INDIGO_ALERT_FROM            sender@gmail.com
    INDIGO_ALERT_TO              indigostarcm@gmail.com (puede ser CSV)

Si las env vars no están seteadas, `send_alert()` retorna False sin error
(útil en dev). El orchestrator ya loggea todo lo importante; el email es
solo conveniencia.

API pública:
    send_alert(subject, body, *, smtp_config=None) -> bool
    would_alert(report) -> tuple[bool, str]    # decide si gatillar por report
    build_alert_body(results, report=None) -> tuple[str, str]
        # genera (subject, body) human-readable
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import Any

log = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

def _read_smtp_config() -> dict[str, Any] | None:
    """Lee config SMTP de env vars. None si falta alguna requerida."""
    required = {
        "host": "INDIGO_ALERT_SMTP_HOST",
        "port": "INDIGO_ALERT_SMTP_PORT",
        "user": "INDIGO_ALERT_SMTP_USER",
        "password": "INDIGO_ALERT_SMTP_PASSWORD",
        "from": "INDIGO_ALERT_FROM",
        "to": "INDIGO_ALERT_TO",
    }
    cfg: dict[str, Any] = {}
    missing = []
    for key, env in required.items():
        val = os.getenv(env, "").strip()
        if not val:
            missing.append(env)
        cfg[key] = val
    if missing:
        log.debug(f"alertas SMTP no configuradas (faltan: {missing})")
        return None
    try:
        cfg["port"] = int(cfg["port"])
    except ValueError:
        log.warning(f"INDIGO_ALERT_SMTP_PORT inválido: {cfg['port']}")
        return None
    cfg["to"] = [t.strip() for t in cfg["to"].split(",") if t.strip()]
    if not cfg["to"]:
        return None
    return cfg


# ── Disparadores ──────────────────────────────────────────────────────────────

def would_alert(report: dict | None) -> tuple[bool, str]:
    """
    Decide si un execution_report justifica una alerta.
    Retorna (debe_alertar, razón).
    """
    if not report:
        return False, "sin execution report"
    summary = report.get("summary") or {}
    n_material = int(summary.get("n_material_drifts") or 0)
    missing = report.get("missing_from_account") or []
    unexpected = report.get("unexpected_in_account") or []

    razones = []
    if n_material > 0:
        razones.append(f"{n_material} drift(s) materiales")
    if missing:
        razones.append(f"{len(missing)} ticker(s) faltantes en account ({','.join(missing[:3])})")
    if unexpected:
        razones.append(f"{len(unexpected)} ticker(s) inesperados en account ({','.join(unexpected[:3])})")

    if razones:
        return True, "; ".join(razones)
    return False, "ejecución limpia"


def _stage_failures(results: list) -> list[dict]:
    """Saca los stages que fallaron de una lista de StageResult."""
    failed = []
    for r in results or []:
        # StageResult.to_dict() o dicts crudos
        data = r.to_dict() if hasattr(r, "to_dict") else r
        if not data.get("ok", True):
            failed.append(data)
    return failed


# ── Builder del cuerpo del mail ───────────────────────────────────────────────

def build_alert_body(
    results: list | None = None,
    report: dict | None = None,
    *,
    cycle_id: str | None = None,
) -> tuple[str, str]:
    """
    Construye (subject, body) plain-text para email. Solo se llama si ya
    decidimos que vale la pena alertar.
    """
    failed = _stage_failures(results or [])
    alert_on_report, report_reason = would_alert(report)

    # Subject
    parts = []
    if failed:
        parts.append(f"{len(failed)} stage(s) FALLARON")
    if alert_on_report:
        parts.append(report_reason)
    if not parts:
        parts.append("alerta manual")
    cycle_str = f"[{cycle_id}] " if cycle_id else ""
    subject = f"[Indigo AI] {cycle_str}{' · '.join(parts)}"

    # Body
    lines = [f"Indigo AI — alerta del ciclo {cycle_id or '(sin ID)'}", ""]
    if failed:
        lines.append("Stages que fallaron:")
        for f in failed:
            lines.append(f"  - {f.get('stage', '?')}: {f.get('error', 'sin detalle')}")
        lines.append("")

    if alert_on_report and report:
        lines.append(f"Validación post-ejecución: {report_reason}")
        s = report.get("summary") or {}
        lines.append(f"  - drift total absoluto: {s.get('total_abs_drift_bps', 0)} bps")
        lines.append(f"  - drift máximo: {s.get('max_drift_ticker', '?')} ({s.get('max_drift_bps', 0)} bps)")
        lines.append(f"  - cash drift: {s.get('cash_drift_bps', 0)} bps")
        missing = report.get("missing_from_account") or []
        if missing:
            lines.append(f"  - faltantes: {', '.join(missing)}")
        unexpected = report.get("unexpected_in_account") or []
        if unexpected:
            lines.append(f"  - inesperados: {', '.join(unexpected)}")
        lines.append("")

    lines.append("Revisar el dashboard: pipeline/outputs/dashboard.html")
    return subject, "\n".join(lines)


# ── Envío SMTP ────────────────────────────────────────────────────────────────

def send_alert(
    subject: str,
    body: str,
    *,
    smtp_config: dict[str, Any] | None = None,
) -> bool:
    """
    Envía un email de alerta. Si las env vars no están seteadas, no-op
    (retorna False). Si falla el envío, log error y retorna False — nunca
    raise (no queremos que un fallo de SMTP rompa el orchestrator).

    Args:
        subject: subject del mail.
        body: texto plano del mail.
        smtp_config: opcional para tests; sino se lee del env.

    Returns:
        True si el mail se envió, False si no se pudo (config faltante o error).
    """
    cfg = smtp_config or _read_smtp_config()
    if cfg is None:
        log.info(f"alerta no enviada (SMTP no configurado): {subject}")
        return False

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])

    try:
        # Permite inyectar un cliente para tests via cfg["_client_factory"].
        client_factory = cfg.get("_client_factory")
        if client_factory:
            server = client_factory(cfg["host"], cfg["port"])
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
        try:
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                # Servidor no soporta STARTTLS — seguimos en plain (dev SMTP).
                pass
            if cfg.get("password"):
                server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from"], cfg["to"], msg.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass
        log.info(f"alerta enviada: {subject}")
        return True
    except Exception as e:
        log.error(f"Error enviando alerta SMTP: {type(e).__name__}: {e}")
        return False


# ── Helper de alto nivel para el orchestrator ─────────────────────────────────

def maybe_alert_cycle(
    results: list | None,
    report: dict | None = None,
    *,
    cycle_id: str | None = None,
) -> bool:
    """
    Decide si el ciclo merece alerta (stage fail o material drift) y la envía.
    Retorna True si se intentó enviar (config presente y al menos una razón).
    """
    failed = _stage_failures(results or [])
    alert_report, _ = would_alert(report)
    if not failed and not alert_report:
        return False
    subject, body = build_alert_body(results, report, cycle_id=cycle_id)
    send_alert(subject, body)
    return True
