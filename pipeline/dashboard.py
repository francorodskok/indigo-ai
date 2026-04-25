"""
dashboard.py — generación de un HTML estático con el estado del sistema.

Después de cada ciclo, se genera un único archivo `outputs/dashboard.html` con
todo lo que un humano necesita para auditar visualmente el sistema:

  - Header: equity, cash %, # posiciones, fecha del último ciclo
  - Tabla de holdings: ticker, weight target/actual, drift, convicción, audit
  - Último execution_report: drifts materiales, fills missing/unexpected
  - Últimas lecciones del postmortem
  - Historial de ciclos (timestamps + counts)

Sin JS frameworks ni assets externos — un único HTML con CSS embebido,
serveable desde cualquier static host (Vercel, Fly volume, S3+CloudFront,
o simplemente abrir el archivo desde el filesystem).

Uso típico al final del ciclo::

    from pipeline.dashboard import generate_dashboard
    path = generate_dashboard()
    log.info(f"Dashboard generado: {path}")

API pública:
    generate_dashboard(outputs_dir=None, state=None) -> Path
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.query import (
    list_available_cycles,
)
from pipeline.state import load_current_holdings

log = logging.getLogger(__name__)

ROOT = Path(__file__).parent.parent
OUTPUTS_DIR = Path(__file__).parent / "outputs"
DASHBOARD_FILE = OUTPUTS_DIR / "dashboard.html"


# ── CSS embebido ──────────────────────────────────────────────────────────────

_CSS = """
:root {
  --bg: #0e1117;
  --fg: #e1e4e8;
  --muted: #8b949e;
  --accent: #58a6ff;
  --good: #56d364;
  --warn: #d29922;
  --bad: #f85149;
  --border: #30363d;
  --card: #161b22;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg);
  color: var(--fg);
}
.container { max-width: 1100px; margin: 0 auto; padding: 24px; }
h1 { font-size: 24px; margin: 0 0 4px 0; }
h2 { font-size: 16px; margin: 28px 0 8px 0; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
h3 { font-size: 13px; margin: 0; }
.subtitle { color: var(--muted); font-size: 13px; margin-bottom: 24px; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px;
}
.kpi-label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
.kpi-value { font-size: 22px; font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--muted); font-weight: 600; }
td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
tr:last-child td { border-bottom: none; }
tr:hover { background: rgba(88,166,255,0.05); }
.right { text-align: right; }
.good { color: var(--good); }
.warn { color: var(--warn); }
.bad { color: var(--bad); }
.muted { color: var(--muted); }
.mono { font-family: 'SF Mono', Menlo, Consolas, monospace; }
details { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; margin-bottom: 6px; }
details[open] { padding-bottom: 14px; }
summary { cursor: pointer; font-weight: 500; color: var(--accent); }
.tesis { white-space: pre-wrap; margin: 8px 0; padding: 8px; background: rgba(0,0,0,0.2); border-left: 2px solid var(--accent); }
.lessons { margin-top: 8px; }
.lessons li { margin-bottom: 6px; }
footer { margin-top: 32px; padding-top: 12px; border-top: 1px solid var(--border); color: var(--muted); font-size: 12px; }
.badge {
  display: inline-block;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--border);
  color: var(--fg);
  margin-left: 4px;
}
.badge.good { background: rgba(86,211,100,0.2); color: var(--good); }
.badge.warn { background: rgba(210,153,34,0.2); color: var(--warn); }
.badge.bad { background: rgba(248,81,73,0.2); color: var(--bad); }
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_latest_json(stem: str, outputs_dir: Path) -> dict | None:
    """Lee el {stem}_*.json más reciente. None si no existe."""
    candidates = sorted(outputs_dir.glob(f"{stem}_*.json"))
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"No se pudo leer {latest}: {e}")
        return None


def _latest_execution_report(outputs_dir: Path) -> dict | None:
    return _load_latest_json("execution_report", outputs_dir)


def _latest_postmortem_lessons(outputs_dir: Path, n: int = 3) -> list[dict]:
    """Últimas lecciones aprendidas del postmortem, si las hay."""
    candidates = sorted(outputs_dir.glob("postmortem_*.json"))
    if not candidates:
        return []
    latest = candidates[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    lessons = data.get("lessons") or data.get("aprendizajes") or []
    return lessons[:n] if isinstance(lessons, list) else []


def _fmt_pct(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v*100:.{decimals}f}%"


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v:,.0f}"


def _drift_class(drift_bps: float | None, is_material: bool) -> str:
    if drift_bps is None:
        return "muted"
    if is_material:
        return "bad"
    if abs(drift_bps) > 25:
        return "warn"
    return "good"


