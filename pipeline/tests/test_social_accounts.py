"""
Tests del módulo pipeline.social.accounts.

La lista de cuentas de referencia es código versionado. Estos tests
chequean estructura mínima y los helpers de búsqueda/filtrado.
"""

from __future__ import annotations

import pytest

from pipeline.social.accounts import (
    ALL_ACCOUNTS,
    AR_ACCOUNTS,
    GLOBAL_ACCOUNTS,
    by_priority,
    by_region,
    get_account,
)


def test_no_duplicates():
    handles = [a["handle"].lower() for a in ALL_ACCOUNTS]
    assert len(handles) == len(set(handles)), "handles duplicados"


def test_at_least_15_accounts():
    # El doc pide 20-30 cuentas. Empezamos con algunas ya identificadas;
    # si la lista cae por debajo, conviene revisar y agregar.
    assert len(ALL_ACCOUNTS) >= 15


def test_required_fields_present():
    for a in ALL_ACCOUNTS:
        assert a["handle"], f"handle vacío: {a}"
        assert a["region"] in {"ar", "latam", "global"}, a["region"]
        assert isinstance(a["priority"], int)
        assert 1 <= a["priority"] <= 3
        assert a["topic"], f"topic vacío: {a['handle']}"


def test_handles_no_at_prefix():
    # Convención: guardamos sin "@". El helper get_account() acepta ambos.
    for a in ALL_ACCOUNTS:
        assert not a["handle"].startswith("@"), a["handle"]


class TestGetAccount:
    def test_finds_with_at(self):
        a = get_account("@mkiguel")
        assert a is not None
        assert a["handle"] == "mkiguel"

    def test_finds_without_at(self):
        a = get_account("mkiguel")
        assert a is not None

    def test_case_insensitive(self):
        a = get_account("MKIGUEL")
        assert a is not None
        assert a["handle"] == "mkiguel"

    def test_unknown_returns_none(self):
        assert get_account("@nadie_de_la_lista_xxx") is None


class TestByPriority:
    def test_priority_1_only(self):
        ones = by_priority(max_priority=1)
        assert len(ones) >= 1
        assert all(a["priority"] == 1 for a in ones)

    def test_priority_2_includes_ones_and_twos(self):
        all_top_two = by_priority(max_priority=2)
        ones = by_priority(max_priority=1)
        assert len(all_top_two) >= len(ones)
        for a in all_top_two:
            assert a["priority"] <= 2


class TestByRegion:
    def test_ar(self):
        ar = by_region("ar")
        assert len(ar) == len(AR_ACCOUNTS)
        assert all(a["region"] == "ar" for a in ar)

    def test_global(self):
        gl = by_region("global")
        assert len(gl) == len(GLOBAL_ACCOUNTS)

    def test_case_insensitive(self):
        assert by_region("AR") == by_region("ar")

    def test_unknown_region(self):
        assert by_region("antartida") == []


@pytest.mark.parametrize("handle", [
    "mkiguel",        # AR priority 1
    "fmarull",        # AR priority 1
    "morganhousel",   # global priority 1
    "LynAldenContact",  # global priority 1
])
def test_known_priority_one_accounts_present(handle):
    a = get_account(handle)
    assert a is not None, f"esperaba encontrar {handle}"
    assert a["priority"] == 1
