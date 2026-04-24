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


@pytest.fixture(autouse=True)
def _isolate_lessons_dir(tmp_path, monkeypatch):
    """
    Aísla philosophy/lessons/ para que los tests no escriban en el corpus real.
    """
    lessons = tmp_path / "lessons"
    lessons.mkdir(exist_ok=True)
    monkeypatch.setattr(postmortem, "LESSONS_DIR", lessons)
    yield lessons


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


# ── Helpers para commit 2 ────────────────────────────────────────────────────


def _valid_lesson_md(today_str: str = "2026-07-21", cycle_str: str = "2026-04-22") -> str:
    """Genera un MD válido con las 6 secciones obligatorias."""
    return f"""# Lección {today_str} (ciclo {cycle_str})

## Resumen cuantitativo

Portfolio +8%, SPY +5%, alpha +3%.

## Aciertos

- NVDA (new, conv=9): return +18%, alpha +13%.

## Errores

- Ninguno con alpha significativo.

## Patrones

- Concentración en AI funcionó.

## Ajustes propuestos

- Mantener sesgo AI mientras capex sector > 20% yoy.

## Vetos validados

- Sin vetos en este ciclo.
"""


def _make_numbers(positions=None, exits=None, alpha=0.03) -> postmortem.PostmortemNumbers:
    """PostmortemNumbers mínimo para tests que no pasan por compute_returns."""
    return postmortem.PostmortemNumbers(
        generated_at="2026-07-21T10:00:00+00:00",
        portfolio_date="2026-04-22",
        days_elapsed=90,
        benchmark="SPY",
        benchmark_return=0.05,
        portfolio_return_weighted=0.08,
        alpha_weighted=alpha,
        positions=positions or [],
        exits=exits or [],
        data_quality={"tickers_missing_price": [], "partial": False},
    )


# ── TestRenderRecentLessons ──────────────────────────────────────────────────


class TestRenderRecentLessons:
    def test_empty_dir_returns_empty_string(self, _isolate_lessons_dir):
        """Sin lecciones → string vacío (caller puede concatenar sin checkear)."""
        result = postmortem.render_recent_lessons()
        assert result == ""

    def test_dir_does_not_exist_returns_empty_string(self, tmp_path, monkeypatch):
        """Dir inexistente → string vacío (no raise)."""
        monkeypatch.setattr(postmortem, "LESSONS_DIR", tmp_path / "nope")
        assert postmortem.render_recent_lessons() == ""

    def test_returns_lessons_most_recent_first(self, _isolate_lessons_dir):
        """Orden descendente por nombre (ISO-sortable)."""
        (_isolate_lessons_dir / "lesson_2026-01-23.md").write_text("OLD LESSON", encoding="utf-8")
        (_isolate_lessons_dir / "lesson_2026-07-21.md").write_text("RECENT LESSON", encoding="utf-8")
        (_isolate_lessons_dir / "lesson_2026-04-22.md").write_text("MID LESSON", encoding="utf-8")
        result = postmortem.render_recent_lessons()
        # RECENT aparece antes que MID, que aparece antes que OLD
        assert result.index("RECENT LESSON") < result.index("MID LESSON")
        assert result.index("MID LESSON") < result.index("OLD LESSON")

    def test_respects_top_n(self, _isolate_lessons_dir):
        """El argumento n limita cuántas lecciones se incluyen."""
        for d in ["2026-01-23", "2026-04-22", "2026-07-21"]:
            (_isolate_lessons_dir / f"lesson_{d}.md").write_text(f"LESSON_{d}", encoding="utf-8")
        result = postmortem.render_recent_lessons(n=2)
        # Las 2 más recientes
        assert "LESSON_2026-07-21" in result
        assert "LESSON_2026-04-22" in result
        # La más vieja NO
        assert "LESSON_2026-01-23" not in result

    def test_ignores_unrelated_files(self, _isolate_lessons_dir):
        """Archivos que no matchean lesson_*.md son ignorados."""
        (_isolate_lessons_dir / "README.md").write_text("readme", encoding="utf-8")
        (_isolate_lessons_dir / "notes.txt").write_text("notes", encoding="utf-8")
        (_isolate_lessons_dir / "lesson_2026-07-21.md").write_text("VALID LESSON", encoding="utf-8")
        result = postmortem.render_recent_lessons()
        assert "VALID LESSON" in result
        assert "readme" not in result
        assert "notes" not in result

    def test_includes_separator_header(self, _isolate_lessons_dir):
        """El bloque rendered tiene un header explícito (para el modelo)."""
        (_isolate_lessons_dir / "lesson_2026-07-21.md").write_text("L1", encoding="utf-8")
        result = postmortem.render_recent_lessons()
        assert "LECCIONES RECIENTES" in result


