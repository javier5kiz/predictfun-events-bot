"""
Strategy for the Predict.fun Fast-Expiry Bot.

  - TARGET (strike) price  → variantData.startPrice  (predict.fun)
  - CURRENT (live) price   → Binance spot via PriceFeedOracle

predict.fun does not populate variantData.currentPrice via REST in real-time,
so Binance spot is used as the live price source for comparison.
"""

import time
from typing import Optional

from config import Config
from models import TradeSignal
from price_oracle import PriceFeedOracle, extract_market_price_context
from logger import get_logger

log = get_logger("strategy")


class FastExpiryStrategy:
    def __init__(self, config: Config, oracle: PriceFeedOracle):
        self.config = config
        self.oracle = oracle

    def evaluate_all(
        self,
        markets_raw: dict[int, dict],
    ) -> Optional[TradeSignal]:
        """Evaluate all markets, return the first valid signal (BTC only)."""
        for market_id, raw in markets_raw.items():
            vd = raw.get("variantData") or {}
            symbol = (vd.get("priceFeedSymbol") or "").upper()
            if "BTC" not in symbol:
                continue

            signal = self._evaluate_single(raw)
            if signal is not None:
                return signal

        return None

    def _evaluate_single(self, market_raw: dict) -> Optional[TradeSignal]:
        ctx = extract_market_price_context(market_raw)
        if ctx is None:
            return None

        start_price = ctx["start_price"]   # strike — from variantData.startPrice (predict.fun)
        symbol      = ctx["price_feed_symbol"]
        provider    = ctx["price_feed_provider"]

        expiry = _get_expiry(market_raw)
        if expiry is None:
            return None

        remaining = expiry - time.time()
        if remaining > self.config.execution_window_seconds:
            return None
        if remaining <= 0:
            return None

        # Live current price from Binance spot
        current_price = self.oracle.get_current_price(
            market_raw=market_raw,
            price_feed_symbol=symbol,
            provider=provider,
        )
        if current_price is None:
            log.warning(f"Market {market_raw.get('id')}: Binance price unavailable — skip")
            return None

        diff     = current_price - start_price
        diff_abs = abs(diff)

        if diff_abs <= self.config.btc_threshold_usd:
            log.info(
                f"|Δ|=${diff_abs:.2f} ≤ ${self.config.btc_threshold_usd} threshold — "
                f"skip (too close to strike)"
            )
            return None

        outcome   = "YES" if diff > 0 else "NO"
        direction = "UP"  if diff > 0 else "DOWN"

        log.warning(
            f"[LAST {remaining:.1f}s] "
            f"BTC spot=${current_price:.1f} (Binance) | "
            f"Strike=${start_price:.1f} (predict.fun) | "
            f"Diff={diff:+.2f}"
        )
        log.warning(
            f">>> SIGNAL {direction}: Δ={diff:+.2f} — "
            f"executing {self.config.account_allocation:.0%} BUY {outcome.lower()} <<<"
        )

        return TradeSignal(
            market_id=market_raw["id"],
            outcome=outcome,
            side="BUY",
            suggested_price=1.0,
            fair_value=diff_abs,
            market_price=start_price,
            edge=diff_abs / start_price,
            confidence=1.0,
            size_alloc=self.config.account_allocation,
        )


# ── Expiry helpers ───────────────────────────────────────────────────

def _get_expiry(market_raw: dict) -> Optional[float]:
    boost_ends = market_raw.get("boostEndsAt")
    if boost_ends:
        try:
            return _parse_iso_timestamp(boost_ends)
        except (ValueError, TypeError):
            pass

    expires_at = (
        market_raw.get("expiresAt")
        or market_raw.get("closeAt")
        or market_raw.get("endDate")
        or (market_raw.get("variantData") or {}).get("closeTime")
    )
    if not expires_at:
        return None
    try:
        expiry = float(expires_at)
        if expiry > 1e12:
            expiry /= 1000
        return expiry
    except (ValueError, TypeError):
        return None


def _parse_iso_timestamp(iso_str: str) -> float:
    from datetime import datetime
    s = iso_str
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).timestamp()
