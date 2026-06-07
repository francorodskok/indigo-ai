"""
Tests del nav_tracker (Tier 1 dashboard, ADR 2026-04-25).

Cobertura:
  - load_history: idempotencia, dedupe por fecha, líneas malformadas, ordenamiento.
  - upsert_entry: insert + replace + atomicidad.
  - record_today: equity ok / equity falla / benchmark falla / benchmark = None.
  - backfill: rango, weekends saltados, no sobreescribe sin --force, no inventa equity histórico.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pipeline import nav_tracker


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_history(tmp_path):
    """Path temporal del JSONL para cada test."""
    return tmp_path / "nav_history.jsonl"


def _write_lines(path: Path, lines: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(line) for line in lines) + "\n"
    path.write_text(payload, encoding="utf-8")


# ── load_history ──────────────────────────────────────────────────────────────


class TestLoadHistory:
    def test_returns_empty_when_no_file(self, tmp_history):
        assert nav_tracker.load_history(tmp_history) == []

    def test_returns_entries_sorted_by_date(self, tmp_history):
        _write_lines(tmp_history, [
            {"date": "2026-04-22", "equity_usd": 100.0},
            {"date": "2026-04-20", "equity_usd": 95.0},
            {"date": "2026-04-21", "equity_usd": 98.0},
        ])
        result = nav_tracker.load_history(tmp_history)
        assert [e["date"] for e in result] == ["2026-04-20", "2026-04-21", "2026-04-22"]

    def test_dedup_keeps_last_for_duplicate_date(self, tmp_history):
        _write_lines(tmp_history, [
            {"date": "2026-04-21", "equity_usd": 100.0, "spy_close": 500.0},
            {"date": "2026-04-21", "equity_usd": 105.0, "spy_close": 505.0},
        ])
        result = nav_tracker.load_history(tmp_history)
        assert len(result) == 1
        assert result[0]["equity_usd"] == 105.0

    def test_skips_malformed_line(self, tmp_history, caplog):
        # Mezcla de líneas válidas y JSON inválido.
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text(
            json.dumps({"date": "2026-04-21", "equity_usd": 100.0}) + "\n"
            + "{not json\n"
            + json.dumps({"date": "2026-04-22", "equity_usd": 105.0}) + "\n",
            encoding="utf-8",
        )
        result = nav_tracker.load_history(tmp_history)
        assert len(result) == 2
        assert [e["date"] for e in result] == ["2026-04-21", "2026-04-22"]

    def test_ignores_blank_lines(self, tmp_history):
        tmp_history.parent.mkdir(parents=True, exist_ok=True)
        tmp_history.write_text(
            "\n"
            + json.dumps({"date": "2026-04-21", "equity_usd": 100.0}) + "\n"
            + "  \n"
            + json.dumps({"date": "2026-04-22", "equity_usd": 105.0}) + "\n",
            encoding="utf-8",
        )
        result = nav_tracker.load_history(tmp_history)
        assert len(result) == 2

    def test_ignores_entries_without_date(self, tmp_history):
        _write_lines(tmp_history, [
            {"date": "2026-04-21", "equity_usd": 100.0},
            {"equity_usd": 999.0},  # sin date
        ])
        result = nav_tracker.load_history(tmp_history)
        assert len(result) == 1


# ── upsert_entry ──────────────────────────────────────────────────────────────


class TestUpsertEntry:
    def test_insert_into_empty_file(self, tmp_history):
        nav_tracker.upsert_entry(
            {"date": "2026-04-25", "equity_usd": 100.0},
            tmp_history,
        )
        loaded = nav_tracker.load_history(tmp_history)
        assert len(loaded) == 1
        assert loaded[0]["equity_usd"] == 100.0

    def test_replace_existing_date(self, tmp_history):
        nav_tracker.upsert_entry({"date": "2026-04-25", "equity_usd": 100.0}, tmp_history)
        nav_tracker.upsert_entry({"date": "2026-04-25", "equity_usd": 105.0}, tmp_history)
        loaded = nav_tracker.load_history(tmp_history)
        assert len(loaded) == 1
        assert loaded[0]["equity_usd"] == 105.0

    def test_inserts_in_correct_chronological_order(self, tmp_history):
        nav_tracker.upsert_entry({"date": "2026-04-25", "equity_usd": 100.0}, tmp_history)
        nav_tracker.upsert_entry({"date": "2026-04-23", "equity_usd": 95.0}, tmp_history)
        nav_tracker.upsert_entry({"date": "2026-04-24", "equity_usd": 98.0}, tmp_history)
        loaded = nav_tracker.load_history(tmp_history)
        assert [e["date"] for e in loaded] == ["2026-04-23", "2026-04-24", "2026-04-25"]

    def test_raises_without_date_field(self, tmp_history):
        with pytest.raises(ValueError, match="date"):
            nav_tracker.upsert_entry({"equity_usd": 100.0}, tmp_history)

    def test_no_orphan_tmp_files_after_write(self, tmp_history):
        """El rewrite atómico no debe dejar archivos .nav_history_*.jsonl."""
        nav_tracker.upsert_entry({"date": "2026-04-25", "equity_usd": 100.0}, tmp_history)
        leftover = list(tmp_history.parent.glob(".nav_history_*"))
        assert leftover == []


# ── _last_completed_session ───────────────────────────────────────────────────


class TestLastCompletedSession:
    """La sesión a sellar debe ser el último día hábil YA cerrado/publicado.

    Calendario de referencia: 2026-04-24 = viernes, 25 = sábado, 26 = domingo,
    27 = lunes. SESSION_FINAL_HOUR_UTC controla cuándo "hoy" cuenta como cerrado.
    """

    def _utc(self, y, m, d, h):
        return datetime(y, m, d, h, 0, tzinfo=timezone.utc)

    def test_morning_run_uses_previous_trading_day(self):
        # Lunes 27 a las 13:45 UTC (mañana BA, antes del cierre US) → debe
        # sellar el VIERNES 24, no el lunes (que aún no cerró) ni el finde.
        now = self._utc(2026, 4, 27, 13)
        assert nav_tracker._last_completed_session(now) == date(2026, 4, 24)

    def test_evening_run_uses_today_when_session_closed(self):
        # Lunes 27 a las 22:30 UTC (post-cierre US + buffer) → sella el lunes 27.
        now = self._utc(2026, 4, 27, 22)
        assert nav_tracker._last_completed_session(now) == date(2026, 4, 27)

    def test_saturday_run_uses_friday(self):
        now = self._utc(2026, 4, 25, 13)  # sábado mañana
        assert nav_tracker._last_completed_session(now) == date(2026, 4, 24)

    def test_sunday_run_uses_friday(self):
        now = self._utc(2026, 4, 26, 23)  # domingo noche
        assert nav_tracker._last_completed_session(now) == date(2026, 4, 24)

    def test_skips_holiday(self):
        # Memorial Day 2026 = lunes 25 de mayo (feriado NYSE). El martes 26 a la
        # mañana debe sellar el viernes 22 (lunes feriado, finde antes).
        now = self._utc(2026, 5, 26, 13)
        assert nav_tracker._last_completed_session(now) == date(2026, 5, 22)


# ── record_today ──────────────────────────────────────────────────────────────


class TestRecordToday:
    def test_writes_entry_with_equity_and_benchmarks(self, tmp_history):
        bm_calls = []

        def fake_bm(ticker, d):
            bm_calls.append((ticker, d))
            return {"SPY": 500.0, "QQQ": 420.0}[ticker]

        entry = nav_tracker.record_today(
            target_date=date(2026, 4, 24),  # viernes (día hábil)
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=fake_bm,
            history_path=tmp_history,
        )
        assert entry == {
            "date": "2026-04-24",
            "equity_usd": 100_000.0,
            "spy_close": 500.0,
            "qqq_close": 420.0,
        }
        assert bm_calls == [("SPY", date(2026, 4, 24)), ("QQQ", date(2026, 4, 24))]

    def test_returns_none_when_equity_fetcher_raises(self, tmp_history):
        def boom():
            raise RuntimeError("alpaca down")

        result = nav_tracker.record_today(
            target_date=date(2026, 4, 24),  # viernes (día hábil)
            equity_fetcher=boom,
            benchmark_fetcher=lambda t, d: 500.0,
            history_path=tmp_history,
        )
        assert result is None
        # No debe escribir nada
        assert not tmp_history.exists() or nav_tracker.load_history(tmp_history) == []

    def test_returns_none_when_equity_zero_or_negative(self, tmp_history):
        result = nav_tracker.record_today(
            target_date=date(2026, 4, 24),  # viernes (día hábil)
            equity_fetcher=lambda: 0.0,
            benchmark_fetcher=lambda t, d: 500.0,
            history_path=tmp_history,
        )
        assert result is None

    def test_writes_entry_even_if_benchmark_fails(self, tmp_history):
        """Si yfinance falla, igual queremos el equity grabado (las series de
        benchmarks pueden interpolarse o quedar como gap en el chart)."""

        def flaky_bm(ticker, d):
            if ticker == "QQQ":
                raise RuntimeError("yfinance down")
            return 500.0

        entry = nav_tracker.record_today(
            target_date=date(2026, 4, 24),  # viernes (día hábil)
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=flaky_bm,
            history_path=tmp_history,
        )
        assert entry["equity_usd"] == 100_000.0
        assert entry["spy_close"] == 500.0
        assert entry["qqq_close"] is None

    def test_idempotent_overwrites_existing_date(self, tmp_history):
        nav_tracker.record_today(
            target_date=date(2026, 4, 24),  # viernes (día hábil)
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=lambda t, d: 500.0,
            history_path=tmp_history,
        )
        nav_tracker.record_today(
            target_date=date(2026, 4, 24),  # viernes (día hábil)
            equity_fetcher=lambda: 101_000.0,  # cambió el equity
            benchmark_fetcher=lambda t, d: 502.0,
            history_path=tmp_history,
        )
        history = nav_tracker.load_history(tmp_history)
        assert len(history) == 1
        assert history[0]["equity_usd"] == 101_000.0
        assert history[0]["spy_close"] == 502.0


# ── backfill ──────────────────────────────────────────────────────────────────


class TestBackfill:
    def test_skips_weekends(self, tmp_history):
        # 2026-04-25 = sábado, 26 = domingo
        bm_calls = []
        nav_tracker.backfill(
            start=date(2026, 4, 24),  # viernes
            end=date(2026, 4, 27),    # lunes
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=lambda t, d: bm_calls.append((t, d)) or 500.0,
            history_path=tmp_history,
        )
        # Sólo viernes 24 y lunes 27 son hábiles
        weekdays_called = sorted({d for _, d in bm_calls})
        assert weekdays_called == [date(2026, 4, 24), date(2026, 4, 27)]

    def test_does_not_overwrite_existing_entries(self, tmp_history):
        # Entry preexistente con datos
        _write_lines(tmp_history, [
            {"date": "2026-04-21", "equity_usd": 99_999.0, "spy_close": 999.0,
             "qqq_close": 888.0},
        ])

        bm_calls = []
        nav_tracker.backfill(
            start=date(2026, 4, 21),  # martes
            end=date(2026, 4, 21),
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=lambda t, d: bm_calls.append((t, d)) or 500.0,
            history_path=tmp_history,
        )
        # No debe haber llamado al fetcher de benchmark
        assert bm_calls == []
        history = nav_tracker.load_history(tmp_history)
        assert history[0]["equity_usd"] == 99_999.0
        assert history[0]["spy_close"] == 999.0  # preservado

    def test_overwrites_benchmarks_when_force_true(self, tmp_history):
        """force=True refresca los benchmarks (yfinance puede haberse arreglado).

        Si la fecha es 'hoy' (== end), también refresca equity con el valor real
        de Alpaca — es deliberado. Para fechas pasadas, equity nunca se inventa.
        """
        # 2026-04-21 = martes. Backfill end == 21 → es 'hoy'.
        _write_lines(tmp_history, [
            {"date": "2026-04-21", "equity_usd": 99_999.0, "spy_close": 999.0},
        ])
        nav_tracker.backfill(
            start=date(2026, 4, 21),
            end=date(2026, 4, 21),
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=lambda t, d: 500.0,
            force=True,
            history_path=tmp_history,
        )
        history = nav_tracker.load_history(tmp_history)
        # En 'hoy' con force, equity se refresca con el valor real de Alpaca.
        assert history[0]["equity_usd"] == 100_000.0
        # benchmarks actualizados por force.
        assert history[0]["spy_close"] == 500.0

    def test_force_does_not_invent_equity_for_past_dates(self, tmp_history):
        """force=True en una fecha pasada NO inventa equity — sólo benchmarks."""
        _write_lines(tmp_history, [
            # entry preexistente sin equity (fecha pasada)
            {"date": "2026-04-21", "spy_close": 999.0, "qqq_close": 888.0},
        ])
        # end = 22 → la fecha 21 es pasada
        nav_tracker.backfill(
            start=date(2026, 4, 21),
            end=date(2026, 4, 22),
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=lambda t, d: 500.0,
            force=True,
            history_path=tmp_history,
        )
        history = nav_tracker.load_history(tmp_history)
        by_date = {e["date"]: e for e in history}
        # 21 (pasada): equity sigue ausente, benchmarks refrescados
        assert "equity_usd" not in by_date["2026-04-21"]
        assert by_date["2026-04-21"]["spy_close"] == 500.0
        # 22 (hoy): equity_usd presente
        assert by_date["2026-04-22"]["equity_usd"] == 100_000.0

    def test_does_not_invent_equity_for_past_dates(self, tmp_history):
        """Backfill sobre fechas pasadas NO debe poner equity_usd — sólo benchmarks."""
        # 2026-04-21 = martes, 2026-04-22 = miércoles, hoy=23 (jueves)
        nav_tracker.backfill(
            start=date(2026, 4, 21),
            end=date(2026, 4, 23),
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=lambda t, d: 500.0,
            history_path=tmp_history,
        )
        history = nav_tracker.load_history(tmp_history)
        by_date = {e["date"]: e for e in history}
        # Sólo el último (end) recibe equity
        assert "equity_usd" not in by_date["2026-04-21"]
        assert "equity_usd" not in by_date["2026-04-22"]
        assert by_date["2026-04-23"]["equity_usd"] == 100_000.0
        # Todas las weekdays tienen los closes
        for d in ("2026-04-21", "2026-04-22", "2026-04-23"):
            assert by_date[d]["spy_close"] == 500.0
            assert by_date[d]["qqq_close"] == 500.0

    def test_returns_counts(self, tmp_history):
        _write_lines(tmp_history, [
            {"date": "2026-04-21", "equity_usd": 100.0, "spy_close": 500.0,
             "qqq_close": 420.0},
        ])
        # Rango: martes 21 → jueves 23 (3 weekdays). Una existe.
        u, s = nav_tracker.backfill(
            start=date(2026, 4, 21),
            end=date(2026, 4, 23),
            equity_fetcher=lambda: 100_000.0,
            benchmark_fetcher=lambda t, d: 500.0,
            history_path=tmp_history,
        )
        # 2 actualizadas (22 y 23), 1 saltada (21)
        assert u == 2
        assert s == 1

    def test_raises_when_start_after_end(self, tmp_history):
        with pytest.raises(ValueError, match="start"):
            nav_tracker.backfill(
                start=date(2026, 4, 25),
                end=date(2026, 4, 24),
                equity_fetcher=lambda: 100.0,
                benchmark_fetcher=lambda t, d: 500.0,
                history_path=tmp_history,
            )

    def test_handles_equity_fetcher_failure(self, tmp_history):
        """Si el equity falla, no escribe equity en hoy pero igual fetchea benchmarks."""
        def boom():
            raise RuntimeError("alpaca down")

        nav_tracker.backfill(
            start=date(2026, 4, 22),  # miércoles
            end=date(2026, 4, 22),
            equity_fetcher=boom,
            benchmark_fetcher=lambda t, d: 500.0,
            history_path=tmp_history,
        )
        history = nav_tracker.load_history(tmp_history)
        assert len(history) == 1
        assert "equity_usd" not in history[0]
        assert history[0]["spy_close"] == 500.0


# ── Robustez ─────────────────────────────────────────────────────────────────


class TestRobustness:
    def test_concurrent_upserts_dont_corrupt_file(self, tmp_history):
        """Smoke test secuencial: muchas upserts seguidas no corrompen el JSONL."""
        for i in range(20):
            nav_tracker.upsert_entry(
                {"date": f"2026-04-{i+1:02d}", "equity_usd": 100.0 + i},
                tmp_history,
            )
        history = nav_tracker.load_history(tmp_history)
        assert len(history) == 20
        # Todas las fechas únicas
        assert len({e["date"] for e in history}) == 20

    def test_rounding_preserved(self, tmp_history):
        nav_tracker.record_today(
            target_date=date(2026, 4, 24),  # viernes (día hábil)
            equity_fetcher=lambda: 100_000.123456,
            benchmark_fetcher=lambda t, d: 500.987654321,
            history_path=tmp_history,
        )
        history = nav_tracker.load_history(tmp_history)
        # equity a 2 decimales, closes a 4
        assert history[0]["equity_usd"] == 100_000.12
        assert history[0]["spy_close"] == 500.9877
