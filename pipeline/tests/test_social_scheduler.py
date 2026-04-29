"""
Tests del daily scheduler `pipeline.social.scheduler`.

Validamos:
  - Cálculo correcto del día del ciclo desde la fecha del portfolio.
  - Idempotencia: si el draft de hoy ya existe, no se regenera.
  - Queue de didácticos: pop correcto, persistencia.
  - Dispatcher: ejecuta solo las tareas del día actual.
  - Errores en una tarea no abortan las demás.
  - Newsletter quincenal: se schedulea solo cada 2 ciclos.
  - dry_run no toca API.

NO testeamos las llamadas reales a generate_post; mockeamos `_run_generate`
y `_run_carrousel_from_thread` para aislar la lógica del scheduler.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.social import scheduler
from pipeline.social.scheduler import (
    CYCLE_SCHEDULE,
    cycle_count_since,
    day_of_cycle,
    run_today,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_dirs(tmp_path: Path, monkeypatch):
    """
    Apunta drafts/, approved/ y la queue a tmp_path para no contaminar el
    repo durante tests. También resetea PIPELINE_OUTPUTS para el portfolio.
    """
    drafts = tmp_path / "drafts"
    approved = tmp_path / "approved"
    state = tmp_path / "state"
    pipeline_outputs = tmp_path / "outputs"
    drafts.mkdir(parents=True)
    approved.mkdir(parents=True)
    state.mkdir(parents=True)
    pipeline_outputs.mkdir(parents=True)

    queue_file = state / "didactico_queue.json"

    monkeypatch.setattr(scheduler, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(scheduler, "APPROVED_DIR", approved)
    monkeypatch.setattr(scheduler, "STATE_DIR", state)
    monkeypatch.setattr(scheduler, "DIDACTICO_QUEUE_FILE", queue_file)
    monkeypatch.setattr(scheduler, "PIPELINE_OUTPUTS", pipeline_outputs)

    return {
        "drafts": drafts,
        "approved": approved,
        "state": state,
        "queue": queue_file,
        "outputs": pipeline_outputs,
    }


def _seed_portfolio(outputs_dir: Path, portfolio_date: date) -> None:
    """Crea un portfolio_<date>.json mínimo para anclar el ciclo."""
    p = outputs_dir / f"portfolio_{portfolio_date.isoformat()}.json"
    p.write_text(
        json.dumps({"cycle_id": "test", "holdings": []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _seed_queue(queue_file: Path, concepts: list[str]) -> None:
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(json.dumps(concepts, ensure_ascii=False), encoding="utf-8")


def _seed_existing_draft(drafts_dir: Path, today: date, post_type: str) -> Path:
    p = drafts_dir / f"post_{today.isoformat()}_{post_type}.json"
    p.write_text("{}", encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# day_of_cycle
# ─────────────────────────────────────────────────────────────────────────────


class TestDayOfCycle:
    def test_same_day_as_portfolio_is_day_1(self):
        d = day_of_cycle(date(2026, 4, 25), cycle_start=date(2026, 4, 25))
        assert d == 1

    def test_day_5(self):
        d = day_of_cycle(date(2026, 4, 29), cycle_start=date(2026, 4, 25))
        assert d == 5

    def test_day_20(self):
        d = day_of_cycle(date(2026, 5, 14), cycle_start=date(2026, 4, 25))
        assert d == 20

    def test_wraps_after_20(self):
        # Día 21 después del start = día 1 del nuevo ciclo (wrap)
        d = day_of_cycle(date(2026, 5, 15), cycle_start=date(2026, 4, 25))
        assert d == 1

    def test_no_portfolio_returns_none(self, isolated_dirs):
        d = day_of_cycle(date(2026, 4, 25))  # sin cycle_start, sin portfolio
        assert d is None

    def test_today_before_start_returns_none(self):
        d = day_of_cycle(date(2026, 4, 20), cycle_start=date(2026, 4, 25))
        assert d is None


class TestCycleCountSince:
    def test_zero_cycles_first_day(self):
        assert cycle_count_since(date(2026, 4, 25), date(2026, 4, 25)) == 0

    def test_one_cycle_at_day_20(self):
        assert cycle_count_since(date(2026, 4, 25), date(2026, 5, 15)) == 1

    def test_two_cycles_at_day_40(self):
        assert cycle_count_since(date(2026, 4, 25), date(2026, 6, 4)) == 2


# ─────────────────────────────────────────────────────────────────────────────
# Queue de didácticos
# ─────────────────────────────────────────────────────────────────────────────


class TestDidacticoQueue:
    def test_pop_returns_first_concept(self, isolated_dirs):
        _seed_queue(isolated_dirs["queue"], ["moat", "rotation"])
        c = scheduler._pop_didactico_concept()
        assert c == "moat"
        # Queue persistida sin el primero
        rest = json.loads(isolated_dirs["queue"].read_text(encoding="utf-8"))
        assert rest == ["rotation"]

    def test_pop_empty_returns_none(self, isolated_dirs):
        _seed_queue(isolated_dirs["queue"], [])
        assert scheduler._pop_didactico_concept() is None

    def test_no_queue_file_returns_none(self, isolated_dirs):
        # archivo inexistente
        assert scheduler._pop_didactico_concept() is None

    def test_invalid_json_returns_none(self, isolated_dirs):
        isolated_dirs["queue"].write_text("not json", encoding="utf-8")
        assert scheduler._pop_didactico_concept() is None

    def test_non_list_json_ignored(self, isolated_dirs):
        isolated_dirs["queue"].write_text(
            json.dumps({"foo": "bar"}), encoding="utf-8"
        )
        assert scheduler._pop_didactico_concept() is None


# ─────────────────────────────────────────────────────────────────────────────
# Idempotencia
# ─────────────────────────────────────────────────────────────────────────────


class TestIdempotency:
    def test_existing_draft_in_drafts_blocks_regeneration(self, isolated_dirs):
        today = date(2026, 4, 25)
        _seed_existing_draft(isolated_dirs["drafts"], today, "didactico")
        assert scheduler._draft_exists_today("didactico", today) is True

    def test_existing_draft_in_approved_blocks_regeneration(self, isolated_dirs):
        today = date(2026, 4, 25)
        _seed_existing_draft(isolated_dirs["approved"], today, "thread_post_ciclo")
        assert scheduler._draft_exists_today("thread_post_ciclo", today) is True

    def test_no_draft_returns_false(self, isolated_dirs):
        assert scheduler._draft_exists_today("didactico", date(2026, 4, 25)) is False

    def test_engagement_reply_with_slug_also_matches(self, isolated_dirs):
        # engagement_reply tiene filename con slug del handle
        today = date(2026, 4, 25)
        f = isolated_dirs["drafts"] / f"post_{today}_engagement_reply_mkiguel.json"
        f.write_text("{}", encoding="utf-8")
        assert scheduler._draft_exists_today("engagement_reply", today) is True


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher: run_today
# ─────────────────────────────────────────────────────────────────────────────


class TestRunTodayNoAnchor:
    def test_no_portfolio_returns_skipped(self, isolated_dirs):
        # Sin portfolio anchor, day_of_cycle devuelve None
        summary = run_today(
            today=date(2026, 4, 25),
            review=False,
            notify=False,
            dry_run=True,
        )
        assert summary["day_of_cycle"] is None
        assert "no-cycle-anchor" in summary["skipped"]
        assert summary["drafts_generated"] == []


class TestRunTodayDay1:
    def test_day_1_runs_thread_and_carrousel(self, isolated_dirs):
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))

        with patch.object(
            scheduler, "_run_generate", return_value={"_fileName": "thread.json"}
        ) as mock_gen, patch.object(
            scheduler,
            "_run_carrousel_from_thread",
            return_value={"_fileName": "carrousel.json"},
        ) as mock_carrousel:
            summary = run_today(
                today=date(2026, 4, 25),
                review=False,
                notify=False,
                dry_run=True,
            )

        assert summary["day_of_cycle"] == 1
        assert "thread_post_ciclo" in summary["tasks_attempted"]
        assert "carrousel_ig_from_thread" in summary["tasks_attempted"]
        assert mock_gen.call_count == 1
        assert mock_carrousel.call_count == 1
        assert len(summary["drafts_generated"]) == 2


class TestRunTodayDidacticoDay:
    def test_day_5_pops_from_queue_and_generates(self, isolated_dirs):
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))
        _seed_queue(isolated_dirs["queue"], ["moat", "rotation"])

        # dry_run=False para que el pop persista a disk (run real, generate mockeado).
        with patch.object(
            scheduler, "_run_generate", return_value={"_fileName": "didactico.json"}
        ) as mock_gen:
            summary = run_today(
                today=date(2026, 4, 29),  # día 5
                review=False,
                notify=False,
                dry_run=False,
            )

        assert summary["day_of_cycle"] == 5
        # Una sola tarea (didactico), una sola gen
        assert mock_gen.call_count == 1
        # El concepto popeado fue "moat"
        call_kwargs = mock_gen.call_args.kwargs
        assert call_kwargs["extra_kwargs"]["concept"] == "moat"
        # Queue se actualizó
        rest = json.loads(isolated_dirs["queue"].read_text(encoding="utf-8"))
        assert rest == ["rotation"]
        # Summary refleja el concepto
        assert summary["drafts_generated"][0]["concept"] == "moat"

    def test_day_5_dry_run_does_not_pop_queue(self, isolated_dirs):
        """Regresión: dry-run no debe consumir conceptos del queue."""
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))
        _seed_queue(isolated_dirs["queue"], ["moat", "rotation"])

        with patch.object(
            scheduler, "_run_generate", return_value={"_fileName": "didactico.json"}
        ):
            run_today(
                today=date(2026, 4, 29),
                review=False,
                notify=False,
                dry_run=True,
            )

        # Queue intacto: dry-run no debe persistir cambios.
        rest = json.loads(isolated_dirs["queue"].read_text(encoding="utf-8"))
        assert rest == ["moat", "rotation"]

    def test_empty_queue_skips_didactico(self, isolated_dirs):
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))
        _seed_queue(isolated_dirs["queue"], [])

        with patch.object(scheduler, "_run_generate") as mock_gen:
            summary = run_today(
                today=date(2026, 4, 29),
                review=False,
                notify=False,
                dry_run=True,
            )

        assert mock_gen.call_count == 0
        assert "didactico:empty-queue" in summary["skipped"]


class TestRunTodayIdempotency:
    def test_existing_draft_skips_generation(self, isolated_dirs):
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))
        _seed_queue(isolated_dirs["queue"], ["moat"])
        # Pre-existe el draft de hoy
        _seed_existing_draft(isolated_dirs["drafts"], date(2026, 4, 29), "didactico")

        with patch.object(scheduler, "_run_generate") as mock_gen:
            summary = run_today(
                today=date(2026, 4, 29),
                review=False,
                notify=False,
                dry_run=True,
            )

        assert mock_gen.call_count == 0
        assert "didactico:exists" in summary["skipped"]
        # Queue NO se tocó (no popeamos si no generamos)
        rest = json.loads(isolated_dirs["queue"].read_text(encoding="utf-8"))
        assert rest == ["moat"]


class TestRunTodayDayWithoutTask:
    def test_day_2_does_nothing(self, isolated_dirs):
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))

        with patch.object(scheduler, "_run_generate") as mock_gen:
            summary = run_today(
                today=date(2026, 4, 26),  # día 2
                review=False,
                notify=False,
                dry_run=True,
            )

        assert summary["day_of_cycle"] == 2
        assert summary["tasks_attempted"] == []
        assert mock_gen.call_count == 0


class TestRunTodayNewsletterBicycle:
    def test_first_cycle_day_20_runs_newsletter(self, isolated_dirs):
        # full_cycles = 0, 0 % 2 == 0 → SI corre
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))

        with patch.object(
            scheduler, "_run_generate", return_value={"_fileName": "nl.json"}
        ) as mock_gen:
            summary = run_today(
                today=date(2026, 5, 14),  # día 20
                review=False,
                notify=False,
                dry_run=True,
            )

        assert summary["day_of_cycle"] == 20
        assert mock_gen.call_count == 1
        call_args = mock_gen.call_args
        assert call_args.args[0] == "newsletter"

    def test_second_cycle_day_20_skips_newsletter(self, isolated_dirs):
        # Segundo ciclo: full_cycles = 1, 1 % 2 == 1 → SKIP
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))

        with patch.object(scheduler, "_run_generate") as mock_gen:
            summary = run_today(
                today=date(2026, 6, 3),  # día 20 del 2do ciclo (start + 39 días)
                cycle_start=date(2026, 4, 25),
                review=False,
                notify=False,
                dry_run=True,
            )

        # Día 20 del segundo ciclo
        assert summary["day_of_cycle"] == 20
        assert "newsletter:not-bicycle" in summary["skipped"]
        assert mock_gen.call_count == 0


class TestRunTodayErrors:
    def test_one_task_fails_others_continue(self, isolated_dirs):
        _seed_portfolio(isolated_dirs["outputs"], date(2026, 4, 25))

        # Mock que tira excepción para thread, OK para carrousel
        def gen_side_effect(*a, **kw):
            raise RuntimeError("boom thread")

        with patch.object(
            scheduler, "_run_generate", side_effect=gen_side_effect
        ), patch.object(
            scheduler,
            "_run_carrousel_from_thread",
            return_value={"_fileName": "carrousel.json"},
        ) as mock_carrousel:
            summary = run_today(
                today=date(2026, 4, 25),
                review=False,
                notify=False,
                dry_run=True,
            )

        # El error de thread no debe bloquear el carrousel
        assert mock_carrousel.call_count == 1
        # El error está reportado
        assert any("boom thread" in str(e) for _, e in summary["errors"])
        # El carrousel se generó (a pesar del fallo del thread)
        gens = [d["type"] for d in summary["drafts_generated"]]
        assert "carrousel_ig" in gens


class TestCycleScheduleConfig:
    def test_thread_on_day_1(self):
        kinds = [t["kind"] for t in CYCLE_SCHEDULE.get(1, [])]
        assert "thread_post_ciclo" in kinds
        assert "carrousel_ig_from_thread" in kinds

    def test_didacticos_distributed(self):
        # Días 5, 9, 13, 17 deberían tener didactico_from_queue
        for d in (5, 9, 13, 17):
            kinds = [t["kind"] for t in CYCLE_SCHEDULE.get(d, [])]
            assert "didactico_from_queue" in kinds, f"día {d} sin didactico"

    def test_newsletter_only_on_day_20(self):
        kinds = [t["kind"] for t in CYCLE_SCHEDULE.get(20, [])]
        assert "newsletter_bicycle" in kinds
