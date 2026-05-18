"""
daily_tasks.py — driver consolidado para el Windows Task Scheduler / cron diario.

Reemplaza el approach previo de "scheduler social SOLO" con uno que también
captura el NAV de Alpaca + benchmarks. Idea: una sola entrada en Task
Scheduler que dispara TODO lo diario, en este orden:

  1. **NAV snapshot (`nav_tracker.record_today`)**: lee equity de Alpaca y
     closes de SPY/QQQ vía yfinance. Idempotente — sobreescribe la entry
     del día si ya existe (last-write-wins).

  2. **Social scheduler (`social.scheduler.run_today`)**: si hoy toca un
     post según el calendario del ciclo, lo genera + revisa + manda a Slack.

Principios:
  - **Una falla no aborta la otra.** El NAV se captura aunque el scheduler
    social falle, y viceversa. Cada paso loggea su error y el siguiente
    sigue.
  - **Exit code 0 siempre.** Igual que `orchestrate.py`: no queremos que
    Task Scheduler / Fly reintente automáticamente.
  - **Modo dry-run end-to-end.** `--dry-run` propaga a ambos pasos sin
    tocar API ni state.

Uso desde Windows Task Scheduler (reemplaza el setup actual):

    Programa:    C:\\Users\\franc\\AppData\\Local\\Programs\\Python\\Python313\\python.exe
    Argumentos:  -m pipeline.daily_tasks
    Iniciar en:  C:\\Users\\franc\\Indigo-AI

Trigger: Daily at 10:00 AM (mismo que el social scheduler tenía).

Si solo querés correr una de las dos tareas:

    python -m pipeline.daily_tasks --skip-nav        # solo social scheduler
    python -m pipeline.daily_tasks --skip-social     # solo NAV snapshot
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Cargar .env explícitamente — vía Task Scheduler no hereda env del shell.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

log = logging.getLogger(__name__)


def _run_nav_snapshot(*, dry_run: bool) -> dict[str, Any]:
    """
    Captura el snapshot de NAV del día. Devuelve un dict con 'ok' y 'detail'.
    Nunca raisea — atrapa todo y reporta.
    """
    if dry_run:
        log.info("[daily/nav] dry-run — no fetcheo NAV.")
        return {"ok": True, "dry_run": True, "entry": None}

    try:
        from pipeline.nav_tracker import record_today
        entry = record_today()
        if entry is None:
            return {"ok": False, "detail": "record_today devolvió None (ver logs)."}
        return {"ok": True, "entry": entry}
    except Exception as e:
        log.exception("[daily/nav] falló: %s", e)
        return {"ok": False, "detail": str(e)}


def _run_social_scheduler(
    *,
    today: date | None,
    review: bool,
    notify: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """
    Corre el social scheduler para `today` (default: hoy UTC).
    """
    try:
        from pipeline.social.scheduler import run_today
        summary = run_today(
            today=today or datetime.now(timezone.utc).date(),
            review=review,
            notify=notify,
            dry_run=dry_run,
        )
        return {"ok": True, "summary": summary}
    except Exception as e:
        log.exception("[daily/social] falló: %s", e)
        return {"ok": False, "detail": str(e)}


# ── Entry point ──────────────────────────────────────────────────────────────


def run(
    *,
    today: date | None = None,
    skip_nav: bool = False,
    skip_social: bool = False,
    review: bool = True,
    notify: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Orquestador. Devuelve un dict con el resultado de cada paso.
    Siempre completa los dos pasos (no aborta ante una falla).
    """
    today = today or datetime.now(timezone.utc).date()
    out: dict[str, Any] = {"date": today.isoformat()}

    if not skip_nav:
        log.info("[daily] NAV snapshot…")
        out["nav"] = _run_nav_snapshot(dry_run=dry_run)
    else:
        out["nav"] = {"ok": True, "skipped": True}

    if not skip_social:
        log.info("[daily] social scheduler (today=%s)…", today)
        out["social"] = _run_social_scheduler(
            today=today,
            review=review,
            notify=notify,
            dry_run=dry_run,
        )
    else:
        out["social"] = {"ok": True, "skipped": True}

    # ── Git push de NAV history → dispara redeploy de Vercel ─────────────────
    # Sin esto, el chart del dashboard nunca se actualiza (los archivos viven
    # localmente pero Vercel pulls desde GitHub). Skipea si dry_run o si no
    # hay cambios.
    if not dry_run and not skip_nav and out.get("nav", {}).get("ok"):
        out["git_push"] = _push_nav_to_git()
    else:
        out["git_push"] = {"ok": True, "skipped": True}

    return out


