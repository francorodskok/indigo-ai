"""
_console.py — helper para forzar stdout/stderr a UTF-8 en Windows.

Sin esto, los logs con caracteres no-ASCII (acentos, emoji, box-drawing)
se rompen en cmd/PowerShell con codepage cp1252 (default Windows).

Uso desde un CLI entry point:

    from pipeline._console import setup_utf8
    if __name__ == "__main__":
        setup_utf8()
        ...

No-op en Linux/macOS y en cualquier ambiente donde reconfigure() no esté
disponible.
"""

from __future__ import annotations

import sys


def setup_utf8() -> None:
    """Best-effort: fuerza stdout/stderr a UTF-8 si estamos en Windows."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, OSError):  # pragma: no cover — best-effort
            pass
