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

        start_price = ctx["start_price"]

        expiry = _get_expiry(market_raw)
        if expiry is None:
            return None

        remaining = expiry - time.time()
        if remaining > self.config.execution_window_seconds:
            return None
        if remaining <= 0:
            return None

        current_price = self.oracle.get_current_price(
            price_feed_symbol=ctx["price_feed_symbol"],
            provider=ctx["price_feed_provider"],
            pyth_feed_id=ctx["price_feed_id"],
        )
        if current_price is None:
            return None

        diff = current_price - start_price
        diff_abs = abs(diff)

        if diff_abs <= self.config.btc_threshold_usd:
            log.info(
                f"|Δ|=${diff_abs:.2f} ≤ ${self.config.btc_threshold_usd} threshold — skip (too close)"
            )
            return None

        if diff > 0:
            outcome = "YES"
            direction = "UP"
        else:
            outcome = "NO"
            direction = "DOWN"

        log.warning(
            f"[LAST {remaining:.1f}s] BTC=${current_price:.1f} | "
            f"Strike=${start_price:.1f} | "
            f"Diff={diff:+.2f}"
        )
        log.warning(
            f">>> SIGNAL {direction}: {diff:+.2f} — executing "
            f"{self.config.account_allocation:.0%} BUY outcome={outcome.lower()} <<<"
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
