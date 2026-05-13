"""Resume del ciclo desde executor (cuando portfolio_*.json ya está en outputs/).

Útil cuando construimos el portfolio manualmente y solo falta ejecutar + social.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pipeline._console import setup_utf8
setup_utf8()

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("resume_exec")

from pipeline import executor


def main() -> int:
    log.info("== Resume from executor ==")

    # 1. Executor (Alpaca trades) — dry_run=False para trades REALES
    log.info("[stage] executor (dry_run=False, trades REALES en Alpaca)")
    try:
        exec_result = executor.run(dry_run=False)
        log.info("Executor OK: %s", exec_result)
    except Exception as e:
        log.exception("Executor falló: %s", e)
        return 2

    # 2. Dashboard
    try:
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard()
        log.info("Dashboard regenerado: %s", out)
    except Exception as e:
        log.warning("Dashboard falló (no fatal): %s", e)

    # 3. Social scheduler (post_ciclo + didactico + newsletter si tocan)
    try:
        from pipeline.social.scheduler import run_today
        log.info("[stage] social.scheduler.run_today")
        social_summary = run_today(dry_run=False)
        log.info("Social scheduler OK: %s", social_summary)
    except Exception as e:
        log.exception("Social scheduler falló (no fatal): %s", e)

    log.info("== Resume completado ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