# ── TestAugmentSuffix ────────────────────────────────────────────────────────


class TestAugmentSuffix:
    def test_empty_lessons_returns_base_unchanged(self, _isolate_lessons_dir):
        """Sin lecciones → devuelve el suffix base intacto (no adds whitespace)."""
        result = postmortem.augment_suffix("BASE SUFFIX")
        assert result == "BASE SUFFIX"

    def test_with_lessons_appends_after_base(self, _isolate_lessons_dir):
        """Lecciones presentes → se concatenan DESPUÉS del suffix base."""
        (_isolate_lessons_dir / "lesson_2026-07-21.md").write_text(
            "LESSON CONTENT", encoding="utf-8"
        )
        result = postmortem.augment_suffix("BASE SUFFIX")
        # La base va primero (crítico para preservar cache)
        assert result.startswith("BASE SUFFIX")
        # Las lecciones van después
        assert "LESSON CONTENT" in result
        assert result.index("BASE SUFFIX") < result.index("LESSON CONTENT")

    def test_respects_n_parameter(self, _isolate_lessons_dir):
        """El arg n se propaga a render_recent_lessons."""
        for d in ["2026-01-23", "2026-04-22", "2026-07-21"]:
            (_isolate_lessons_dir / f"lesson_{d}.md").write_text(
                f"CONTENT_{d}", encoding="utf-8"
            )
        result = postmortem.augment_suffix("BASE", n=1)
        # Solo la más reciente
        assert "CONTENT_2026-07-21" in result
        assert "CONTENT_2026-04-22" not in result


# ── TestBuildPrompt ──────────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_includes_today_and_portfolio_dates(self):
        numbers = _make_numbers()
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={"cycle_id": "2026-04-22"},
            today=date(2026, 7, 21),
        )
        assert "2026-07-21" in prompt
        assert "2026-04-22" in prompt
        assert "hace 90 días" in prompt

    def test_includes_aggregate_numbers(self):
        numbers = _make_numbers()
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={"cycle_id": "2026-04-22"},
            today=date(2026, 7, 21),
        )
        # Portfolio +8%, SPY +5%, alpha +3%
        assert "+8" in prompt
        assert "+5" in prompt
        assert "+3" in prompt

    def test_includes_positions_table(self):
        pos = postmortem.PositionReturn(
            ticker="NVDA", weight=0.10, action="new", conviction=9,
            entry_price=100.0, price_today=118.0,
            nominal_return=0.18, benchmark_return=0.05,
            alpha=0.13, contribution=0.018,
        )
        numbers = _make_numbers(positions=[pos])
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={"cycle_id": "2026-04-22"},
            today=date(2026, 7, 21),
        )
        assert "NVDA" in prompt
        # Tabla markdown
        assert "| Ticker " in prompt
        assert "+18" in prompt

    def test_exits_section_when_empty_shows_placeholder(self):
        numbers = _make_numbers(exits=[])
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={"cycle_id": "2026-04-22"},
            today=date(2026, 7, 21),
        )
        assert "Sin exits" in prompt

    def test_exits_table_when_present(self):
        ex = postmortem.ExitReturn(
            ticker="META", kind="veto", reason="Valuación estirada",
            previous_weight=0.07, entry_price=500.0, price_today=550.0,
            counterfactual_return=0.10, benchmark_return=0.05,
            counterfactual_alpha=0.05,
        )
        numbers = _make_numbers(exits=[ex])
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={"cycle_id": "2026-04-22"},
            today=date(2026, 7, 21),
        )
        assert "META" in prompt
        assert "veto" in prompt
        assert "Valuación estirada" in prompt

    def test_includes_decision_summary_and_macro(self):
        """El contexto original del ciclo se inyecta si está presente."""
        numbers = _make_numbers()
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={
                "cycle_id": "2026-04-22",
                "decision_summary": "Régimen prudente, sesgo calidad.",
                "macro_concerns": ["CAPE 33", "VIX promediando 22"],
            },
            today=date(2026, 7, 21),
        )
        assert "Régimen prudente" in prompt
        assert "CAPE 33" in prompt
        assert "VIX" in prompt

    def test_includes_previous_lessons_when_provided(self):
        numbers = _make_numbers()
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={"cycle_id": "2026-04-22"},
            today=date(2026, 7, 21),
            previous_lessons_block="LECCIONES ANTERIORES DUMMY",
        )
        assert "LECCIONES ANTERIORES DUMMY" in prompt
        assert "LECCIONES PREVIAS" in prompt

    def test_handles_none_values_gracefully(self):
        """Si benchmark_return / alpha son None, el prompt los marca como N/D."""
        numbers = postmortem.PostmortemNumbers(
            generated_at="2026-07-21T10:00:00+00:00",
            portfolio_date="2026-04-22",
            days_elapsed=90,
            benchmark="SPY",
            benchmark_return=None,
            portfolio_return_weighted=None,
            alpha_weighted=None,
            data_quality={"tickers_missing_price": ["FOO"], "partial": True},
        )
        prompt = postmortem.build_prompt(
            numbers=numbers,
            portfolio={"cycle_id": "2026-04-22"},
            today=date(2026, 7, 21),
        )
        assert "N/D" in prompt
        # Data quality missing tickers se reporta
        assert "FOO" in prompt


