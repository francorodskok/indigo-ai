"""Corre el judge sobre el portfolio actual y lo agrega al JSON.

Usado cuando el portfolio se generó fuera del constructor (manual fallback).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pipeline._console import setup_utf8
setup_utf8()

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("judge_runner")

from pipeline.judge import judge_portfolio


def main() -> int:
    port_path = ROOT / "pipeline" / "outputs" / "portfolio_2026-05-13.json"
    debate_path = ROOT / "pipeline" / "outputs" / "debate_2026-05-13.json"

    port = json.loads(port_path.read_text(encoding="utf-8"))
    debate = json.loads(debate_path.read_text(encoding="utf-8"))
    macro = port.get("macro_audit") or port.get("macro_decision")

    log.info("Corriendo judge sobre portfolio %s", port["cycle_id"])
    result = judge_portfolio(port, debate, macro_decision=macro, dry_run=False)

    log.info("Judge verdict: %s", result.get("verdict"))
    log.info("Needs human review: %s", result.get("needs_human_review"))
    log.info("Issues: %d", len(result.get("issues", [])))
    log.info("Cost: $%.4f", result.get("cost_usd", 0.0))
    for issue in result.get("issues", []):
        log.info("  - %s", issue)
    log.info("Summary: %s", result.get("summary"))

    port["judge"] = result
    port_path.write_text(json.dumps(port, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Portfolio actualizado con verdict del judge: %s", port_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
