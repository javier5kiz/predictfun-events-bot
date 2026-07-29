import time
from typing import Optional

from config import Config
from predict_client import PredictClient
from logger import get_logger

log = get_logger("scanner")


class MarketScanner:
    def __init__(
        self,
        config: Config,
        client: PredictClient,
    ):
        self.config = config
        self.client = client
        self._cursor: Optional[str] = None

    async def scan(self) -> dict[int, dict]:
        result: dict[int, dict] = {}
        now = time.time()

        if self._cursor:
            _, raws, _ = self.client.get_markets_with_raw(
                status="OPEN",
                market_variant="CRYPTO_UP_DOWN",
                first=50,
                after=self._cursor,
            )
            for raw in raws:
                m = self._match(raw, now)
                if m is not None:
                    result[m] = raw
            if result:
                return result

        _, raws, cursor = self.client.get_markets_with_raw(
            status="OPEN",
            market_variant="CRYPTO_UP_DOWN",
            first=50,
        )

        while cursor:
            for raw in raws:
                m = self._match(raw, now)
                if m is not None:
                    result[m] = raw
            if result:
                self._cursor = cursor
                return result
            _, raws, cursor = self.client.get_markets_with_raw(
                status="OPEN",
                market_variant="CRYPTO_UP_DOWN",
                first=50,
                after=cursor,
            )

        return result

    @staticmethod
    def _match(raw: dict, now: float) -> Optional[int]:
        if raw.get("marketVariant") != "CRYPTO_UP_DOWN":
            return None
        vd = raw.get("variantData")
        if not isinstance(vd, dict):
            return None
        provider = (vd.get("priceFeedProvider") or "").upper()
        symbol = (vd.get("priceFeedSymbol") or "").upper()
        slug = raw.get("categorySlug", "")
        if provider != "CHAINLINK":
            return None
        if "BTC" not in symbol:
            return None
        if vd.get("startPrice") is None:
            return None
        if "-5m-" not in slug:
            return None
        boost_ends = raw.get("boostEndsAt")
        if not boost_ends:
            return None
        try:
            ends_ts = _parse_iso_timestamp(boost_ends)
        except (ValueError, TypeError):
            return None
        if ends_ts <= now:
            return None
        return raw["id"]


def _parse_iso_timestamp(iso_str: str) -> float:
    from datetime import datetime
    if iso_str.endswith("Z"):
        iso_str = iso_str[:-1] + "+00:00"
    return datetime.fromisoformat(iso_str).timestamp()
