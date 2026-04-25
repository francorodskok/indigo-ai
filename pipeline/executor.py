"""
executor.py — Paso 9: ejecución de órdenes en Alpaca (paper trading).

Flujo:
  1. Lee el portfolio_YYYY-MM-DD.json más reciente de pipeline/outputs/
  2. Consulta estado actual de la cuenta en Alpaca (equity, cash, posiciones)
  3. Consulta precios actuales de cada ticker target
  4. Calcula deltas (buys/sells) vs. estado actual
  5. Aplica safety checks duros (raise RuntimeError si falla)
  6. Submite órdenes MARKET day
  7. Registra cada orden en orders_YYYY-MM-DD.jsonl
  8. Verifica fills después de 15 min (salvo skip_fill_verify=True)

Regla dura: nunca llamar a un endpoint real de trading sin 'paper' en la URL.
"""

import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env", override=True)

from pipeline.config import (
    FILL_VERIFY_WAIT_SECONDS,
    MAX_ORDERS_PER_CYCLE,
    MAX_POSITION_SAFETY_PCT,
)
from pipeline.execution_report import (
    build_execution_report,
    log_summary as log_execution_summary,
    save_execution_report,
)
from pipeline.state import save_holdings, sync_from_alpaca

log = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent / "outputs"

# Tolerancia mínima para generar trade (evita rebalances micro)
REBALANCE_TOLERANCE = 0.005  # 0.5% del equity

# Singletons (lazy init)
_trading_client = None
_data_client = None


# ── Clientes Alpaca (lazy) ────────────────────────────────────────────────────

def _load_alpaca_credentials() -> tuple[str, str, str]:
    """Retorna (api_key, api_secret, base_url). Raise RuntimeError si faltan."""
    api_key = os.environ.get("ALPACA_API_KEY", "").strip()
    api_secret = os.environ.get("ALPACA_API_SECRET", "").strip()
    base_url = os.environ.get("ALPACA_BASE_URL", "").strip()

    if not api_key or not api_secret:
        raise RuntimeError(
            "ALPACA_API_KEY no encontrada. "
            "Configurar .env con credenciales de paper trading."
        )
    if not base_url:
        base_url = "https://paper-api.alpaca.markets"
    return api_key, api_secret, base_url


def get_trading_client():
    """Singleton lazy para el cliente de trading de Alpaca (paper)."""
    global _trading_client
    if _trading_client is None:
        from alpaca.trading.client import TradingClient
        api_key, api_secret, base_url = _load_alpaca_credentials()
        if "paper" not in base_url.lower():
            raise RuntimeError(
                f"ALPACA_BASE_URL debe ser paper trading. Recibido: {base_url}"
            )
        _trading_client = TradingClient(api_key, api_secret, paper=True)
    return _trading_client


def get_data_client():
    """Singleton lazy para el cliente de datos históricos de Alpaca."""
    global _data_client
    if _data_client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        api_key, api_secret, _ = _load_alpaca_credentials()
        _data_client = StockHistoricalDataClient(api_key, api_secret)
    return _data_client


# ── Lectura del portfolio objetivo ────────────────────────────────────────────

def load_latest_portfolio(outputs_dir: Path | None = None) -> dict:
    """Lee el portfolio_YYYY-MM-DD.json más reciente."""
    base = outputs_dir if outputs_dir is not None else OUTPUTS_DIR
    candidates = sorted(base.glob("portfolio_*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"No se encontró ningún portfolio_*.json en {base}"
        )
    latest = candidates[-1]
    log.info(f"Leyendo portfolio objetivo desde: {latest}")
    return json.loads(latest.read_text(encoding="utf-8"))


def _load_companion_output(stem: str, outputs_dir: Path | None = None) -> dict | None:
    """
    Lee el output más reciente de una etapa anterior para enriquecer el audit
    trail. `stem` es 'analysis' o 'debate'. Devuelve None si no hay archivo.

    No es bloqueante — si por algún motivo no existe el archivo (ej. primer
    deploy donde un ciclo se ejecuta sin las etapas previas), el audit_snapshot
    igual se construye, solo con menos campos.
    """
    base = outputs_dir if outputs_dir is not None else OUTPUTS_DIR
    candidates = sorted(base.glob(f"{stem}_*.json"))
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"No se pudo leer {latest} para enriquecer audit: {e}")
        return None


