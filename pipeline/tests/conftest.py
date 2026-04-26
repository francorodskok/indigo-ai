"""
conftest.py — guardrails de testing comunes a toda la suite.

PRINCIPAL: bloquea llamadas reales a la API de Anthropic en tests.

Si un test no mockea correctamente `call_agent` o el cliente HTTP, el
fixture autouse `_block_anthropic_api` revienta antes de que llegue a la
red — protegiendo tu billetera. Esto es necesario porque si el patch
del test apunta al lugar incorrecto (ej. import lazy + patch del módulo
contenedor), el código real corre y gasta créditos.

Para tests que SÍ deben pegarle a la API real (integración de verdad),
declaralos así:

    @pytest.mark.real_api
    def test_full_integration():
        ...

Y corré con `pytest -m real_api`. Por default `pytest -m "not real_api"`
está implícito en la siguiente fixture.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    """Registra el marker `real_api` para que pytest no warneé."""
    config.addinivalue_line(
        "markers",
        "real_api: marca un test que SÍ debe llegar a la API real "
        "(default: bloqueado por el fixture autouse).",
    )


@pytest.fixture(autouse=True)
def _block_anthropic_api(request, monkeypatch):
    """
    Reemplaza el constructor del cliente de Anthropic por uno que revienta
    inmediatamente. Cualquier test que termine llamando a la API real
    (típicamente porque un patch falló) va a fallar con un error claro,
    no con un cargo en la tarjeta.

    Saltea la protección si el test tiene `@pytest.mark.real_api`.
    """
    if request.node.get_closest_marker("real_api"):
        # Test deliberadamente quiere la API real. Asegurarse de que la
        # API key esté seteada porque sino el error es confuso.
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("real_api test requires ANTHROPIC_API_KEY in env")
        return

    # Usamos una CLASE (no función) para no romper type hints como
    # `anthropic.Anthropic | None` que claude_client.py evalúa al
    # importarse. La clase explota en __init__ — el momento que importa
    # para evitar la llamada a la red.
    class _BlockedAnthropicClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(
                "Test trató de instanciar anthropic.Anthropic(). "
                "Esto significa que un mock de call_agent NO está atrapando "
                "la ruta del código real (ej. patch al módulo equivocado, o "
                "import lazy bypaseando el patch). "
                "Solucioná el mock o marcá el test con @pytest.mark.real_api "
                "si querés que pegue a la API de verdad."
            )

    # Patch al constructor real del SDK. Cualquier `anthropic.Anthropic(...)`
    # explota antes de hacer red.
    try:
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", _BlockedAnthropicClient)
    except ImportError:
        # Si anthropic no está instalado, no hay nada que bloquear.
        pass

    # También invalidamos cualquier singleton de claude_client que ya
    # haya quedado de una corrida previa.
    try:
        from pipeline import claude_client
        monkeypatch.setattr(claude_client, "_client", None)
    except ImportError:
        pass
