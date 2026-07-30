"""
Price oracle for the Predict.fun Fast-Expiry Bot.

  - TARGET (strike) price  → variantData.startPrice from predict.fun  ✓
  - CURRENT (live) price   → Binance spot API (BTCUSDT ticker)

Server must be outside US (e.g. Amsterdam) — Binance blocks US-based IPs (HTTP 451).
"""

import time
from typing import Optional

import requests

from logger import get_logger

log = get_logger("oracle")

_CACHE_TTL       = 2.0
_BINANCE_API_URL = "https://api.binance.com"
FIVE_MINUTES     = 300


class PriceFeedOracle:
    """
    Current price  → Binance spot (BTCUSDT or whichever symbol the market uses)
    Target price   → variantData.startPrice (read by strategy directly from predict.fun)
    """

    def __init__(self, predict_client=None):
        self.client = predict_client  # interface compat, not used for price
        # cache: symbol -> (price, fetched_at)
        self._cache: dict[str, tuple[float, float]] = {}

    def get_current_price(
        self,
        market_raw: dict = None,
        price_feed_symbol: str = "BTCUSDT",
        provider: str = "CHAINLINK",
        pyth_feed_id: Optional[str] = None,
    ) -> Optional[float]:
        """Fetch the live spot price from Binance for the given symbol."""
        if market_raw is not None:
            vd = market_raw.get("variantData") or {}
            raw_sym = vd.get("priceFeedSymbol") or price_feed_symbol
        else:
            raw_sym = price_feed_symbol

        symbol = _normalise_binance_symbol(raw_sym)

        cached = self._cache.get(symbol)
        if cached and time.time() - cached[1] < _CACHE_TTL:
            return cached[0]

        price = self._fetch_binance(symbol)
        if price is not None:
            self._cache[symbol] = (price, time.time())
        return price

    def _fetch_binance(self, symbol: str) -> Optional[float]:
        try:
            resp = requests.get(
                f"{_BINANCE_API_URL}/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=5,
            )
            resp.raise_for_status()
            price = float(resp.json()["price"])
            log.debug(f"Binance spot {symbol}: ${price:,.2f}")
            return price
        except Exception as e:
            log.error(f"Binance price fetch failed ({symbol}): {e}")
            return None

    def invalidate(self, symbol: str):
        self._cache.pop(_normalise_binance_symbol(symbol), None)


# ── Helpers ──────────────────────────────────────────────────────────

def _normalise_binance_symbol(symbol: str) -> str:
    s = symbol.upper().replace("/", "").replace("_", "").replace("-", "")
    if s.endswith("USD") and not s.endswith("USDT"):
        s = s[:-3] + "USDT"
    return s


def is_five_minute_market(market_raw: dict) -> bool:
    vd = market_raw.get("variantData") or {}
    if not isinstance(vd, dict):
        return False
    if vd.get("duration") == FIVE_MINUTES:
        return True
    close_time = vd.get("closeTime")
    start_time = vd.get("startTime")
    if close_time and start_time:
        try:
            if abs(float(close_time) - float(start_time) - FIVE_MINUTES) < 1:
                return True
        except (ValueError, TypeError):
            pass
    question = (market_raw.get("question") or "").lower()
    return "5 minute" in question or "5 min" in question


def extract_market_price_context(market: dict) -> Optional[dict]:
    """
    Extract price context from a predict.fun CRYPTO_UP_DOWN market dict.

    start_price  → variantData.startPrice (predict.fun — the target/strike)
    current_price comes from Binance spot via get_current_price().
    """
    if market.get("marketVariant") != "CRYPTO_UP_DOWN":
        return None

    vd = market.get("variantData") or {}
    if not isinstance(vd, dict):
        return None

    start_price = vd.get("startPrice")
    if start_price is None:
        return None

    return {
        "start_price":         float(start_price),
        "price_feed_symbol":   vd.get("priceFeedSymbol", "BTCUSDT"),
        "price_feed_provider": vd.get("priceFeedProvider", "CHAINLINK"),
        "price_feed_id":       vd.get("priceFeedId"),
    }