# ── TestParseLessonMd ────────────────────────────────────────────────────────


class TestParseLessonMd:
    def test_valid_lesson_returns_dict_of_sections(self):
        md = _valid_lesson_md()
        sections = postmortem.parse_lesson_md(md)
        for expected in postmortem.REQUIRED_SECTIONS:
            assert expected in sections

    def test_section_bodies_are_extracted(self):
        md = _valid_lesson_md()
        sections = postmortem.parse_lesson_md(md)
        assert "Portfolio" in sections["Resumen cuantitativo"]
        assert "NVDA" in sections["Aciertos"]
        assert "Sin vetos" in sections["Vetos validados"]

    def test_missing_section_raises(self):
        """Falta 'Vetos validados' → LessonSchemaError."""
        md = """# Lección 2026-07-21 (ciclo 2026-04-22)

## Resumen cuantitativo

x

## Aciertos

- uno

## Errores

- ninguno

## Patrones

- nada

## Ajustes propuestos

- nada
"""
        with pytest.raises(postmortem.LessonSchemaError) as exc:
            postmortem.parse_lesson_md(md)
        assert "Vetos validados" in str(exc.value)

    def test_no_headers_raises(self):
        """MD sin ningún ## header → LessonSchemaError."""
        md = "# Solo hay H1\n\ntexto plano sin secciones."
        with pytest.raises(postmortem.LessonSchemaError):
            postmortem.parse_lesson_md(md)

    def test_extra_sections_allowed(self):
        """Si el modelo agregó secciones extra, se aceptan (no hay allowlist estricto)."""
        md = _valid_lesson_md() + "\n## Notas adicionales\n\n- extra"
        sections = postmortem.parse_lesson_md(md)
        # Todas las obligatorias están
        for expected in postmortem.REQUIRED_SECTIONS:
            assert expected in sections
        # Y la extra también
        assert "Notas adicionales" in sections


# ── TestSaveLesson / TestSaveFailedLesson ────────────────────────────────────


class TestSaveLesson:
    def test_writes_to_lessons_dir_with_iso_name(self, _isolate_lessons_dir):
        md = _valid_lesson_md()
        path = postmortem.save_lesson(md, today=date(2026, 7, 21))
        assert path.exists()
        assert path.name == "lesson_2026-07-21.md"
        assert path.parent == _isolate_lessons_dir

    def test_content_is_written_verbatim(self, _isolate_lessons_dir):
        md = _valid_lesson_md()
        path = postmortem.save_lesson(md, today=date(2026, 7, 21))
        assert path.read_text(encoding="utf-8") == md


