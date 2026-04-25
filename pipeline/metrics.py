"""
metrics.py — funciones puras para métricas de portfolio sobre series temporales.

Sin I/O. Sin `datetime.now()`. Cada función toma una secuencia de valores
(equity diario o closes de un benchmark) y devuelve un escalar.

Convenciones:
  - Las series se asumen ya ordenadas cronológicamente y sin gaps.
    El caller (nav_tracker o dashboard) es responsable de filtrar Nones.
  - Retornos diarios se calculan como (v[i] / v[i-1]) - 1, no log-returns.
  - Vol y Sharpe se anualizan asumiendo 252 días hábiles. Si la serie tiene
    menos de 2 puntos, devolvemos 0.0 (no NaN — el dashboard prefiere ceros).
  - Sharpe asume rf=0 por default (paper trading; no tiene sentido descontar
    treasury yield). El caller puede pasar rf anualizado.

Estas fórmulas se replican exactamente en `dashboard/src/lib/metrics.ts`.
Los tests cruzados (Python vs TS, mismos inputs) están en
`pipeline/tests/test_metrics.py` y `dashboard/src/lib/metrics.test.ts`.

ADR: docs/decisions/2026-04-25-dashboard-equity-curve.md
"""

from __future__ import annotations

import math
from typing import Sequence

# Días hábiles asumidos para anualizar (NYSE: ~252).
TRADING_DAYS_PER_YEAR = 252


def daily_returns(values: Sequence[float]) -> list[float]:
    """
    Retornos diarios simples. Para una serie de N puntos devuelve N-1 retornos.

    Si algún v[i-1] es 0 o negativo, ese retorno se omite (no es financieramente
    sensato dividir por 0). Esto puede pasar al inicio si el portfolio aún no
    fue fondeado.
    """
    out: list[float] = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        curr = values[i]
        if prev is None or curr is None:
            continue
        if prev <= 0:
            continue
        out.append((curr / prev) - 1.0)
    return out


def total_return_pct(values: Sequence[float]) -> float:
    """
    Retorno total acumulado, en porcentaje (no fracción).
    Si la serie tiene <2 puntos o el primer valor es <=0, devuelve 0.0.
    """
    if len(values) < 2:
        return 0.0
    first = values[0]
    last = values[-1]
    if first is None or last is None or first <= 0:
        return 0.0
    return ((last / first) - 1.0) * 100.0


def cagr_pct(values: Sequence[float], n_days: int) -> float:
    """
    CAGR anualizado, en porcentaje, asumiendo `n_days` días calendario entre
    el primer y último valor de la serie. Para 1 año, n_days=365.

    Si n_days < 1, devuelve total_return_pct (no anualiza).
    """
    if len(values) < 2 or n_days < 1:
        return total_return_pct(values)
    first = values[0]
    last = values[-1]
    if first is None or last is None or first <= 0 or last <= 0:
        return 0.0
    years = n_days / 365.25
    if years <= 0:
        return total_return_pct(values)
    return (((last / first) ** (1.0 / years)) - 1.0) * 100.0


def vol_annualized_pct(returns: Sequence[float]) -> float:
    """
    Desviación estándar de los retornos diarios, anualizada × √252, en %.
    Si len(returns) < 2 devuelve 0.0.
    """
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)  # sample
    daily_std = math.sqrt(variance)
    return daily_std * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def max_drawdown_pct(values: Sequence[float]) -> float:
    """
    Máxima caída desde un peak previo, en porcentaje (siempre <= 0 o exactamente 0).
    Por convención devolvemos un número positivo (magnitud del drawdown), no negativo.

    Ej: si la serie sube de 100 a 120 y baja a 90, max_drawdown = 25.0 (de 120 a 90).

    Si len(values) < 2 devuelve 0.0.
    """
    if len(values) < 2:
        return 0.0
    max_so_far = values[0]
    max_dd = 0.0
    for v in values:
        if v is None:
            continue
        if v > max_so_far:
            max_so_far = v
        if max_so_far > 0:
            dd = (max_so_far - v) / max_so_far
            if dd > max_dd:
                max_dd = dd
    return max_dd * 100.0