# ── Estado actual de la cuenta ────────────────────────────────────────────────

def fetch_current_state(client=None) -> dict:
    """
    Retorna {
        "equity": float,       # cash + market value de posiciones
        "cash": float,
        "positions": {ticker: {"qty": int, "market_value": float, "weight": float}}
    }
    """
    if client is None:
        client = get_trading_client()

    account = client.get_account()
    equity = float(account.equity)
    cash = float(account.cash)

    positions = {}
    for p in client.get_all_positions():
        ticker = p.symbol
        qty = int(float(p.qty))
        mv = float(p.market_value)
        positions[ticker] = {
            "qty": qty,
            "market_value": mv,
            "weight": (mv / equity) if equity > 0 else 0.0,
        }

    return {"equity": equity, "cash": cash, "positions": positions}


# ── Precios ───────────────────────────────────────────────────────────────────

def fetch_prices(tickers: list[str], data_client=None) -> dict[str, float]:
    """
    Retorna {ticker: price}. Usa latest trade de Alpaca.
    Si falla, intenta yfinance como fallback.
    """
    if not tickers:
        return {}

    prices: dict[str, float] = {}
    try:
        if data_client is None:
            data_client = get_data_client()
        from alpaca.data.requests import StockLatestTradeRequest
        req = StockLatestTradeRequest(symbol_or_symbols=list(tickers))
        latest = data_client.get_stock_latest_trade(req)
        for t in tickers:
            trade = latest.get(t)
            if trade is not None:
                prices[t] = float(trade.price)
    except Exception as e:
        log.warning(f"Alpaca data fetch falló ({e}); usando fallback yfinance")

    # Fallback yfinance para faltantes — con retries via yf_utils
    missing = [t for t in tickers if t not in prices or not prices[t]]
    if missing:
        try:
            import yfinance as yf

            from pipeline.yf_utils import fetch_with_retry, is_blacklisted
            for t in missing:
                if is_blacklisted(t):
                    log.warning(f"{t}: skip yfinance fallback — en blacklist de delistings")
                    continue
                try:
                    info = fetch_with_retry(
                        lambda t=t: yf.Ticker(t).fast_info,
                        ticker=t,
                        max_attempts=3,
                    )
                    price = getattr(info, "last_price", None) or info.get("lastPrice") if isinstance(info, dict) else getattr(info, "last_price", None)
                    if price:
                        prices[t] = float(price)
                except Exception as inner:
                    log.warning(f"yfinance fallback falló para {t} tras retries: {inner}")
        except ImportError:
            log.warning("yfinance no disponible para fallback de precios")

    return prices


# ── Cálculo de deltas ─────────────────────────────────────────────────────────