def _push_nav_to_git() -> dict[str, Any]:
    """Auto-commit + push de nav_history.jsonl para que Vercel redeployee.

    Idempotente: si no hay cambios reales, exit 0 sin push.
    Nunca raisea — atrapa todo y reporta.
    """
    import subprocess
    repo_root = Path(__file__).resolve().parent.parent
    try:
        # ¿Hay cambios en el archivo?
        status = subprocess.run(
            ["git", "status", "--porcelain", "pipeline/outputs/nav_history.jsonl"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=15,
        )
        if not status.stdout.strip():
            log.info("[daily/git] nav_history sin cambios — skip push.")
            return {"ok": True, "skipped": "no_changes"}

        # Add (force porque pipeline/outputs/ está gitignored excepto los que ya rastrea)
        subprocess.run(
            ["git", "add", "-f", "pipeline/outputs/nav_history.jsonl"],
            cwd=str(repo_root), check=True, timeout=15,
        )
        # Commit con mensaje automatizado
        today_iso = date.today().isoformat()
        subprocess.run(
            ["git", "commit", "-m", f"data(nav): auto-snapshot {today_iso}"],
            cwd=str(repo_root), check=True, timeout=15,
        )
        # Push
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(repo_root), check=True, timeout=60,
        )
        log.info("[daily/git] nav_history pusheado a origin/main.")
        return {"ok": True, "pushed": True}
    except subprocess.CalledProcessError as e:
        log.warning("[daily/git] falló: %s", e)
        return {"ok": False, "detail": f"git failed: {e}"}
    except Exception as e:
        log.warning("[daily/git] error: %s", e)
        return {"ok": False, "detail": str(e)}


def _print_summary(result: dict[str, Any], *, dry_run: bool) -> None:
    mode = "DRY-RUN" if dry_run else "LIVE"
    print(f"\n┌─ daily_tasks [{mode}] · {result['date']} ─\n")

    nav = result.get("nav", {})
    if nav.get("skipped"):
        print("│  ⊘  nav: skip")
    elif nav.get("ok"):
        entry = nav.get("entry")
        if entry:
            equity = entry.get("equity_usd")
            spy = entry.get("spy_close")
            print(f"│  ✓  nav: equity=${equity:.2f}  spy={spy}")
        else:
            print("│  ✓  nav: dry-run, no fetch")
    else:
        print(f"│  ✗  nav: {nav.get('detail', 'error')}")

    soc = result.get("social", {})
    if soc.get("skipped"):
        print("│  ⊘  social: skip")
    elif soc.get("ok"):
        s = soc.get("summary", {})
        day = s.get("day_of_cycle")
        gens = len(s.get("drafts_generated", []))
        skipped = s.get("skipped", [])
        if day is None:
            print(f"│  ✓  social: sin anchor de ciclo ({skipped})")
        else:
            print(f"│  ✓  social: día {day} del ciclo, {gens} drafts generados")
    else:
        print(f"│  ✗  social: {soc.get('detail', 'error')}")

    gp = result.get("git_push", {})
    if gp.get("skipped"):
        print(f"│  ⊘  git push: {gp.get('skipped')}")
    elif gp.get("ok"):
        print("│  ✓  git push: nav_history → origin/main (Vercel redeploy disparado)")
    else:
        print(f"│  ✗  git push: {gp.get('detail', 'error')}")
    print("└─\n")


def main(argv: list[str] | None = None) -> int:
    from pipeline._console import setup_utf8
    setup_utf8()

    p = argparse.ArgumentParser(prog="pipeline.daily_tasks")
    p.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Forzar fecha (override de hoy UTC) — solo para testing.",
    )
    p.add_argument("--skip-nav", action="store_true")
    p.add_argument("--skip-social", action="store_true")
    p.add_argument(
        "--no-review",
        action="store_true",
        help="No corre regulatory review en los drafts generados (más barato).",
    )
    p.add_argument(
        "--no-notify",
        action="store_true",
        help="No manda los drafts a Slack.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="No toca API ni disk en ninguno de los pasos.",
    )
    p.add_argument("-v", "--verbose", action="count", default=0)
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose >= 2
        else logging.INFO if args.verbose == 1
        else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    today: date | None = None
    if args.date:
        try:
            today = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            p.error(f"--date debe ser YYYY-MM-DD: {args.date}")

    result = run(
        today=today,
        skip_nav=args.skip_nav,
        skip_social=args.skip_social,
        review=not args.no_review,
        notify=not args.no_notify,
        dry_run=args.dry_run,
    )

    _print_summary(result, dry_run=args.dry_run)
    # Siempre exit 0 — Task Scheduler / Fly NO debe reintentar.
    return 0


if __name__ == "__main__":
    sys.exit(main())
