"""
test_api_guardrail.py — verifica que el conftest bloquee llamadas reales a
Anthropic en tests. Si este test alguna vez deja de fallar como debe, el
guardrail está roto y hay que arreglarlo.
"""

from __future__ import annotations

import pytest


def test_instantiating_anthropic_client_explodes():
    """
    Llamar a `anthropic.Anthropic()` en un test sin marker debe reventar
    inmediatamente con un error claro — no llegar a la red.
    """
    import anthropic

    with pytest.raises(RuntimeError, match="anthropic.Anthropic"):
        anthropic.Anthropic(api_key="fake-key-that-would-otherwise-fail-401")


def test_call_agent_without_mock_explodes():
    """
    Si alguien llama a `call_agent` sin mockear, debería terminar
    instanciando el cliente y explotando antes de la red.
    """
    from pipeline.claude_client import call_agent

    with pytest.raises(RuntimeError, match="anthropic.Anthropic"):
        call_agent(role="test", user_input="hola")