# ── Render de secciones ───────────────────────────────────────────────────────

def _render_header(state: dict, latest_report: dict | None) -> str:
    holdings = state.get("holdings", [])
    n_pos = len(holdings)
    updated_at = state.get("updated_at") or "—"

    if latest_report:
        equity = latest_report.get("equity_post_execution", 0)
        cash_pct = latest_report.get("summary", {}).get("actual_cash_weight", 0)
        cycle_id = latest_report.get("cycle_id", "—")
    else:
        equity = 0
        cash_pct = 0
        cycle_id = "—"

    return f"""
    <h1>Indigo AI</h1>
    <div class="subtitle">Último ciclo: <span class="mono">{html.escape(str(cycle_id))}</span> · Estado actualizado: <span class="mono">{html.escape(str(updated_at))}</span></div>
    <div class="grid">
      <div class="card"><div class="kpi-label">Equity</div><div class="kpi-value">{_fmt_usd(equity)}</div></div>
      <div class="card"><div class="kpi-label">Cash</div><div class="kpi-value">{_fmt_pct(cash_pct, 1)}</div></div>
      <div class="card"><div class="kpi-label">Posiciones</div><div class="kpi-value">{n_pos}</div></div>
      <div class="card"><div class="kpi-label">Ciclo</div><div class="kpi-value mono" style="font-size:16px;">{html.escape(str(cycle_id))}</div></div>
    </div>
    """


def _render_holdings_table(state: dict, report_by_ticker: dict[str, dict]) -> str:
    holdings = state.get("holdings", [])
    if not holdings:
        return "<p class='muted'>No hay posiciones — primer ciclo o cartera 100% cash.</p>"

    rows = []
    for h in holdings:
        ticker = h.get("ticker", "?")
        target_w = float(h.get("weight", 0.0))
        report_row = report_by_ticker.get(ticker, {})
        actual_w = report_row.get("actual_weight", target_w)
        drift_bps = report_row.get("drift_bps")
        is_material = report_row.get("is_material", False)
        drift_cls = _drift_class(drift_bps, is_material)
        drift_str = f"{drift_bps:+.0f}" if drift_bps is not None else "—"

        # Audit snapshot (entry + latest)
        audit = h.get("audit_snapshot") or {}
        entry = audit.get("entry") or {}
        latest = audit.get("latest") or entry
        analyst = latest.get("analyst") or {}
        debate = latest.get("debate") or {}
        constructor = latest.get("constructor") or {}

        conv = analyst.get("conviccion") or constructor.get("conviction") or "—"
        tesis_text = analyst.get("tesis") or "(sin análisis)"
        debate_decision = debate.get("verdict_decision") or "—"
        debate_razon = debate.get("verdict_razon") or ""
        constructor_rationale = constructor.get("rationale") or ""
        entry_cycle = entry.get("cycle_id") or "—"
        entry_tesis = (entry.get("analyst") or {}).get("tesis") or ""

        critica = (analyst.get("critica") or [])
        pre_conv = analyst.get("conviccion_pre_critica")
        pre_str = f"<span class='muted'>(pre-crítica: {pre_conv})</span>" if pre_conv else ""

        critica_html = ""
        if critica:
            items = "".join(f"<li>{html.escape(str(c))}</li>" for c in critica)
            critica_html = f"<h3>Auto-crítica del analyst {pre_str}</h3><ul class='lessons'>{items}</ul>"

        rows.append(f"""
          <tr>
            <td class="mono"><strong>{html.escape(ticker)}</strong></td>
            <td class="right">{_fmt_pct(target_w)}</td>
            <td class="right">{_fmt_pct(actual_w)}</td>
            <td class="right {drift_cls} mono">{drift_str} bps</td>
            <td class="right">{conv}</td>
            <td>
              <details>
                <summary>Audit (entry: {html.escape(str(entry_cycle))})</summary>
                <h3>Tesis actual ({html.escape(str(latest.get('cycle_id', '—')))})</h3>
                <div class="tesis">{html.escape(tesis_text)}</div>
                {critica_html}
                <h3>Debate verdict: <span class="badge">{html.escape(debate_decision)}</span></h3>
                <div class="tesis">{html.escape(debate_razon)}</div>
                <h3>Constructor</h3>
                <div class="tesis">{html.escape(constructor_rationale)}</div>
                {f'<h3>Tesis original (entry, {html.escape(str(entry_cycle))})</h3><div class="tesis">{html.escape(entry_tesis)}</div>' if entry_tesis and entry_tesis != tesis_text else ''}
              </details>
            </td>
          </tr>
        """)

    return f"""
    <h2>Holdings ({len(holdings)})</h2>
    <div class="card" style="padding:0;">
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th class="right">Target</th>
          <th class="right">Actual</th>
          <th class="right">Drift</th>
          <th class="right">Conv</th>
          <th>Audit trail</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    </div>
    """


