"""Wrapper para correr el orchestrator cargando .env explícitamente.

Útil para ejecutar el ciclo desde un launcher (Task Scheduler, PowerShell
Start-Process, etc.) que no hereda el .env.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pipeline._console import setup_utf8
setup_utf8()

from pipeline.orchestrate import run

if __name__ == "__main__":
    sys.exit(run(force=False, dry_run=False, check_only=False))
