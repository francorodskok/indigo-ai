"""
Tests del driver `pipeline.daily_tasks`.

Validamos:
  - dry_run no toca nav_tracker ni el scheduler.
  - Una falla en nav no aborta social, y viceversa.
  - --skip-nav y --skip-social funcionan.
  - El exit code es siempre 0 (Task Scheduler no debe reintentar).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from pipeline import daily_tasks


class TestRun:
    def test_dry_run_skips_nav_fetch(self):
        """En dry_run, nav_tracker.record_today NO se llama."""
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_sched.return_value = {
                "day_of_cycle": None,
                "drafts_generated": [],
                "skipped": ["no-cycle-anchor"],
            }
            result = daily_tasks.run(today=date(2026, 4, 29), dry_run=True)

        mock_nav.assert_not_called()
        # Social SÍ corre en dry_run (con dry_run=True propagado).
        mock_sched.assert_called_once()
        assert mock_sched.call_args.kwargs["dry_run"] is True
        assert result["nav"]["ok"]
        assert result["social"]["ok"]

    def test_live_run_calls_both(self):
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_nav.return_value = {
                "date": "2026-04-29",
                "equity_usd": 99500.0,
                "spy_close": 715.0,
                "qqq_close": 660.0,
            }
            mock_sched.return_value = {
                "day_of_cycle": 5,
                "drafts_generated": [{"type": "didactico"}],
                "skipped": [],
            }
            result = daily_tasks.run(today=date(2026, 4, 29), dry_run=False)

        mock_nav.assert_called_once()
        mock_sched.assert_called_once()
        assert result["nav"]["ok"] is True
        assert result["nav"]["entry"]["equity_usd"] == 99500.0
        assert result["social"]["ok"] is True

    def test_nav_failure_does_not_abort_social(self):
        """Si nav_tracker tira, igual corremos el scheduler social."""
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_nav.side_effect = RuntimeError("alpaca down")
            mock_sched.return_value = {
                "day_of_cycle": 1,
                "drafts_generated": [{"type": "thread_post_ciclo"}],
                "skipped": [],
            }
            result = daily_tasks.run(today=date(2026, 4, 29), dry_run=False)

        assert result["nav"]["ok"] is False
        assert "alpaca down" in result["nav"]["detail"]
        # Social corrió aunque nav falló.
        mock_sched.assert_called_once()
        assert result["social"]["ok"] is True

    def test_social_failure_does_not_abort_nav(self):
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_nav.return_value = {
                "date": "2026-04-29",
                "equity_usd": 99500.0,
                "spy_close": 715.0,
                "qqq_close": 660.0,
            }
            mock_sched.side_effect = RuntimeError("scheduler bug")
            result = daily_tasks.run(today=date(2026, 4, 29), dry_run=False)

        assert result["nav"]["ok"] is True
        assert result["social"]["ok"] is False
        assert "scheduler bug" in result["social"]["detail"]

    def test_nav_returns_none_is_failure(self):
        """record_today devuelve None si Alpaca no respondió. Lo tratamos como fail."""
        with patch("pipeline.nav_tracker.record_today", return_value=None), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_sched.return_value = {"day_of_cycle": 2, "drafts_generated": [], "skipped": []}
            result = daily_tasks.run(today=date(2026, 4, 29), dry_run=False)

        assert result["nav"]["ok"] is False
        assert "None" in result["nav"]["detail"]

    def test_skip_nav_flag(self):
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_sched.return_value = {"day_of_cycle": 5, "drafts_generated": [], "skipped": []}
            result = daily_tasks.run(today=date(2026, 4, 29), skip_nav=True)

        mock_nav.assert_not_called()
        assert result["nav"]["skipped"] is True
        mock_sched.assert_called_once()

    def test_skip_social_flag(self):
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_nav.return_value = {
                "date": "2026-04-29",
                "equity_usd": 99500.0,
                "spy_close": 715.0,
                "qqq_close": 660.0,
            }
            result = daily_tasks.run(today=date(2026, 4, 29), skip_social=True)

        mock_nav.assert_called_once()
        mock_sched.assert_not_called()
        assert result["social"]["skipped"] is True


class TestPositionsStep:
    def test_positions_snapshot_runs_with_nav(self):
        """El snapshot de posiciones corre junto al NAV en un run live."""
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions") as mock_pos, \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_nav.return_value = {"date": "2026-04-29", "equity_usd": 99500.0}
            mock_pos.return_value = {
                "positions_count": 12,
                "total_unrealized_pl_usd": 1234.56,
                "total_unrealized_pl_pct": 1.25,
            }
            mock_sched.return_value = {"day_of_cycle": 5, "drafts_generated": [], "skipped": []}
            result = daily_tasks.run(today=date(2026, 4, 29), dry_run=False)

        mock_pos.assert_called_once()
        assert result["positions"]["ok"] is True
        assert result["positions"]["snapshot"]["positions_count"] == 12

    def test_positions_failure_does_not_abort_social(self):
        """Si el snapshot de posiciones falla, social igual corre."""
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", side_effect=RuntimeError("alpaca 500")), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_nav.return_value = {"date": "2026-04-29", "equity_usd": 99500.0}
            mock_sched.return_value = {"day_of_cycle": 5, "drafts_generated": [], "skipped": []}
            result = daily_tasks.run(today=date(2026, 4, 29), dry_run=False)

        assert result["positions"]["ok"] is False
        assert "alpaca 500" in result["positions"]["detail"]
        mock_sched.assert_called_once()

    def test_dry_run_skips_positions_fetch(self):
        with patch("pipeline.nav_tracker.record_today"), \
             patch("pipeline.position_tracker.record_positions") as mock_pos, \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_sched.return_value = {"day_of_cycle": None, "drafts_generated": [], "skipped": []}
            daily_tasks.run(today=date(2026, 4, 29), dry_run=True)

        mock_pos.assert_not_called()

    def test_skip_nav_also_skips_positions(self):
        """--skip-nav saltea posiciones (comparten el fetch de Alpaca)."""
        with patch("pipeline.position_tracker.record_positions") as mock_pos, \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_sched.return_value = {"day_of_cycle": 5, "drafts_generated": [], "skipped": []}
            result = daily_tasks.run(today=date(2026, 4, 29), skip_nav=True)

        mock_pos.assert_not_called()
        assert result["positions"]["skipped"] is True


class TestCli:
    def test_main_exits_0_even_on_failures(self):
        """Task Scheduler no debe reintentar — exit 0 siempre."""
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_nav.side_effect = RuntimeError("nav broke")
            mock_sched.side_effect = RuntimeError("social broke")
            exit_code = daily_tasks.main(["--date", "2026-04-29"])

        assert exit_code == 0

    def test_main_dry_run_flag(self):
        with patch("pipeline.nav_tracker.record_today") as mock_nav, \
             patch("pipeline.position_tracker.record_positions", return_value={
                 "positions_count": 0,
                 "total_unrealized_pl_usd": 0.0,
                 "total_unrealized_pl_pct": 0.0,
             }), \
             patch("pipeline.social.scheduler.run_today") as mock_sched:
            mock_sched.return_value = {"day_of_cycle": None, "drafts_generated": [], "skipped": []}
            exit_code = daily_tasks.main(["--date", "2026-04-29", "--dry-run"])

        assert exit_code == 0
        mock_nav.assert_not_called()  # dry-run no fetcha
        mock_sched.assert_called_once()
        assert mock_sched.call_args.kwargs["dry_run"] is True

    def test_main_invalid_date(self, capsys):
        with pytest.raises(SystemExit):
            daily_tasks.main(["--date", "no-fecha"])