def _render_execution_report(report: dict | None) -> str:
    if not report:
        return ""
    s = report.get("summary", {})
    n_mat = s.get("n_material_drifts", 0)
    badge_cls = "good" if n_mat == 0 else "bad"
    badge_txt = "clean" if n_mat == 0 else f"{n_mat} drifts materiales"

    missing = report.get("missing_from_account") or []
    unexpected = report.get("unexpected_in_account") or []

    extra_rows = ""
    if missing or unexpected:
        if missing:
            extra_rows += f"<li class='bad'><strong>Missing del account:</strong> {', '.join(html.escape(t) for t in missing)} (target > 0 pero qty = 0)</li>"
        if unexpected:
            extra_rows += f"<li class='warn'><strong>Inesperados en account:</strong> {', '.join(html.escape(t) for t in unexpected)} (residuos de sells fallidos)</li>"

    return f"""
    <h2>Validación post-ejecución <span class="badge {badge_cls}">{badge_txt}</span></h2>
    <div class="card">
      <table>
        <tr><td class="muted">Drift total absoluto</td><td class="right mono">{s.get('total_abs_drift_bps', 0)} bps</td></tr>
        <tr><td class="muted">Drift máximo</td><td class="right mono">{html.escape(str(s.get('max_drift_ticker', '—')))} ({s.get('max_drift_bps', 0)} bps)</td></tr>
        <tr><td class="muted">Cash drift</td><td class="right mono">{s.get('cash_drift_bps', 0)} bps</td></tr>
      </table>
      {f'<ul class="lessons">{extra_rows}</ul>' if extra_rows else ''}
    </div>
    """


def _render_postmortem_lessons(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    items = []
    for lesson in lessons:
        if isinstance(lesson, dict):
            text = lesson.get("text") or lesson.get("aprendizaje") or json.dumps(lesson, ensure_ascii=False)
        else:
            text = str(lesson)
        items.append(f"<li>{html.escape(str(text))}</li>")
    return f"""
    <h2>Últimas lecciones del postmortem</h2>
    <div class="card">
      <ul class="lessons">{''.join(items)}</ul>
    </div>
    """


def _render_cycles_history(outputs_dir: Path) -> str:
    cycles = list_available_cycles(outputs_dir=outputs_dir)
    if not cycles:
        return ""
    items = "".join(
        f'<li class="mono">{html.escape(c)}</li>' for c in reversed(cycles[-10:])
    )
    return f"""
    <h2>Ciclos disponibles ({len(cycles)})</h2>
    <div class="card">
      <ul class="lessons">{items}</ul>
    </div>
    """


# ── Generación principal ──────────────────────────────────────────────────────

def generate_dashboard(
    outputs_dir: Path | None = None,
    state: dict[str, Any] | None = None,
    output_path: Path | None = None,
) -> Path:
    """
    Genera el HTML del dashboard. Retorna el path al archivo escrito.

    Args:
        outputs_dir: directorio con los JSON de outputs. Default
                     pipeline/outputs.
        state: dict de current_holdings. Si es None, se carga del filesystem.
        output_path: archivo destino. Default outputs_dir/dashboard.html.
    """
    base = outputs_dir or OUTPUTS_DIR
    base.mkdir(parents=True, exist_ok=True)
    if state is None:
        state = load_current_holdings()

    latest_report = _latest_execution_report(base)
    report_by_ticker: dict[str, dict] = {}
    if latest_report:
        for r in latest_report.get("by_ticker", []):
            if r.get("ticker"):
                report_by_ticker[r["ticker"]] = r

    lessons = _latest_postmortem_lessons(base, n=5)

    body = (
        _render_header(state, latest_report)
        + _render_holdings_table(state, report_by_ticker)
        + _render_execution_report(latest_report)
        + _render_postmortem_lessons(lessons)
        + _render_cycles_history(base)
    )

    generated_at = datetime.now(timezone.utc).isoformat()
    html_doc = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Indigo AI · Dashboard</title>
<style>{_CSS}</style>
</head>
<body>
<div class="container">
{body}
<footer>Generado en {html.escape(generated_at)} · Indigo AI</footer>
</div>
</body>
</html>"""

    target = output_path or (base / "dashboard.html")
    target.write_text(html_doc, encoding="utf-8")
    log.info(f"Dashboard generado en {target}")
    return target
