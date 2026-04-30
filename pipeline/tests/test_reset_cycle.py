"""
Tests del módulo `pipeline.reset_cycle`.

Validamos:
  - dry_run nunca toca disk ni Alpaca.
  - liquidate_alpaca_positions detecta cuenta vacía y caso con posiciones.
  - liquidate refusa correr si ALPACA_BASE_URL no es paper.
  - archive_cycle_outputs mueve los prefijos correctos y deja el resto en paz.
  - archive filtra `equity_usd` de nav_history.jsonl preservando benchmarks.
  - reset_state borra solo lo que debe.
  - run() aborta si liquidate falla.

NO hacemos calls reales a Alpaca; mockeamos el TradingClient.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pipeline import reset_cycle
from pipeline.reset_cycle import (
    archive_cycle_outputs,
    liquidate_alpaca_positions,
    reset_state,
    run,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dirs(tmp_path: Path, monkeypatch):
    """Aísla outputs/ y state/ a tmp_path para no contaminar el repo."""
    outputs = tmp_path / "outputs"
    state = tmp_path / "state"
    outputs.mkdir()
    state.mkdir()

    monkeypatch.setattr(reset_cycle, "OUTPUTS_DIR", outputs)
    monkeypatch.setattr(reset_cycle, "STATE_DIR", state)
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

    return {"outputs": outputs, "state": state}


def _seed_test_cycle(outputs_dir: Path) -> list[Path]:
    """Crea archivos típicos del test cycle en outputs."""
    files = [
        outputs_dir / "analysis_2026-04-21.json",
        outputs_dir / "analysis_2026-04-22.json",
        outputs_dir / "debate_2026-04-22.json",
        outputs_dir / "portfolio_2026-04-22.json",
        outputs_dir / "orders_2026-04-22.jsonl",
        outputs_dir / "filtered_2026-04-21.csv",
        outputs_dir / "analyst_run.log",
        outputs_dir / "analyst_retry.log",
    ]
    for f in files:
        f.write_text("{}", encoding="utf-8")
    # Archivo que NO debe archivarse
    (outputs_dir / "cost_log.jsonl").write_text(
        '{"role": "analyst"}\n', encoding="utf-8"
    )
    # Subdir que NO debe tocarse
    social = outputs_dir / "social"
    social.mkdir()
    (social / "draft.json").write_text("{}", encoding="utf-8")
    return files


def _seed_nav_history(outputs_dir: Path) -> Path:
    """Crea un nav_history.jsonl mixto: con y sin equity_usd."""
    p = outputs_dir / "nav_history.jsonl"
    lines = [
        {"date": "2026-04-20", "spy_close": 708.72, "qqq_close": 646.79},
        {"date": "2026-04-21", "spy_close": 704.08, "qqq_close": 644.33},
        {
            "date": "2026-04-22",
            "equity_usd": 100000.0,
            "spy_close": 711.21,
            "qqq_close": 655.11,
        },
        {
            "date": "2026-04-23",
            "equity_usd": 99500.0,
            "spy_close": 708.45,
            "qqq_close": 651.42,
        },
    ]
    p.write_text(
        "\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8"
    )
    return p


def _make_position(symbol: str, qty: int, market_value: float) -> MagicMock:
    p = MagicMock()
    p.symbol = symbol
    p.qty = qty
    p.market_value = market_value
    return p


# ─────────────────────────────────────────────────────────────────────────────
# liquidate_alpaca_positions
# ─────────────────────────────────────────────────────────────────────────────


class TestLiquidate:
    def test_dry_run_does_not_close(self, isolated_dirs):
        client = MagicMock()
        client.get_all_positions.return_value = [
            _make_position("AAPL", 10, 1500.0),
            _make_position("MSFT", 5, 2000.0),
        ]
        result = liquidate_alpaca_positions(dry_run=True, client=client)

        assert result.ok is True
        assert result.dry_run is True
        client.close_all_positions.assert_not_called()
        # El detail menciona DRY-RUN
        assert any("DRY-RUN" in d for d in result.details)

    def test_no_positions_is_noop(self, isolated_dirs):
        client = MagicMock()
        client.get_all_positions.return_value = []
        result = liquidate_alpaca_positions(dry_run=False, client=client)

        assert result.ok is True
        client.close_all_positions.assert_not_called()
        assert any("sin posiciones" in d.lower() or "no hay posiciones" in d.lower()
                   for d in result.details)

    def test_real_run_closes(self, isolated_dirs):
        client = MagicMock()
        client.get_all_positions.return_value = [
            _make_position("AAPL", 10, 1500.0)
        ]
        client.close_all_positions.return_value = [{"id": "abc"}]
        result = liquidate_alpaca_positions(dry_run=False, client=client)

        assert result.ok is True
        assert result.dry_run is False
        client.close_all_positions.assert_called_once_with(cancel_orders=True)

    def test_refuses_non_paper(self, isolated_dirs, monkeypatch):
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        client = MagicMock()
        result = liquidate_alpaca_positions(dry_run=False, client=client)

        assert result.ok is False
        assert "paper" in (result.error or "").lower()
        client.get_all_positions.assert_not_called()

    def test_handles_get_positions_error(self, isolated_dirs):
        client = MagicMock()
        client.get_all_positions.side_effect = RuntimeError("api down")
        result = liquidate_alpaca_positions(dry_run=False, client=client)

        assert result.ok is False
        assert "api down" in (result.error or "")

    def test_handles_close_error(self, isolated_dirs):
        client = MagicMock()
        client.get_all_positions.return_value = [_make_position("AAPL", 1, 100.0)]
        client.close_all_positions.side_effect = RuntimeError("boom")
        result = liquidate_alpaca_positions(dry_run=False, client=client)

        assert result.ok is False
        assert "boom" in (result.error or "")


# ─────────────────────────────────────────────────────────────────────────────
# archive_cycle_outputs
# ─────────────────────────────────────────────────────────────────────────────


class TestArchive:
    def test_dry_run_does_not_move(self, isolated_dirs):
        seeded = _seed_test_cycle(isolated_dirs["outputs"])
        result = archive_cycle_outputs(
            label="cycle-0-test",
            dry_run=True,
            outputs_dir=isolated_dirs["outputs"],
        )

        assert result.ok is True
        assert all(f.exists() for f in seeded), "dry-run no debe mover archivos"
        # No debería haber creado el archive dir
        assert not (isolated_dirs["outputs"] / "archive").exists()

    def test_real_run_moves_and_preserves_others(self, isolated_dirs):
        seeded = _seed_test_cycle(isolated_dirs["outputs"])
        cost_log = isolated_dirs["outputs"] / "cost_log.jsonl"
        social = isolated_dirs["outputs"] / "social"

        result = archive_cycle_outputs(
            label="cycle-0-test",
            dry_run=False,
            outputs_dir=isolated_dirs["outputs"],
        )

        assert result.ok is True

        archive_dir = isolated_dirs["outputs"] / "archive" / "cycle-0-test"
        assert archive_dir.exists()
        # Todos los seeded files se movieron
        for f in seeded:
            assert not f.exists()
            assert (archive_dir / f.name).exists()

        # cost_log y social/ siguen en su lugar
        assert cost_log.exists()
        assert social.exists()
        assert (social / "draft.json").exists()

    def test_refuses_to_overwrite_existing_archive(self, isolated_dirs):
        archive_dir = isolated_dirs["outputs"] / "archive" / "cycle-0-test"
        archive_dir.mkdir(parents=True)
        _seed_test_cycle(isolated_dirs["outputs"])

        result = archive_cycle_outputs(
            label="cycle-0-test",
            dry_run=False,
            outputs_dir=isolated_dirs["outputs"],
        )
        assert result.ok is False
        assert "ya existe" in (result.error or "").lower()

    def test_strips_equity_usd_preserves_benchmarks(self, isolated_dirs):
        nav = _seed_nav_history(isolated_dirs["outputs"])
        _seed_test_cycle(isolated_dirs["outputs"])

        archive_cycle_outputs(
            label="cycle-0-test",
            dry_run=False,
            outputs_dir=isolated_dirs["outputs"],
        )

        # nav_history.jsonl sigue existiendo
        assert nav.exists()
        # Backup creado
        backup = nav.with_suffix(".pre-reset.jsonl")
        assert backup.exists()

        # Equity_usd ya no está en ninguna entry
        entries = [json.loads(l) for l in nav.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert all("equity_usd" not in e for e in entries)
        # Pero los benchmarks sobreviven
        assert all("spy_close" in e and "qqq_close" in e for e in entries)
        # Cantidad de entries preservada
        assert len(entries) == 4

    def test_no_files_to_archive(self, isolated_dirs):
        # Solo cost_log + social/, no test cycle artifacts
        (isolated_dirs["outputs"] / "cost_log.jsonl").write_text("{}", encoding="utf-8")
        result = archive_cycle_outputs(
            label="cycle-0-test",
            dry_run=False,
            outputs_dir=isolated_dirs["outputs"],
        )
        assert result.ok is True
        assert any("nada" in d.lower() or "no hay" in d.lower() for d in result.details)


# ─────────────────────────────────────────────────────────────────────────────
# reset_state
# ─────────────────────────────────────────────────────────────────────────────


class TestResetState:
    def test_dry_run_does_not_delete(self, isolated_dirs):
        sd = isolated_dirs["state"]
        (sd / "current_holdings.json").write_text("{}", encoding="utf-8")
        (sd / "budget.json").write_text("{}", encoding="utf-8")  # NO se toca

        result = reset_state(dry_run=True, state_dir=sd)
        assert result.ok is True
        assert (sd / "current_holdings.json").exists()
        assert (sd / "budget.json").exists()

    def test_real_run_deletes_and_preserves_budget(self, isolated_dirs):
        sd = isolated_dirs["state"]
        (sd / "current_holdings.json").write_text("{}", encoding="utf-8")
        (sd / "last_cycle.json").write_text("{}", encoding="utf-8")
        (sd / "budget.json").write_text("{}", encoding="utf-8")

        result = reset_state(dry_run=False, state_dir=sd)
        assert result.ok is True
        assert not (sd / "current_holdings.json").exists()
        assert not (sd / "last_cycle.json").exists()
        # budget.json preservado
        assert (sd / "budget.json").exists()

    def test_state_dir_not_existing(self, isolated_dirs, tmp_path):
        result = reset_state(dry_run=False, state_dir=tmp_path / "nonexistent")
        assert result.ok is True

    def test_no_files_to_delete(self, isolated_dirs):
        result = reset_state(dry_run=False, state_dir=isolated_dirs["state"])
        assert result.ok is True
        assert any("no hay" in d.lower() for d in result.details)


# ─────────────────────────────────────────────────────────────────────────────
# run() orchestrator
# ─────────────────────────────────────────────────────────────────────────────


class TestRunOrchestrator:
    def test_dry_run_default(self, isolated_dirs, monkeypatch):
        # Mock liquidate para no necesitar Alpaca
        def mock_liquidate(**kwargs):
            from pipeline.reset_cycle import StepResult
            return StepResult(name="liquidate", dry_run=kwargs["dry_run"])

        monkeypatch.setattr(reset_cycle, "liquidate_alpaca_positions", mock_liquidate)
        _seed_test_cycle(isolated_dirs["outputs"])

        summary = run(label="test", confirm=False)
        assert summary.dry_run is True
        # Nada se movió
        assert (isolated_dirs["outputs"] / "portfolio_2026-04-22.json").exists()

    def test_aborts_if_liquidate_fails(self, isolated_dirs, monkeypatch):
        def mock_liquidate(**kwargs):
            from pipeline.reset_cycle import StepResult
            r = StepResult(name="liquidate", dry_run=kwargs["dry_run"])
            r.ok = False
            r.error = "alpaca down"
            return r

        monkeypatch.setattr(reset_cycle, "liquidate_alpaca_positions", mock_liquidate)
        _seed_test_cycle(isolated_dirs["outputs"])

        summary = run(label="test", confirm=True)
        assert summary.liquidate is not None
        assert summary.liquidate.ok is False
        # Archive y reset NO deberían haber corrido
        assert summary.archive is None
        assert summary.reset_state is None
        # Disk intacto
        assert (isolated_dirs["outputs"] / "portfolio_2026-04-22.json").exists()

    def test_skip_flags_work(self, isolated_dirs, monkeypatch):
        def mock_liquidate(**kwargs):
            from pipeline.reset_cycle import StepResult
            return StepResult(name="liquidate", dry_run=kwargs["dry_run"])

        monkeypatch.setattr(reset_cycle, "liquidate_alpaca_positions", mock_liquidate)

        summary = run(
            label="test",
            confirm=True,
            skip_liquidate=True,
            skip_archive=True,
            skip_reset=True,
        )
        assert summary.liquidate is None
        assert summary.archive is None
        assert summary.reset_state is None

    def test_full_real_run_e2e(self, isolated_dirs, monkeypatch):
        """Smoke test del orden completo con liquidate mockeado."""
        client = MagicMock()
        client.get_all_positions.return_value = []
        monkeypatch.setattr(
            reset_cycle, "liquidate_alpaca_positions",
            lambda **kw: reset_cycle.StepResult(name="liquidate", dry_run=kw["dry_run"]),
        )
        _seed_test_cycle(isolated_dirs["outputs"])
        (isolated_dirs["state"] / "current_holdings.json").write_text("{}", encoding="utf-8")

        summary = run(label="cycle-0-test", confirm=True)

        assert summary.dry_run is False
        assert summary.liquidate.ok
        assert summary.archive.ok
        assert summary.reset_state.ok
        # Disk realmente modificado
        assert not (isolated_dirs["outputs"] / "portfolio_2026-04-22.json").exists()
        archive_dir = isolated_dirs["outputs"] / "archive" / "cycle-0-test"
        assert (archive_dir / "portfolio_2026-04-22.json").exists()
        assert not (isolated_dirs["state"] / "current_holdings.json").exists()
