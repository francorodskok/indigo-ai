"""
Tests del módulo yf_utils.py — retries con backoff y blacklist de delistings.

Cubre:
  - fetch_with_retry: éxito en 1° intento, retry tras transitoria,
    raise tras agotar intentos, no-retry ante permanente
  - is_delisted_response: dict vacío, None, quoteType=NONE, sin price
  - blacklist: record/load/is_blacklisted/clear con expiración por edad
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ── fetch_with_retry ──────────────────────────────────────────────────────────

class TestFetchWithRetry:
    def test_returns_value_on_first_success(self):
        from pipeline.yf_utils import fetch_with_retry
        calls = []
        def fn():
            calls.append(1)
            return {"ok": True}
        result = fetch_with_retry(fn, ticker="X", sleep=lambda s: None)
        assert result == {"ok": True}
        assert len(calls) == 1

    def test_retries_on_transient_error(self):
        from pipeline.yf_utils import fetch_with_retry
        attempts = {"n": 0}
        def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                # ConnectionError es retryable por nombre
                raise ConnectionError("temp network glitch")
            return "ok"
        result = fetch_with_retry(fn, ticker="X", sleep=lambda s: None)
        assert result == "ok"
        assert attempts["n"] == 3

    def test_retries_on_429_in_message(self):
        """Mensaje con '429' o 'rate limit' detecta como transitorio."""
        from pipeline.yf_utils import fetch_with_retry
        attempts = {"n": 0}
        def fn():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("HTTP 429 Too Many Requests")
            return "ok"
        result = fetch_with_retry(fn, ticker="X", sleep=lambda s: None)
        assert result == "ok"

    def test_raises_after_exhausting_attempts(self):
        from pipeline.yf_utils import fetch_with_retry
        def fn():
            raise ConnectionError("siempre falla")
        with pytest.raises(ConnectionError):
            fetch_with_retry(fn, ticker="X", max_attempts=3, sleep=lambda s: None)

    def test_does_not_retry_permanent_error(self):
        """Errores como TypeError / AttributeError no deben reintentar."""
        from pipeline.yf_utils import fetch_with_retry
        attempts = {"n": 0}
        def fn():
            attempts["n"] += 1
            raise TypeError("permanente")
        with pytest.raises(TypeError):
            fetch_with_retry(fn, ticker="X", sleep=lambda s: None)
        assert attempts["n"] == 1

    def test_uses_injected_sleep(self):
        """Verifica que sleep se inyecta — útil para tests rápidos."""
        from pipeline.yf_utils import fetch_with_retry
        slept = []
        attempts = {"n": 0}
        def fn():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise ConnectionError("retry me")
            return "ok"
        fetch_with_retry(
            fn, ticker="X", sleep=lambda s: slept.append(s),
            base_delay=0.5,
        )
        assert len(slept) == 1
        # base_delay × 2^0 + jitter[0,0.5) → entre 0.5 y 1.0
        assert 0.5 <= slept[0] < 1.0


# ── is_delisted_response ──────────────────────────────────────────────────────

class TestIsDelistedResponse:
    def test_none_is_delisted(self):
        from pipeline.yf_utils import is_delisted_response
        assert is_delisted_response(None) is True

    def test_empty_dict_is_delisted(self):
        from pipeline.yf_utils import is_delisted_response
        assert is_delisted_response({}) is True

    def test_quote_type_none_string_is_delisted(self):
        from pipeline.yf_utils import is_delisted_response
        assert is_delisted_response({"quoteType": "NONE"}) is True

    def test_no_price_no_quote_type_is_delisted(self):
        from pipeline.yf_utils import is_delisted_response
        assert is_delisted_response({"shortName": "XYZ"}) is True

    def test_no_price_no_market_cap_is_delisted(self):
        from pipeline.yf_utils import is_delisted_response
        assert is_delisted_response({"quoteType": "EQUITY"}) is True

    def test_with_price_is_not_delisted(self):
        from pipeline.yf_utils import is_delisted_response
        info = {"quoteType": "EQUITY", "regularMarketPrice": 100.0}
        assert is_delisted_response(info) is False

    def test_with_market_cap_and_quote_type_not_delisted(self):
        """Si hay quoteType=EQUITY y marketCap, conservador: NO marcar delisted."""
        from pipeline.yf_utils import is_delisted_response
        info = {"quoteType": "EQUITY", "marketCap": 1_000_000_000}
        # Aunque falte price, evitamos blacklistear tickers validos por
        # respuestas yfinance lentas/parciales.
        assert is_delisted_response(info) is False

    def test_with_previous_close_is_not_delisted(self):
        """previousClose cuenta como price."""
        from pipeline.yf_utils import is_delisted_response
        info = {"quoteType": "EQUITY", "previousClose": 50.0}
        assert is_delisted_response(info) is False

    def test_non_dict_is_not_delisted(self):
        """Si yfinance devolvió algo raro (no None, no dict), no asumimos delisted."""
        from pipeline.yf_utils import is_delisted_response
        assert is_delisted_response([1, 2]) is False


# ── Blacklist persistente ─────────────────────────────────────────────────────

class TestBlacklist:
    def test_record_creates_entry(self, tmp_path):
        from pipeline.yf_utils import record_delisted, load_delisted
        path = tmp_path / "delisted.json"
        record_delisted("XYZ", "test_reason", path=path)
        data = load_delisted(path=path)
        assert "XYZ" in data
        assert data["XYZ"]["reasons"][-1]["reason"] == "test_reason"
        assert "first_seen" in data["XYZ"]
        assert "last_seen" in data["XYZ"]

    def test_record_normalizes_to_uppercase(self, tmp_path):
        from pipeline.yf_utils import record_delisted, load_delisted
        path = tmp_path / "delisted.json"
        record_delisted("xyz", "r", path=path)
        data = load_delisted(path=path)
        assert "XYZ" in data
        assert "xyz" not in data

    def test_record_appends_reasons_and_caps_at_5(self, tmp_path):
        from pipeline.yf_utils import record_delisted, load_delisted
        path = tmp_path / "delisted.json"
        for i in range(7):
            record_delisted("XYZ", f"reason_{i}", path=path)
        data = load_delisted(path=path)
        # Solo las últimas 5 razones
        assert len(data["XYZ"]["reasons"]) == 5
        assert data["XYZ"]["reasons"][-1]["reason"] == "reason_6"

    def test_is_blacklisted_recent(self, tmp_path):
        from pipeline.yf_utils import record_delisted, is_blacklisted
        path = tmp_path / "delisted.json"
        record_delisted("XYZ", "r", path=path)
        assert is_blacklisted("XYZ", path=path) is True
        assert is_blacklisted("xyz", path=path) is True  # case-insensitive

    def test_is_blacklisted_unknown_ticker(self, tmp_path):
        from pipeline.yf_utils import is_blacklisted
        path = tmp_path / "delisted.json"
        assert is_blacklisted("UNKNOWN", path=path) is False

    def test_is_blacklisted_expires_after_max_age(self, tmp_path):
        """Después de max_age_days, expira y se debe reintentar."""
        from pipeline.yf_utils import is_blacklisted
        path = tmp_path / "delisted.json"
        # Forzamos un last_seen viejo
        old_iso = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        path.write_text(
            json.dumps({"XYZ": {"first_seen": old_iso, "last_seen": old_iso, "reasons": []}}),
            encoding="utf-8",
        )
        assert is_blacklisted("XYZ", path=path, max_age_days=30) is False
        # Pero con max_age_days=90 sigue válido
        assert is_blacklisted("XYZ", path=path, max_age_days=90) is True

    def test_clear_specific_ticker(self, tmp_path):
        from pipeline.yf_utils import record_delisted, clear_delisted, is_blacklisted
        path = tmp_path / "delisted.json"
        record_delisted("XYZ", "r", path=path)
        record_delisted("ABC", "r", path=path)
        clear_delisted("XYZ", path=path)
        assert is_blacklisted("XYZ", path=path) is False
        assert is_blacklisted("ABC", path=path) is True

    def test_clear_all(self, tmp_path):
        from pipeline.yf_utils import record_delisted, clear_delisted, load_delisted
        path = tmp_path / "delisted.json"
        record_delisted("XYZ", "r", path=path)
        record_delisted("ABC", "r", path=path)
        clear_delisted(path=path)
        assert load_delisted(path=path) == {}

    def test_corrupted_blacklist_file_treated_as_empty(self, tmp_path):
        """Si delisted.json está corrupto, no debe crashear."""
        from pipeline.yf_utils import is_blacklisted, load_delisted
        path = tmp_path / "delisted.json"
        path.write_text("not valid json {{", encoding="utf-8")
        assert load_delisted(path=path) == {}
        assert is_blacklisted("XYZ", path=path) is False

    def test_record_handles_empty_ticker(self, tmp_path):
        """Ticker vacío no se persiste (defensivo)."""
        from pipeline.yf_utils import record_delisted, load_delisted
        path = tmp_path / "delisted.json"
        record_delisted("", "r", path=path)
        record_delisted("   ", "r", path=path)
        assert load_delisted(path=path) == {}