def sharpe_ratio(
    returns: Sequence[float],
    rf_annualized: float = 0.0,
) -> float:
    """
    Sharpe anualizado: (mean_daily_return - rf_daily) / std_daily × √252.

    rf_annualized se desanualiza dividiendo por 252 (aprox suficiente para
    valores chicos de rf — lineal en lugar de geométrica).

    Si len(returns) < 2 o std == 0, devuelve 0.0.
    """
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    daily_std = math.sqrt(variance)
    if daily_std == 0.0:
        return 0.0
    rf_daily = rf_annualized / TRADING_DAYS_PER_YEAR
    excess_mean = mean - rf_daily
    return (excess_mean / daily_std) * math.sqrt(TRADING_DAYS_PER_YEAR)


def alpha_vs_benchmark_pct(
    portfolio_values: Sequence[float],
    benchmark_values: Sequence[float],
) -> float:
    """
    Alpha total acumulado: portfolio_total_return - benchmark_total_return.
    En puntos porcentuales (NO porcentaje del retorno del benchmark).

    Las dos series deben estar alineadas en fechas (el caller debe haberlas
    inner-joined antes). Si tienen distinta longitud, lanza ValueError.
    """
    if len(portfolio_values) != len(benchmark_values):
        raise ValueError(
            f"portfolio_values y benchmark_values deben alinearse: "
            f"{len(portfolio_values)} vs {len(benchmark_values)}"
        )
    return total_return_pct(portfolio_values) - total_return_pct(benchmark_values)


def rebase_to_100(values: Sequence[float]) -> list[float]:
    """
    Normaliza una serie para que el primer valor (no nulo) sea 100.
    Útil para charts comparativos: poner Indigo, SPY, QQQ todos arriba de 100.

    Valores None se preservan como None (gaps en el chart).
    """
    base = None
    for v in values:
        if v is not None and v > 0:
            base = v
            break
    if base is None:
        return [None for _ in values]  # type: ignore[misc]
    out: list[float] = []
    for v in values:
        if v is None:
            out.append(None)  # type: ignore[arg-type]
        else:
            out.append((v / base) * 100.0)
    return out


def compute_summary(
    portfolio_values: Sequence[float],
    benchmark_values: Sequence[float] | None = None,
    n_days: int = 0,
) -> dict:
    """
    Helper que devuelve un dict con todas las métricas resumidas — listo para
    embeber en el header del dashboard.

    Args:
        portfolio_values: serie de equity (asumida ordenada, sin Nones).
        benchmark_values: serie alineada del benchmark; opcional.
        n_days: días calendario entre primer y último punto (para CAGR).

    Returns:
        {
          "total_return_pct": float,
          "cagr_pct": float,
          "vol_annualized_pct": float,
          "sharpe": float,
          "max_drawdown_pct": float,
          "alpha_vs_benchmark_pct": float | None,
          "n_observations": int,
        }
    """
    rets = daily_returns(portfolio_values)
    summary = {
        "total_return_pct": total_return_pct(portfolio_values),
        "cagr_pct": cagr_pct(portfolio_values, n_days),
        "vol_annualized_pct": vol_annualized_pct(rets),
        "sharpe": sharpe_ratio(rets),
        "max_drawdown_pct": max_drawdown_pct(portfolio_values),
        "alpha_vs_benchmark_pct": None,
        "n_observations": len(portfolio_values),
    }
    if benchmark_values is not None and len(benchmark_values) == len(portfolio_values):
        summary["alpha_vs_benchmark_pct"] = alpha_vs_benchmark_pct(
            portfolio_values, benchmark_values
        )
    return summary