def calculate_deltas(
    target_portfolio: dict,
    current_state: dict,
    prices: dict[str, float],
) -> list[dict]:
    """
    Calcula las órdenes necesarias para ir de current_state → target_portfolio.

    Retorna lista de dicts:
        [{"ticker", "side", "qty", "estimated_cost",
          "current_weight", "target_weight", "price"}]

    Reglas:
      - Ticker en target, no en current: buy qty = target_weight*equity/price
      - Ticker en current, no en target: sell todo
      - Ticker en ambos: ajusta por la diferencia si supera la tolerancia
      - Qty redondeado a entero (sin fractional shares)
      - Si price es None o 0: skip con warning
    """
    equity = float(current_state.get("equity", 0.0))
    positions = current_state.get("positions", {})
    holdings = target_portfolio.get("holdings", [])

    target_by_ticker = {h["ticker"]: float(h.get("weight", 0.0)) for h in holdings}
    current_tickers = set(positions.keys())
    target_tickers = set(target_by_ticker.keys())

    trades: list[dict] = []

    # Sells de tickers que salen del portfolio
    for ticker in sorted(current_tickers - target_tickers):
        pos = positions[ticker]
        qty = int(pos.get("qty", 0))
        if qty <= 0:
            continue
        price = prices.get(ticker)
        if not price:
            log.warning(f"[{ticker}] sin precio; se intenta sell con qty conocido")
            price = pos.get("market_value", 0.0) / qty if qty else 0.0
        trades.append({
            "ticker": ticker,
            "side": "sell",
            "qty": qty,
            "estimated_cost": round(qty * float(price or 0), 2),
            "current_weight": pos.get("weight", 0.0),
            "target_weight": 0.0,
            "price": float(price or 0),
        })

    # Buys y rebalances de tickers en target
    for ticker in sorted(target_tickers):
        target_w = target_by_ticker[ticker]
        price = prices.get(ticker)
        if not price or price <= 0:
            log.warning(f"[{ticker}] precio inválido ({price}); skip")
            continue

        pos = positions.get(ticker, {})
        current_qty = int(pos.get("qty", 0))
        current_w = float(pos.get("weight", 0.0))

        target_value = target_w * equity
        current_value = current_qty * price  # revalúa con precio actual
        delta_value = target_value - current_value

        # Tolerancia: no operamos si el ajuste es menor que REBALANCE_TOLERANCE*equity
        if abs(delta_value) < REBALANCE_TOLERANCE * equity:
            continue

        delta_qty = int(round(delta_value / price))
        if delta_qty == 0:
            continue

        side = "buy" if delta_qty > 0 else "sell"
        qty = abs(delta_qty)
        trades.append({
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "estimated_cost": round(qty * price, 2),
            "current_weight": round(current_w, 6),
            "target_weight": round(target_w, 6),
            "price": float(price),
        })

    return trades


# ── Validaciones de seguridad ─────────────────────────────────────────────────

def validate_trades(trades: list[dict], target_portfolio: dict) -> None:
    """
    Safety checks que deben pasar antes de submitir órdenes.
    Raise RuntimeError con mensaje claro si alguno falla.
    """
    # Base URL debe ser paper
    base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    if "paper" not in base_url.lower():
        raise RuntimeError(
            f"ALPACA_BASE_URL no es de paper trading: {base_url}. "
            "Abortando por seguridad."
        )

    # Máximo de órdenes por ciclo
    if len(trades) > MAX_ORDERS_PER_CYCLE:
        raise RuntimeError(
            f"Demasiadas órdenes ({len(trades)} > {MAX_ORDERS_PER_CYCLE}). "
            "Revisar el portfolio objetivo."
        )

    # Ningún target weight puede exceder el límite de seguridad
    holdings = target_portfolio.get("holdings", [])
    seen_tickers = set()
    for h in holdings:
        ticker = h.get("ticker")
        if ticker in seen_tickers:
            raise RuntimeError(f"Ticker duplicado en portfolio objetivo: {ticker}")
        seen_tickers.add(ticker)
        w = float(h.get("weight", 0.0))
        if w > MAX_POSITION_SAFETY_PCT:
            raise RuntimeError(
                f"Target weight de {ticker} = {w:.4f} supera el límite de "
                f"seguridad ({MAX_POSITION_SAFETY_PCT}). Posible error del constructor."
            )

    # Suma de pesos no puede exceder 1
    total_w = sum(float(h.get("weight", 0.0)) for h in holdings)
    cash_w = float(target_portfolio.get("cash_weight", 0.0))
    if total_w + cash_w > 1.0 + 1e-6:
        raise RuntimeError(
            f"Suma de pesos target ({total_w:.6f}) + cash ({cash_w:.6f}) "
            f"supera 1.0. Portfolio inválido."
        )


# ── Submit de órdenes ─────────────────────────────────────────────────────────

def submit_orders(trades: list[dict], client=None) -> list[dict]:
    """
    Submite órdenes MARKET day. Retorna lista de dicts con resultado:
        [{"ticker", "side", "qty", "alpaca_order_id", "status", "estimated_cost"}]
    """
    if client is None:
        client = get_trading_client()

    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    results = []
    for t in trades:
        side = OrderSide.BUY if t["side"] == "buy" else OrderSide.SELL
        req = MarketOrderRequest(
            symbol=t["ticker"],
            qty=t["qty"],
            side=side,
            time_in_force=TimeInForce.DAY,
        )
        try:
            order = client.submit_order(req)
            results.append({
                "ticker": t["ticker"],
                "side": t["side"],
                "qty": t["qty"],
                "alpaca_order_id": str(order.id),
                "status": str(getattr(order, "status", "accepted")),
                "estimated_cost": t.get("estimated_cost", 0.0),
            })
            log.info(f"Orden enviada: {t['side']} {t['qty']} {t['ticker']} (id={order.id})")
        except Exception as e:
            log.error(f"Error enviando orden {t['side']} {t['qty']} {t['ticker']}: {e}")
            results.append({
                "ticker": t["ticker"],
                "side": t["side"],
                "qty": t["qty"],
                "alpaca_order_id": None,
                "status": f"error: {e}",
                "estimated_cost": t.get("estimated_cost", 0.0),
            })
    return results


