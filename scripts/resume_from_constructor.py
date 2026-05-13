"""Resume del ciclo desde constructor (cuando filter/analyst/debate ya están en outputs/).

Útil cuando el ciclo cayó en constructor y no querés re-pagar analyst+debate.
Lee debate_YYYY-MM-DD.json del día y corre: constructor → executor → dashboard
→ social scheduler (post_ciclo).
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
log = logging.getLogger("resume")

from pipeline import constructor, executor


def main() -> int:
    log.info("== Resume from constructor ==")

    # 1. Constructor
    log.info("[stage] constructor")
    try:
        port_path = constructor.run(dry_run=False)
        log.info("Constructor OK: %s", port_path)
    except Exception as e:
        log.exception("Constructor falló: %s", e)
        return 1

    # 2. Executor (Alpaca trades)
    log.info("[stage] executor")
    try:
        exec_result = executor.run()
        log.info("Executor OK: %s", exec_result)
    except Exception as e:
        log.exception("Executor falló: %s", e)
        return 2

    # 3. Dashboard
    try:
        from pipeline.dashboard import generate_dashboard
        out = generate_dashboard()
        log.info("Dashboard regenerado: %s", out)
    except Exception as e:
        log.warning("Dashboard falló (no fatal): %s", e)

    # 4. Social scheduler (post_ciclo + didactico + newsletter si tocan)
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
