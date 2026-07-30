"""
Price oracle for the Predict.fun Fast-Expiry Bot.

ALL price data comes from predict.fun's own API — no Binance, no external exchange.

For CRYPTO_UP_DOWN markets, predict.fun stores everything we need in variantData:
  - variantData.startPrice     → the strike / reference price set at market creation
  - variantData.currentPrice   → the live price as tracked by predict.fun itself
  - variantData.priceFeedSymbol  → e.g. "BTCUSDT"
  - variantData.priceFeedProvider → "CHAINLINK" (predict.fun uses Chainlink on BNB Chain)

We read BOTH prices directly from the market data returned by predict.fun's REST API.
No external API calls for price — predict.fun is the single source of truth.
"""

import time
import logging
from typing import Optional

import requests

from logger import get_logger

log = get_logger("oracle")

# How long to cache a price fetch (seconds)
_CACHE_TTL = 2.0


class PriceFeedOracle:
    """
    Fetches target price (startPrice) and current price (currentPrice)
    exclusively from predict.fun's own market data.

    The optional predict_client reference allows the oracle to do a live
    refresh of a single market if currentPrice is stale or missing.
    """

    def __init__(self, predict_client=None):
        """
        Args:
            predict_client: optional PredictClient instance for live market refreshes.
                            If None, we rely on whatever price is in the market_raw dict.
        """
        self.client = predict_client
        # cache: market_id -> (current_price, fetched_at)
        self._cache: dict[int, tuple[float, float]] = {}

    def get_prices_from_market(self, market_raw: dict) -> tuple[Optional[float], Optional[float]]:
        """
        Extract startPrice (strike) and currentPrice from a predict.fun market dict.

        Returns:
            (start_price, current_price) — both float or None if missing.

        predict.fun sets:
          variantData.startPrice   = price at market open (the strike)
          variantData.currentPrice = live price updated by predict.fun's Chainlink feed
        """
        vd = market_raw.get("variantData") or {}
        if not isinstance(vd, dict):
            return None, None

        raw_start = vd.get("startPrice")
        raw_current = vd.get("currentPrice")

        start_price = float(raw_start) if raw_start is not None else None
        current_price = float(raw_current) if raw_current is not None else None

        return start_price, current_price

    def get_current_price(
        self,
        market_raw: dict,
        price_feed_symbol: str = "",
        provider: str = "CHAINLINK",
        pyth_feed_id: Optional[str] = None,
    ) -> Optional[float]:
        """
        Get the live current price for a market — from predict.fun's own data only.

        Priority:
          1. variantData.currentPrice in market_raw (freshest if just fetched)
          2. Live refresh via PredictClient.get_market_with_raw() if client is set
          3. None — log a warning

        The provider/symbol/pyth_feed_id args are accepted for interface compatibility
        but are NOT used to call any external API.
        """
        market_id = market_raw.get("id")

        # Check cache first
        if market_id is not None:
            cached = self._cache.get(market_id)
            if cached and time.time() - cached[1] < _CACHE_TTL:
                return cached[0]

        # Read currentPrice from the dict we already have
        _, current_price = self.get_prices_from_market(market_raw)

        if current_price is not None and current_price > 0:
            log.debug(
                f"predict.fun currentPrice for market {market_id}: "
                f"${current_price:,.2f} (provider={provider})"
            )
            if market_id is not None:
                self._cache[market_id] = (current_price, time.time())
            return current_price

        # currentPrice missing — try a live refresh if we have a client
        if self.client is not None and market_id is not None:
            try:
                _, refreshed_raw = self.client.get_market_with_raw(market_id)
                if refreshed_raw:
                    _, current_price = self.get_prices_from_market(refreshed_raw)
                    if current_price and current_price > 0:
                        log.debug(
                            f"Refreshed currentPrice for market {market_id}: "
                            f"${current_price:,.2f}"
                        )
                        self._cache[market_id] = (current_price, time.time())
                        return current_price
            except Exception as e:
                log.warning(f"Market refresh failed for {market_id}: {e}")

        log.warning(
            f"No currentPrice available for market {market_id} "
            f"(symbol={price_feed_symbol}, provider={provider})"
        )
        return None

    def invalidate(self, market_id: int):
        """Force a fresh fetch on next call for this market."""
        self._cache.pop(market_id, None)


# ── Helpers used by strategy.py ─────────────────────────────────────

FIVE_MINUTES = 300


def is_five_minute_market(market_raw: dict) -> bool:
    """
    Returns True only if this is a 5-minute expiry CRYPTO_UP_DOWN market.
    """
    vd = market_raw.get("variantData") or {}
    if not isinstance(vd, dict):
        return False

    if vd.get("duration") == FIVE_MINUTES:
        return True

    close_time = vd.get("closeTime")
    start_time = vd.get("startTime")
    if close_time and start_time:
        try:
            diff = float(close_time) - float(start_time)
            if abs(diff - FIVE_MINUTES) < 1:
                return True
        except (ValueError, TypeError):
            pass

    question = (market_raw.get("question") or "").lower()
    if "5 minute" in question or "5 min" in question:
        return True

    return False


def extract_market_price_context(market: dict) -> Optional[dict]:
    """
    Extract price context from a predict.fun CRYPTO_UP_DOWN market dict.

    Returns:
      - start_price:          float  — strike price from variantData.startPrice
      - current_price:        float | None — live price from variantData.currentPrice
      - price_feed_symbol:    str    — e.g. "BTCUSDT"
      - price_feed_provider:  str    — "CHAINLINK" (predict.fun's oracle on BNB Chain)
      - price_feed_id:        str | None — Chainlink/Pyth feed ID if present

    Returns None if this is not a CRYPTO_UP_DOWN market or startPrice is missing.
    """
    if market.get("marketVariant") != "CRYPTO_UP_DOWN":
        return None

    vd = market.get("variantData") or {}
    if not isinstance(vd, dict):
        return None

    start_price = vd.get("startPrice")
    if start_price is None:
        return None

    current_price_raw = vd.get("currentPrice")

    return {
        "start_price":         float(start_price),
        "current_price":       float(current_price_raw) if current_price_raw is not None else None,
        "price_feed_symbol":   vd.get("priceFeedSymbol", "BTCUSDT"),
        "price_feed_provider": vd.get("priceFeedProvider", "CHAINLINK"),
        "price_feed_id":       vd.get("priceFeedId"),
    }