class TestSaveFailedLesson:
    def test_writes_to_failed_subdir(self, _isolate_lessons_dir):
        path = postmortem.save_failed_lesson(
            "malformed", today=date(2026, 7, 21), reason="missing Vetos",
        )
        assert path.exists()
        assert path.parent.name == "failed"
        assert path.name == "lesson_2026-07-21.md"

    def test_includes_reason_as_comment_header(self, _isolate_lessons_dir):
        path = postmortem.save_failed_lesson(
            "malformed", today=date(2026, 7, 21), reason="missing Vetos",
        )
        content = path.read_text(encoding="utf-8")
        assert "missing Vetos" in content
        assert "<!--" in content  # HTML comment
        assert "malformed" in content


# ── TestRun (orquestación completa) ──────────────────────────────────────────


def _make_portfolio_file(outputs_dir: Path, cycle_id: str) -> Path:
    """Escribe un portfolio_*.json mínimo para tests de run()."""
    portfolio = _make_portfolio_json(cycle_id=cycle_id)
    path = outputs_dir / f"portfolio_{cycle_id}.json"
    path.write_text(json.dumps(portfolio), encoding="utf-8")
    return path


class TestRun:
    def test_skips_when_no_reference_portfolio(self, _isolate_outputs_dir, _isolate_state_dir):
        """Primer post-mortem antes de tener historia → skipped, no raise."""
        result = postmortem.run(
            dry_run=True,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict({}),
        )
        assert result.status == "skipped"
        assert result.portfolio_date is None
        assert result.lesson_path is None
        # State persistido
        state_file = _isolate_state_dir / "last_postmortem.json"
        assert state_file.exists()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["skipped"] is True

    def test_dry_run_success_happy_path(
        self, _isolate_outputs_dir, _isolate_state_dir, _isolate_lessons_dir
    ):
        """dry_run con portfolio de referencia → lesson stub válido + state success."""
        _make_portfolio_file(_isolate_outputs_dir, cycle_id="2026-04-22")
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("MSFT", "2026-04-22"): 400.0, ("MSFT", "2026-07-21"): 420.0,
            ("META", "2026-04-22"): 500.0, ("META", "2026-07-21"): 550.0,
            ("SPY", "2026-04-22"): 450.0, ("SPY", "2026-07-21"): 470.0,
        }
        result = postmortem.run(
            dry_run=True,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict(prices),
        )
        assert result.status == "success"
        assert result.portfolio_date == "2026-04-22"
        assert result.n_positions == 2
        # La lección se escribió y tiene las 6 secciones
        lesson_path = Path(result.lesson_path)
        assert lesson_path.exists()
        md = lesson_path.read_text(encoding="utf-8")
        postmortem.parse_lesson_md(md)  # no raise = válido
        # El JSON numérico también
        assert Path(result.postmortem_json_path).exists()
        # State persistido
        state = json.loads((_isolate_state_dir / "last_postmortem.json").read_text(encoding="utf-8"))
        assert state["status"] == "success"
        assert state["skipped"] is False

    def test_run_persists_json_even_if_lesson_invalid(
        self, _isolate_outputs_dir, _isolate_state_dir, _isolate_lessons_dir
    ):
        """
        Si el LLM devuelve un MD malformado, el JSON numérico debe quedar
        persistido igual y el MD se guarda en failed/.
        """
        _make_portfolio_file(_isolate_outputs_dir, cycle_id="2026-04-22")
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("MSFT", "2026-04-22"): 400.0, ("MSFT", "2026-07-21"): 420.0,
            ("META", "2026-04-22"): 500.0, ("META", "2026-07-21"): 550.0,
            ("SPY", "2026-04-22"): 450.0, ("SPY", "2026-07-21"): 470.0,
        }

        def bad_agent(**kwargs):
            return {"content": "# Malformed\n\nsin secciones ##"}

        result = postmortem.run(
            dry_run=False,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict(prices),
            call_agent_fn=bad_agent,
        )
        assert result.status == "lesson_invalid"
        # JSON numérico sí existe
        assert Path(result.postmortem_json_path).exists()
        # El MD fallido está en failed/
        assert result.lesson_path is not None
        failed_path = Path(result.lesson_path)
        assert failed_path.parent.name == "failed"

    def test_run_handles_api_error_gracefully(
        self, _isolate_outputs_dir, _isolate_state_dir, _isolate_lessons_dir
    ):
        """Si el LLM tira, el JSON numérico ya quedó persistido y el state dice api_error."""
        _make_portfolio_file(_isolate_outputs_dir, cycle_id="2026-04-22")
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("MSFT", "2026-04-22"): 400.0, ("MSFT", "2026-07-21"): 420.0,
            ("META", "2026-04-22"): 500.0, ("META", "2026-07-21"): 550.0,
            ("SPY", "2026-04-22"): 450.0, ("SPY", "2026-07-21"): 470.0,
        }

        def broken_agent(**kwargs):
            raise RuntimeError("anthropic 529 overloaded")

        result = postmortem.run(
            dry_run=False,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict(prices),
            call_agent_fn=broken_agent,
        )
        assert result.status == "api_error"
        assert "529" in result.notes or "overloaded" in result.notes
        # JSON existe, lesson no
        assert Path(result.postmortem_json_path).exists()
        assert result.lesson_path is None
        # State refleja el error, NO skipped (para no bloquear el retry normal)
        state = json.loads((_isolate_state_dir / "last_postmortem.json").read_text(encoding="utf-8"))
        assert state["status"] == "api_error"

    def test_run_success_with_real_agent_stub(
        self, _isolate_outputs_dir, _isolate_state_dir, _isolate_lessons_dir
    ):
        """call_agent_fn devuelve un MD válido → success."""
        _make_portfolio_file(_isolate_outputs_dir, cycle_id="2026-04-22")
        prices = {
            ("NVDA", "2026-04-22"): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("MSFT", "2026-04-22"): 400.0, ("MSFT", "2026-07-21"): 420.0,
            ("META", "2026-04-22"): 500.0, ("META", "2026-07-21"): 550.0,
            ("SPY", "2026-04-22"): 450.0, ("SPY", "2026-07-21"): 470.0,
        }

        captured = {}

        def good_agent(**kwargs):
            captured.update(kwargs)
            return {"content": _valid_lesson_md()}

        result = postmortem.run(
            dry_run=False,
            today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict(prices),
            call_agent_fn=good_agent,
        )
        assert result.status == "success"
        # Verificamos que se le pasaron los argumentos correctos al agent
        assert captured["role"] == "postmortem"
        assert "retrospectivo" in captured["system_suffix"]
        assert captured["dry_run"] is False
        # Crítico: postmortem NO debe reinyectar lecciones en system_suffix —
        # las lecciones ya van dentro del user_input via build_prompt.
        assert captured["inject_lessons"] is False

    def test_dry_run_lesson_is_valid_schema(self):
        """El stub de dry_run pasa parse_lesson_md sin error."""
        numbers = _make_numbers()
        md = postmortem._build_dry_run_lesson(numbers, date(2026, 7, 21))
        # no raise
        sections = postmortem.parse_lesson_md(md)
        for expected in postmortem.REQUIRED_SECTIONS:
            assert expected in sections

    def test_run_uses_custom_lookback_days(
        self, _isolate_outputs_dir, _isolate_state_dir, _isolate_lessons_dir
    ):
        """Un lookback_days corto permite correr post-mortem en dev sin esperar 90d."""
        # Portfolio a 30 días — fuera del lookback default (90d)
        cycle = date(2026, 7, 21) - timedelta(days=30)
        _make_portfolio_file(_isolate_outputs_dir, cycle_id=cycle.isoformat())
        prices = {
            ("NVDA", cycle.isoformat()): 100.0, ("NVDA", "2026-07-21"): 110.0,
            ("MSFT", cycle.isoformat()): 400.0, ("MSFT", "2026-07-21"): 420.0,
            ("META", cycle.isoformat()): 500.0, ("META", "2026-07-21"): 550.0,
            ("SPY", cycle.isoformat()): 450.0, ("SPY", "2026-07-21"): 470.0,
        }
        # Con default (90d) → skip
        default_result = postmortem.run(
            dry_run=True, today=date(2026, 7, 21),
            price_fetcher=_price_fetcher_from_dict(prices),
        )
        assert default_result.status == "skipped"
        # Con lookback=30 → success
        custom_result = postmortem.run(
            dry_run=True, today=date(2026, 7, 21),
            lookback_days=30,
            price_fetcher=_price_fetcher_from_dict(prices),
        )
        assert custom_result.status == "success"