# ── Verificación de fills ─────────────────────────────────────────────────────

def verify_fills(
    order_ids: list[str],
    wait_seconds: int = FILL_VERIFY_WAIT_SECONDS,
    client=None,
) -> dict:
    """
    Espera hasta `wait_seconds` y verifica que las órdenes estén filled o
    partially_filled. Retorna {"filled": [...], "unfilled": [...]}.
    """
    if not order_ids:
        return {"filled": [], "unfilled": []}

    if client is None:
        client = get_trading_client()

    deadline = time.time() + wait_seconds
    filled_ok = {"filled", "partially_filled"}
    remaining = set(order_ids)
    filled: list[str] = []

    while remaining and time.time() < deadline:
        for oid in list(remaining):
            try:
                o = client.get_order_by_id(oid)
                status = str(getattr(o, "status", "")).lower()
                if status.split(".")[-1] in filled_ok:
                    filled.append(oid)
                    remaining.discard(oid)
            except Exception as e:
                log.warning(f"No se pudo consultar orden {oid}: {e}")
        if remaining:
            time.sleep(min(30, max(1, int((deadline - time.time()) / 10))))

    if remaining:
        log.warning(
            f"{len(remaining)} órdenes no filled tras {wait_seconds}s: {sorted(remaining)}"
        )
    return {"filled": filled, "unfilled": sorted(remaining)}


# ── Log JSONL ─────────────────────────────────────────────────────────────────

