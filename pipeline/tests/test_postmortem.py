"""
Tests del módulo post-mortem (ADR 2026-04-23).

Commit 1: core (state, find_reference_portfolio, fetch_close_on_or_near,
compute_returns, save_postmortem_json).

Correr con: pytest pipeline/tests/test_postmortem.py -v

Ningún test llama a yfinance real: todos inyectan un fake via
monkeypatch sobre `_YFINANCE_FACTORY` o usan `price_fetcher=` directamente.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from pipeline import postmortem


# ── Fixtures y helpers ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_state_dir(tmp_path, monkeypatch):
    """
    Aísla el dir de state para que los tests no toquen el state real del proyecto.
    Todos los tests obtienen un INDIGO_STATE_DIR limpio bajo tmp_path.
    """
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setenv("INDIGO_STATE_DIR", str(state))
    yield state


@pytest.fixture(autouse=True)
def _isolate_outputs_dir(tmp_path, monkeypatch):
    """
    Los tests que tocan outputs_dir deben poder inyectar uno propio.
    Esta fixture default-patches el módulo para que apunte a un tmp vacío —
    evita que find_reference_portfolio lea los portfolio_*.json reales.
    """
    outputs = tmp_path / "outputs"
    outputs.mkdir(exist_ok=True)
    monkeypatch.setattr(postmortem, "OUTPUTS_DIR", outputs)
    yield outputs


def _make_portfolio_json(
    cycle_id: str = "2026-01-23",
    holdings: list[dict] | None = None,
    exits: list[dict] | None = None,
) -> dict:
    """Genera un dict con el schema de portfolio_YYYY-MM-DD.json."""
    if holdings is None:
        holdings = [
            {
                "ticker": "NVDA",
                "weight": 0.10,
                "action": "new",
                "previous_weight": 0.0,
                "conviction": 9,
                "rationale": "AI demand + CUDA moat.",
            },
            {
                "ticker": "MSFT",
                "weight": 0.08,
                "action": "add",
                "previous_weight": 0.05,
                "conviction": 8,
                "rationale": "Azure growth.",
            },
        ]
    if exits is None:
        exits = [
            {
                "ticker": "META",
                "previous_weight": 0.07,
                "reason": "Valuación estirada vs media 5y.",
            }
        ]
    return {
        "cycle_id": cycle_id,
        "generated_at": f"{cycle_id}T10:00:00+00:00",
        "holdings": holdings,
        "exits": exits,
        "cash_weight": 0.05,
    }


def _make_debate_json(veto_tickers: list[str] | None = None) -> dict:
    """Genera un dict de debate_*.json. veto_tickers entran como no_invertir."""
    veto_tickers = veto_tickers or []
    debates = [
        {
            "ticker": t,
            "verdict": {"decision": "no_invertir"},
        }
        for t in veto_tickers
    ]
    return {"debates": debates}


def _price_fetcher_from_dict(prices: dict[tuple[str, str], float | None]):
    """
    Construye un price_fetcher que busca (ticker, date_iso) en un dict.
    Si no está en el dict, retorna None.
    """
    def _fetch(ticker: str, target_date: date):
        key = (ticker, target_date.isoformat())
        return prices.get(key)
    return _fetch


# ── TestState (cadencia 90d) ─────────────────────────────────────────────────


class TestIsDue:
    def test_is_due_first_run_returns_true(self):
        """Sin state file → due."""
        due, reason = postmortem.is_due(today=date(2026, 7, 21))
        assert due is True
        assert "primer" in reason.lower() or "no hay state" in reason.lower()

    def test_is_due_respects_90d_threshold_below(self):
        """Hace 89d → NOT due."""
        postmortem.save_last_postmortem(
            {"last_run": (date(2026, 7, 21) - timedelta(days=89)).isoformat()}
        )
        due, _ = postmortem.is_due(today=date(2026, 7, 21))
        assert due is False

    def test_is_due_respects_90d_threshold_at(self):
        """Exactamente 90d → due."""
        postmortem.save_last_postmortem(
            {"last_run": (date(2026, 7, 21) - timedelta(days=90)).isoformat()}
        )
        due, _ = postmortem.is_due(today=date(2026, 7, 21))
        assert due is True

    def test_is_due_respects_state_dir_env(self, tmp_path, monkeypatch):
        """El INDIGO_STATE_DIR se honra correctamente."""
        custom = tmp_path / "custom_state"
        custom.mkdir()
        monkeypatch.setenv("INDIGO_STATE_DIR", str(custom))
        # Estado NO escrito en custom → is_due debe retornar True (primer run)
        due, _ = postmortem.is_due(today=date(2026, 7, 21))
        assert due is True
        # Ahora escribo uno reciente en custom → NOT due
        postmortem.save_last_postmortem(
            {"last_run": (date(2026, 7, 21) - timedelta(days=30)).isoformat()}
        )
        due, _ = postmortem.is_due(today=date(2026, 7, 21))
        assert due is False

    def test_is_due_retries_after_skip(self):
        """Si el último run fue 'skipped', se reintenta al día siguiente, no a 90d."""
        postmortem.save_last_postmortem(
            {
                "last_run": (date(2026, 7, 21) - timedelta(days=2)).isoformat(),
                "skipped": True,
            }
        )
        due, reason = postmortem.is_due(today=date(2026, 7, 21))
        assert due is True
        assert "skip" in reason.lower() or "reintento" in reason.lower()

    def test_is_due_skip_blocks_same_day(self):
        """Si ya corrió (aunque skip) hoy, no se reintenta hasta mañana."""
        postmortem.save_last_postmortem(
            {
                "last_run": date(2026, 7, 21).isoformat(),
                "skipped": True,
            }
        )
        due, _ = postmortem.is_due(today=date(2026, 7, 21))
        assert due is False

    def test_is_due_handles_invalid_date(self):
        """last_run con string inválido → trata como 'nunca corrió'."""
        postmortem.save_last_postmortem({"last_run": "not-a-date"})
        due, _ = postmortem.is_due(today=date(2026, 7, 21))
        assert due is True

    def test_days_since_last_none_when_never_ran(self):
        assert postmortem.days_since_last_postmortem(today=date(2026, 7, 21)) is None

    def test_days_since_last_computes_correctly(self):
        postmortem.save_last_postmortem(
            {"last_run": date(2026, 4, 22).isoformat()}
        )
        assert postmortem.days_since_last_postmortem(today=date(2026, 7, 21)) == 90


# ── TestFindReferencePortfolio ───────────────────────────────────────────────


class TestFindReferencePortfolio:
    def test_returns_none_when_no_portfolios(self, tmp_path):
        """Sin portfolios en outputs_dir → None (primer post-mortem)."""
        result = postmortem.find_reference_portfolio(
            today=date(2026, 7, 21),
            outputs_dir=tmp_path,
        )
        assert result is None

    def test_finds_portfolio_exactly_90d_ago(self, tmp_path):
        """Portfolio exactamente a 90d es elegido."""
        target = date(2026, 4, 22)  # 90d antes de 2026-07-21
        (tmp_path / f"portfolio_{target.isoformat()}.json").write_text("{}")
        result = postmortem.find_reference_portfolio(
            today=date(2026, 7, 21),
            outputs_dir=tmp_path,
        )
        assert result is not None
        assert target.isoformat() in result.name

    def test_prefers_nearest_within_window(self, tmp_path):
        """Entre dos candidatos dentro de la ventana, elige el más cercano al target."""
        # target = 2026-04-22. Ventana ±7d.
        (tmp_path / "portfolio_2026-04-20.json").write_text("{}")  # delta 2
        (tmp_path / "portfolio_2026-04-25.json").write_text("{}")  # delta 3
        result = postmortem.find_reference_portfolio(
            today=date(2026, 7, 21),
            outputs_dir=tmp_path,
        )
        assert result is not None
        assert "2026-04-20" in result.name

    def test_skips_outside_window(self, tmp_path):
        """Portfolio fuera de ±7d no se considera."""
        # target = 2026-04-22. Este está a 10d de distancia.
        (tmp_path / "portfolio_2026-05-02.json").write_text("{}")
        result = postmortem.find_reference_portfolio(
            today=date(2026, 7, 21),
            outputs_dir=tmp_path,
        )
        assert result is None

    def test_ignores_malformed_filenames(self, tmp_path):
        """Archivos con nombre no-ISO son ignorados silenciosamente."""
        (tmp_path / "portfolio_latest.json").write_text("{}")
        (tmp_path / "portfolio_2026-xx-xx.json").write_text("{}")
        target = date(2026, 4, 22)
        (tmp_path / f"portfolio_{target.isoformat()}.json").write_text("{}")
        result = postmortem.find_reference_portfolio(
            today=date(2026, 7, 21),
            outputs_dir=tmp_path,
        )
        assert result is not None
        assert target.isoformat() in result.name

    def test_custom_lookback_days(self, tmp_path):
        """El lookback_days es parametrizable (útil para dev/testing)."""
        target = date(2026, 7, 11)  # 10d antes de 2026-07-21
        (tmp_path / f"portfolio_{target.isoformat()}.json").write_text("{}")
        # Con lookback default 90d, este no debería matchear
        default = postmortem.find_reference_portfolio(
            today=date(2026, 7, 21),
            outputs_dir=tmp_path,
        )
        assert default is None
        # Con lookback 10d, sí
        custom = postmortem.find_reference_portfolio(
            today=date(2026, 7, 21),
            lookback_days=10,
            outputs_dir=tmp_path,
        )
        assert custom is not None


# ── TestFetchCloseOnOrNear ───────────────────────────────────────────────────


@dataclass
class _FakeHistRow:
    """Mock de una fila de yfinance DataFrame (fecha + Close)."""
    close: float


class _FakeHistoryFrame:
    """
    Mock mínimo del DataFrame de yfinance.history().
    Soporta .index y .loc[idx, "Close"].
    """
    def __init__(self, rows: dict):
        """rows: {date: close}. Lookup preserva orden de inserción."""
        self._rows = rows

    @property
    def index(self):
        return list(self._rows.keys())

    def __len__(self):
        return len(self._rows)

    class _Loc:
        def __init__(self, rows):
            self._rows = rows

        def __getitem__(self, key):
            idx, col = key
            if col == "Close":
                return self._rows[idx]
            raise KeyError(col)

    @property
    def loc(self):
        return self._FakeHistoryFrame__class_loc(self._rows)

    # Nombre-mangling workaround for nested class access
    def __class_loc(self, rows):
        return self._Loc(rows)


class _FakeTicker:
    """Mock de yfinance.Ticker."""
    def __init__(self, history_map: dict):
        """history_map: dict {date: close}."""
        self._history_map = history_map

    def history(self, start: str, end: str):
        # Filtrar por rango
        start_d = date.fromisoformat(start)
        end_d = date.fromisoformat(end)
        filtered = {
            d: c for d, c in self._history_map.items()
            if start_d <= d < end_d
        }
        return _FakeHistoryFrame(filtered)


@pytest.fixture
def fake_yfinance(monkeypatch):
    """
    Inyecta una factory de tickers mock. Devuelve un helper para registrar
    series por ticker.
    """
    registry: dict[str, _FakeTicker] = {}

    def factory(symbol: str):
        return registry.get(symbol, _FakeTicker({}))

    monkeypatch.setattr(postmortem, "_YFINANCE_FACTORY", factory)

    def register(symbol: str, prices: dict[date, float]):
        registry[symbol] = _FakeTicker(prices)

    return register


class TestFetchCloseOnOrNear:
    def test_returns_close_on_exact_date(self, fake_yfinance):
        fake_yfinance("NVDA", {date(2026, 4, 22): 850.0})
        got = postmortem.fetch_close_on_or_near("NVDA", date(2026, 4, 22))
        assert got == 850.0

    def test_returns_nearest_within_window(self, fake_yfinance):
        """Si la fecha exacta no tiene trade (weekend), elige el más cercano."""
        fake_yfinance("NVDA", {
            date(2026, 4, 20): 840.0,
            date(2026, 4, 23): 855.0,
        })
        # 2026-04-22 es miércoles, pero forzamos que solo hay 20 y 23.
        got = postmortem.fetch_close_on_or_near("NVDA", date(2026, 4, 22))
        # 20 está a delta 2, 23 está a delta 1 → elige 23.
        assert got == 855.0

    def test_returns_none_when_delisted(self, fake_yfinance):
        """Ticker sin history (delisted/symbol changed) → None."""
        # No registramos XYZQ, el factory devuelve ticker vacío
        got = postmortem.fetch_close_on_or_near("XYZQ", date(2026, 4, 22))
        assert got is None

    def test_returns_none_on_invalid_close(self, fake_yfinance):
        """Close no-positivo (datos corruptos) → None."""
        fake_yfinance("BAD", {date(2026, 4, 22): 0.0})
        got = postmortem.fetch_close_on_or_near("BAD", date(2026, 4, 22))
        assert got is None

    def test_handles_yfinance_exception(self, monkeypatch):
        """Si yfinance tira (red, etc.), retorna None sin raise."""
        class _BrokenTicker:
            def history(self, start, end):
                raise RuntimeError("network down")

        def factory(symbol):
            return _BrokenTicker()

        monkeypatch.setattr(postmortem, "_YFINANCE_FACTORY", factory)
        got = postmortem.fetch_close_on_or_near("NVDA", date(2026, 4, 22))
        assert got is None


# ── TestComputeReturns ───────────────────────────────────────────────────────


class TestComputeReturns:
    def test_nominal_return_and_alpha(self):
        """Return = (today/entry - 1), alpha = return - benchmark."""
        portfolio = _make_portfolio_json(
            cycle_id="2026-04-22",
            holdings=[
                {"ticker": "NVDA", "weight": 0.1, "action": "new", "conviction": 9,
                 "previous_weight": 0.0, "rationale": "x"},
            ],
            exits=[],
        )
        # NVDA: 100 → 110 (+10%). SPY: 400 → 420 (+5%). Alpha = +5%.
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("SPY", "2026-04-22"): 400.0, ("SPY", "2026-07-21"): 420.0,
        }
        fetch = _price_fetcher_from_dict(prices)
        result = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21), price_fetcher=fetch,
        )
        assert len(result.positions) == 1
        pos = result.positions[0]
        assert pos.nominal_return == pytest.approx(0.10)
        assert pos.benchmark_return == pytest.approx(0.05)
        assert pos.alpha == pytest.approx(0.05)
        assert pos.contribution == pytest.approx(0.01)  # 0.1 * 0.10

    def test_weighted_aggregate(self):
        """El portfolio_return_weighted es la suma de contributions."""
        portfolio = _make_portfolio_json(
            cycle_id="2026-04-22",
            holdings=[
                {"ticker": "NVDA", "weight": 0.10, "action": "new", "conviction": 9,
                 "previous_weight": 0.0, "rationale": "x"},
                {"ticker": "MSFT", "weight": 0.05, "action": "new", "conviction": 8,
                 "previous_weight": 0.0, "rationale": "x"},
            ],
            exits=[],
        )
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 120.0,  # +20%
            ("MSFT", "2026-04-22"): 400.0, ("MSFT", "2026-07-21"): 420.0,  # +5%
            ("SPY", "2026-04-22"): 450.0, ("SPY", "2026-07-21"): 450.0,   # 0%
        }
        fetch = _price_fetcher_from_dict(prices)
        result = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21), price_fetcher=fetch,
        )
        # portfolio_return = 0.10*0.20 + 0.05*0.05 = 0.02 + 0.0025 = 0.0225
        assert result.portfolio_return_weighted == pytest.approx(0.0225)
        assert result.benchmark_return == pytest.approx(0.0)
        assert result.alpha_weighted == pytest.approx(0.0225)

    def test_exit_tagged_as_veto_when_debate_says_no_invertir(self):
        """Un exit cuyo ticker fue no_invertir en el debate → kind='veto'."""
        portfolio = _make_portfolio_json(
            cycle_id="2026-04-22",
            holdings=[],
            exits=[{"ticker": "META", "previous_weight": 0.07, "reason": "veto del debate"}],
        )
        debate = _make_debate_json(veto_tickers=["META"])
        prices = {
            ("META", "2026-04-22"): 500.0, ("META", "2026-07-21"): 550.0,
            ("SPY", "2026-04-22"): 400.0, ("SPY", "2026-07-21"): 410.0,
        }
        fetch = _price_fetcher_from_dict(prices)
        result = postmortem.compute_returns(
            portfolio, debate_data=debate,
            today=date(2026, 7, 21), price_fetcher=fetch,
        )
        assert len(result.exits) == 1
        assert result.exits[0].kind == "veto"
        # Counterfactual aún se computa (auditabilidad), pero no afecta el agregado
        assert result.exits[0].counterfactual_return == pytest.approx(0.10)

    def test_exit_tagged_as_rotation_when_not_in_debate_vetos(self):
        """Un exit normal (rotación) se taggea 'rotation'."""
        portfolio = _make_portfolio_json(
            cycle_id="2026-04-22",
            holdings=[],
            exits=[{"ticker": "META", "previous_weight": 0.07, "reason": "valuación"}],
        )
        debate = _make_debate_json(veto_tickers=[])  # no está META como veto
        prices = {
            ("META", "2026-04-22"): 500.0, ("META", "2026-07-21"): 550.0,
            ("SPY", "2026-04-22"): 400.0, ("SPY", "2026-07-21"): 410.0,
        }
        fetch = _price_fetcher_from_dict(prices)
        result = postmortem.compute_returns(
            portfolio, debate_data=debate,
            today=date(2026, 7, 21), price_fetcher=fetch,
        )
        assert result.exits[0].kind == "rotation"

    def test_missing_prices_registered_in_data_quality(self):
        """Ticker sin precios cae a data_quality.tickers_missing_price."""
        portfolio = _make_portfolio_json(
            cycle_id="2026-04-22",
            holdings=[
                {"ticker": "NVDA", "weight": 0.1, "action": "new", "conviction": 9,
                 "previous_weight": 0.0, "rationale": "x"},
                {"ticker": "DELISTED", "weight": 0.05, "action": "new", "conviction": 7,
                 "previous_weight": 0.0, "rationale": "x"},
            ],
            exits=[],
        )
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("SPY", "2026-04-22"): 400.0, ("SPY", "2026-07-21"): 420.0,
            # DELISTED no está → None
        }
        fetch = _price_fetcher_from_dict(prices)
        result = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21), price_fetcher=fetch,
        )
        assert "DELISTED" in result.data_quality["tickers_missing_price"]
        assert result.data_quality["partial"] is True
        # El agregado corre pero solo con las posiciones con datos válidos
        assert result.portfolio_return_weighted is not None

    def test_no_benchmark_prices_gives_none_alpha(self):
        """Si SPY no tiene precios, benchmark_return = None y alpha = None."""
        portfolio = _make_portfolio_json(
            cycle_id="2026-04-22",
            holdings=[
                {"ticker": "NVDA", "weight": 0.1, "action": "new", "conviction": 9,
                 "previous_weight": 0.0, "rationale": "x"},
            ],
            exits=[],
        )
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            # SPY ausente
        }
        fetch = _price_fetcher_from_dict(prices)
        result = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21), price_fetcher=fetch,
        )
        assert result.benchmark_return is None
        assert result.alpha_weighted is None
        assert result.positions[0].alpha is None

    def test_days_elapsed_correct(self):
        portfolio = _make_portfolio_json(cycle_id="2026-04-22")
        result = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict({}),
        )
        assert result.days_elapsed == 90


# ── TestSavePostmortemJson ───────────────────────────────────────────────────


class TestSavePostmortemJson:
    def test_writes_file_with_iso_date(self, tmp_path):
        portfolio = _make_portfolio_json(cycle_id="2026-04-22")
        numbers = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict({}),
        )
        path = postmortem.save_postmortem_json(
            numbers, today=date(2026, 7, 21), outputs_dir=tmp_path,
        )
        assert path.exists()
        assert path.name == "postmortem_2026-07-21.json"

    def test_persisted_json_has_all_top_level_keys(self, tmp_path):
        portfolio = _make_portfolio_json(cycle_id="2026-04-22")
        numbers = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict({}),
        )
        path = postmortem.save_postmortem_json(
            numbers, today=date(2026, 7, 21), outputs_dir=tmp_path,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in [
            "generated_at", "portfolio_date", "days_elapsed", "benchmark",
            "benchmark_return", "portfolio_return_weighted", "alpha_weighted",
            "positions", "exits", "data_quality",
        ]:
            assert key in data, f"Missing key: {key}"

    def test_persisted_json_roundtrip(self, tmp_path):
        """El JSON guardado es re-parseable y mantiene los valores."""
        portfolio = _make_portfolio_json(
            cycle_id="2026-04-22",
            holdings=[
                {"ticker": "NVDA", "weight": 0.1, "action": "new", "conviction": 9,
                 "previous_weight": 0.0, "rationale": "x"},
            ],
            exits=[],
        )
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("SPY", "2026-04-22"): 400.0, ("SPY", "2026-07-21"): 420.0,
        }
        numbers = postmortem.compute_returns(
            portfolio, debate_data=None,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict(prices),
        )
        path = postmortem.save_postmortem_json(
            numbers, today=date(2026, 7, 21), outputs_dir=tmp_path,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["positions"][0]["ticker"] == "NVDA"
        assert data["positions"][0]["nominal_return"] == pytest.approx(0.10)
        assert data["benchmark_return"] == pytest.approx(0.05)
