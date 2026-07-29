import time
import logging
from typing import Optional

import requests

from logger import get_logger

log = get_logger("oracle")


class PriceFeedOracle:
    """
    Fetches live spot prices from the provider predict.fun uses
    for a given CRYPTO_UP_DOWN market (Binance, Pyth, or Chainlink).
    """

    def __init__(
        self,
        binance_api_url: str = "https://api.binance.com",
        pyth_api_url: str = "https://hermes.pyth.network",
    ):
        self.binance_api_url = binance_api_url
        self.pyth_api_url = pyth_api_url
        self._cache: dict[str, tuple[float, float]] = {}
        self._cache_ttl = 2.0

    def get_current_price(
        self,
        price_feed_symbol: str,
        provider: str = "BINANCE",
        pyth_feed_id: Optional[str] = None,
    ) -> Optional[float]:
        cache_key = f"{provider}:{price_feed_symbol}"
        cached = self._cache.get(cache_key)
        if cached and time.time() - cached[1] < self._cache_ttl:
            return cached[0]

        price = None
        if provider == "BINANCE":
            price = self._fetch_binance(price_feed_symbol)
        elif provider == "PYTH":
            price = self._fetch_pyth(pyth_feed_id or price_feed_symbol)
        elif provider == "CHAINLINK":
            binance_sym = _pyth_to_binance_symbol(price_feed_symbol)
            price = self._fetch_binance(binance_sym)
        else:
            price = self._fetch_binance(price_feed_symbol)

        if price is not None:
            self._cache[cache_key] = (price, time.time())

        return price

    def _fetch_binance(self, symbol: str) -> Optional[float]:
        symbol = _normalise_binance_symbol(symbol)
        try:
            resp = requests.get(
                f"{self.binance_api_url}/api/v3/ticker/price",
                params={"symbol": symbol},
                timeout=5,
            )
            resp.raise_for_status()
            price = float(resp.json()["price"])
            log.debug(f"Binance {symbol}: ${price:,.4f}")
            return price
        except Exception as e:
            log.error(f"Binance price fetch failed ({symbol}): {e}")
            return None

    def _fetch_pyth(self, feed_id_or_symbol: str) -> Optional[float]:
        if len(feed_id_or_symbol) > 20:
            feed_id = feed_id_or_symbol
            if not feed_id.startswith("0x"):
                feed_id = "0x" + feed_id
        else:
            return self._fetch_binance(feed_id_or_symbol)

        try:
            resp = requests.get(
                f"{self.pyth_api_url}/v2/updates/price/latest",
                params={"ids[]": feed_id},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            parsed = data.get("parsed", [])
            if parsed:
                price_data = parsed[0].get("price", {})
                price = float(price_data.get("price", 0))
                expo = int(price_data.get("expo", 0))
                actual_price = price * (10 ** expo)
                log.debug(f"Pyth {feed_id[:20]}…: ${actual_price:,.4f}")
                return actual_price
        except Exception as e:
            log.error(f"Pyth price fetch failed ({feed_id[:20]}…): {e}")
        return None


FIVE_MINUTES = 300


def is_five_minute_market(market_raw: dict) -> bool:
    """
    Strict check: returns True only if this is a 5-minute expiry market.

    Checks multiple signals:
      1. Question text contains '5 minute' or '5 min'
      2. variantData has duration=300
      3. (closeTime - startTime) == 300
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


def _normalise_binance_symbol(symbol: str) -> str:
    symbol = symbol.upper().replace("/", "").replace("_", "").replace("-", "")
    if symbol.endswith("USD") and not symbol.endswith("USDT"):
        symbol = symbol[:-3] + "USDT"
    return symbol


def _pyth_to_binance_symbol(pyth_symbol: str) -> str:
    return _normalise_binance_symbol(pyth_symbol)


def extract_market_price_context(market: dict) -> Optional[dict]:
    """
    Extract price oracle context from a predict.fun CRYPTO_UP_DOWN market dict.

    Returns:
      - start_price: float (predict.fun's reference price)
      - price_feed_symbol: str (e.g. "BTCUSDT")
      - price_feed_provider: str ("BINANCE", "PYTH", "CHAINLINK")
      - price_feed_id: str | None
    """
    if market.get("marketVariant") != "CRYPTO_UP_DOWN":
        return None

    variant_data = market.get("variantData") or {}
    if not isinstance(variant_data, dict):
        return None

    start_price = variant_data.get("startPrice")
    if start_price is None:
        return None

    return {
        "start_price": float(start_price),
        "price_feed_symbol": variant_data.get("priceFeedSymbol", "BTCUSDT"),
        "price_feed_provider": variant_data.get("priceFeedProvider", "BINANCE"),
        "price_feed_id": variant_data.get("priceFeedId"),
    }