def log_orders(orders: list[dict], path: Path, cycle: str, dry_run: bool) -> None:
    """Escribe una línea JSON por orden en `path` (append si existe)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for o in orders:
            entry = {
                "ts": ts,
                "cycle": cycle,
                "alpaca_order_id": o.get("alpaca_order_id"),
                "ticker": o["ticker"],
                "side": o["side"],
                "qty": o["qty"],
                "estimated_cost": o.get("estimated_cost", 0.0),
                "status": o.get("status", "unknown"),
                "dry_run": dry_run,
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Orquestación principal ────────────────────────────────────────────────────

def run(
    dry_run: bool = True,
    skip_fill_verify: bool = False,
    outputs_dir: Path | None = None,
    portfolio: dict | None = None,
    current_state: dict | None = None,
    prices: dict[str, float] | None = None,
) -> Path:
    """
    Ejecuta el ciclo completo de ejecución. Retorna path del orders JSONL.

    - dry_run=True: no llama a Alpaca; sólo calcula deltas y escribe log.
    - skip_fill_verify=True: submite pero no espera los 15 min.
    - outputs_dir / portfolio / current_state / prices: inyectables para tests.
    """
    base = outputs_dir if outputs_dir is not None else OUTPUTS_DIR
    base.mkdir(parents=True, exist_ok=True)

    # 1. Portfolio objetivo
    if portfolio is None:
        portfolio = load_latest_portfolio(base)

    # 2. Estado actual
    if current_state is None:
        if dry_run:
            current_state = {"equity": 100_000.0, "cash": 100_000.0, "positions": {}}
            log.info("dry_run: usando estado sintético (equity=100000, sin posiciones)")
        else:
            current_state = fetch_current_state()

    # 3. Precios
    target_tickers = [h["ticker"] for h in portfolio.get("holdings", [])]
    all_tickers = sorted(set(target_tickers) | set(current_state.get("positions", {}).keys()))

    if prices is None:
        if dry_run:
            # Precio sintético uniforme — solo importa para tener un delta calculado
            prices = {t: 100.0 for t in all_tickers}
            log.info("dry_run: usando precios sintéticos ($100 por ticker)")
        else:
            prices = fetch_prices(all_tickers)

    # 4. Deltas
    trades = calculate_deltas(portfolio, current_state, prices)
    log.info(f"{len(trades)} trades calculados")

    # 5. Validaciones
    validate_trades(trades, portfolio)

    # 6. Submit o dry-run
    today = date.today().isoformat()
    orders_path = base / f"orders_{today}.jsonl"

    if dry_run:
        print(f"[DRY RUN] {len(trades)} trades que se ejecutarían:")
        for t in trades:
            print(
                f"  {t['side'].upper():4s} {t['qty']:6d} {t['ticker']:6s} "
                f"@ ${t['price']:.2f} = ${t['estimated_cost']:.2f} "
                f"(current={t['current_weight']:.4f} → target={t['target_weight']:.4f})"
            )
        dry_orders = [
            {
                "ticker": t["ticker"],
                "side": t["side"],
                "qty": t["qty"],
                "alpaca_order_id": None,
                "status": "dry_run",
                "estimated_cost": t["estimated_cost"],
            }
            for t in trades
        ]
        log_orders(dry_orders, orders_path, cycle=today, dry_run=True)
        return orders_path

    # 7. Real submit
    submitted = submit_orders(trades)
    log_orders(submitted, orders_path, cycle=today, dry_run=False)

    # 8. Fill verify
    if not skip_fill_verify:
        order_ids = [o["alpaca_order_id"] for o in submitted if o.get("alpaca_order_id")]
        result = verify_fills(order_ids)
        log.info(
            f"Fills: {len(result['filled'])} filled, {len(result['unfilled'])} unfilled"
        )

    # 8.5. Validación post-ejecución: target vs realidad.
    # Snapshot del estado real después de los fills, comparado contra el target
    # del constructor. Detecta drift por slippage, fills parciales, redondeo,
    # o errores transitorios. No bloqueante — solo loggea y guarda el reporte.
    try:
        post_state = fetch_current_state()
        report = build_execution_report(
            target_portfolio=portfolio,
            actual_state=post_state,
            cycle_id=today,
            submitted_orders=submitted,
        )
        save_execution_report(report, base, cycle_id=today)
        log_execution_summary(report)
    except Exception as e:
        log.error(f"Error generando execution report: {e}")

    # 9. Sincronizar memoria entre ciclos (Paso D).
    # Fuente de verdad = Alpaca (qué posiciones quedaron, con qué avg_cost);
    # el portfolio JSON aporta metadata (conviction, price_target, rationale).
    # Adicionalmente cargamos los outputs de analyst y debate del mismo ciclo
    # para construir el audit trail completo (¿por qué compramos X?).
    try:
        client = get_trading_client()
        positions = client.get_all_positions()
        equity = float(client.get_account().equity)
        analysis_data = _load_companion_output("analysis", base)
        debate_data = _load_companion_output("debate", base)
        updated_state = sync_from_alpaca(
            alpaca_positions=positions,
            account_equity=equity,
            portfolio_snapshot=portfolio,
            analysis_data=analysis_data,
            debate_data=debate_data,
        )
        save_holdings(updated_state)
        log.info(
            f"Memoria entre ciclos sincronizada: "
            f"{len(updated_state['holdings'])} posiciones, "
            f"{len(updated_state['history'])} eventos en historial. "
            f"Audit trail: analyst={'ok' if analysis_data else 'missing'}, "
            f"debate={'ok' if debate_data else 'missing'}."
        )
    except Exception as e:
        # No abortar el ciclo si la memoria falla — el executor ya hizo su trabajo.
        log.error(f"Error sincronizando memoria entre ciclos: {e}")

    return orders_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    parser = argparse.ArgumentParser(description="Indigo AI — executor (Paso 9)")
    parser.add_argument("--dry-run", action="store_true", help="no llama a Alpaca")
    parser.add_argument(
        "--skip-fill-verify",
        action="store_true",
        help="submite órdenes pero no espera los 15 min de verificación",
    )
    args = parser.parse_args()

    path = run(dry_run=args.dry_run, skip_fill_verify=args.skip_fill_verify)
    print(f"Orders log: {path}")


if __name__ == "__main__":
    main()
