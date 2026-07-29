"""
Monitor: record orderbook state in the last 1.5s of every 5-min contract
to check whether sellers exist on the dominant side (needed for live MARKET FOK).
"""
import asyncio
import json
import os
import signal
import time
from datetime import datetime, timezone

from config import Config
from auth import PredictAuth
from predict_client import PredictClient
from market_scanner import MarketScanner
from price_oracle import PriceFeedOracle
from strategy import FastExpiryStrategy, _get_expiry
from logger import get_logger, BotLogger

log = get_logger("monitor")

RECORD_FILE = "orderbook_records.jsonl"


class LiquidityMonitor:
    def __init__(self, config: Config):
        self.config = config
        self.running = False

        self.auth = PredictAuth(config)
        self.client = PredictClient(config, self.auth)
        self.oracle = PriceFeedOracle()
        self.scanner = MarketScanner(config, self.client)
        self.strategy = FastExpiryStrategy(config, self.oracle)

        self._current_market_id: int | None = None
        self._current_strike: float | None = None
        self._current_expiry: float | None = None
        self._seen_contracts: set[int] = set()
        self._contract_count = 0
        self._signal_count = 0

    async def run(self, duration_minutes: int = 30):
        end_time = time.time() + duration_minutes * 60
        self.running = True

        log.info("=" * 60)
        log.info("  LIQUIDITY MONITOR")
        log.info(f"  Duration: {duration_minutes} min")
        log.info(f"  Threshold: ±${self.config.btc_threshold_usd}")
        log.info(f"  Window: {self.config.execution_window_seconds}s")
        log.info("=" * 60)

        while self.running and time.time() < end_time:
            try:
                await self._tick()
                await asyncio.sleep(0.2)
            except Exception as e:
                log.error(f"Monitor error: {e}")
                await asyncio.sleep(1)

        self._print_summary()

    def _print_summary(self):
        log.info("=" * 50)
        log.info("  MONITORING COMPLETE")
        log.info(f"  Contracts discovered: {self._contract_count}")
        log.info(f"  Signals generated: {self._signal_count}")
        log.info(f"  Records saved to: {RECORD_FILE}")
        log.info("=" * 50)

    async def _tick(self):
        markets_raw = await self.scanner.scan()

        if not markets_raw:
            return

        market_id = min(markets_raw, key=lambda mid: _get_expiry(markets_raw[mid]) or float("inf"))
        raw = markets_raw[market_id]
        vd = raw.get("variantData") or {}
        strike = float(vd["startPrice"])
        expiry = _get_expiry(raw)

        if market_id != self._current_market_id:
            self._current_market_id = market_id
            self._current_strike = strike
            self._current_expiry = expiry
            self._contract_count += 1
            log.info(f"→ Contract #{self._contract_count}: {market_id} expires {datetime.fromtimestamp(expiry, tz=timezone.utc).strftime('%H:%M:%S')}Z strike=${strike}")

        if expiry is None:
            return
        remaining = expiry - time.time()
        if remaining > self.config.execution_window_seconds:
            return

        if market_id in self._seen_contracts:
            return
        self._seen_contracts.add(market_id)

        signal = self.strategy.evaluate_all({market_id: raw})

        record = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "market_id": market_id,
            "strike": strike,
            "expiry_ts": expiry,
            "remaining": round(remaining, 2),
            "signal": None,
            "orderbook": None,
        }

        ob = self.client.get_orderbook(market_id)
        if ob:
            record["orderbook"] = {
                "asks": ob.get("asks", []),
                "bids": ob.get("bids", []),
                "lastOrderSettled": ob.get("lastOrderSettled"),
                "updateTimestampMs": ob.get("updateTimestampMs"),
            }

            if signal:
                dominant_side = signal.outcome  # YES or NO
                asks_exist = len(ob.get("asks", [])) > 0
                bids_exist = len(ob.get("bids", [])) > 0
                best_ask = ob["asks"][0][0] if asks_exist else None
                best_bid = ob["bids"][0][0] if bids_exist else None
                spread = (best_ask - best_bid) if (best_ask and best_bid) else None

                record["signal"] = {
                    "outcome": signal.outcome,
                    "side": signal.side,
                    "direction": "UP" if signal.outcome == "YES" else "DOWN",
                    "diff": signal.market_price - signal.market_price + signal.fair_value,
                    "fair_value": signal.fair_value,
                    "asks_count": len(ob["asks"]),
                    "bids_count": len(ob["bids"]),
                    "best_ask": best_ask,
                    "best_bid": best_bid,
                    "spread": spread,
                    "has_sellers": asks_exist,
                    "dominant_side_asks": asks_exist,
                }
                self._signal_count += 1
        else:
            record["orderbook"] = "error"

        self._append_record(record)

        if signal:
            log.warning(
                f"[SIGNAL #{self._signal_count}] mkt={market_id} "
                f"{'UP' if signal.outcome == 'YES' else 'DOWN'} "
                f"diff=${signal.fair_value:.0f} "
                f"asks={record['signal']['asks_count']} "
                f"bestAsk={record['signal']['best_ask']}"
            )

        log.debug(f"Recorded contract {market_id} (remaining={remaining:.1f}s)")

    def _append_record(self, record: dict):
        with open(RECORD_FILE, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=30, help="Minutes to monitor")
    args = parser.parse_args()

    config = Config()
    BotLogger(config)

    monitor = LiquidityMonitor(config)

    def shutdown(signum, frame):
        log.info("Shutting down monitor…")
        monitor.running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    asyncio.run(monitor.run(duration_minutes=args.duration))


if __name__ == "__main__":
    main()
