import asyncio
import signal
import time

from config import Config
from auth import PredictAuth
from predict_client import PredictClient
from market_scanner import MarketScanner
from price_oracle import PriceFeedOracle
from strategy import FastExpiryStrategy, _get_expiry
from trader import Trader
from logger import get_logger, BotLogger
from keepalive import KeepAliveServer

log = get_logger("bot")


class PredictBot:
    def __init__(self, config: Config):
        self.config = config
        self.running = False

        self.auth    = PredictAuth(config)
        self.client  = PredictClient(config, self.auth)
        # Oracle reads prices exclusively from predict.fun's own market data.
        # We pass the client so it can do a live GET /v1/markets/{id} refresh
        # if variantData.currentPrice is missing from the cached market dict.
        self.oracle  = PriceFeedOracle(predict_client=self.client)
        self.scanner = MarketScanner(config, self.client)
        self.strategy = FastExpiryStrategy(config, self.oracle)
        self.trader  = Trader(config, self.auth, self.client)
        self.keepalive = KeepAliveServer()

        self._current_market_id: int | None = None
        self._current_strike: float | None = None
        self._current_expiry: float | None = None

    async def start(self):
        print("=== BOT STARTING ===", flush=True)
        log.info("=" * 60)
        log.info("  Predict.fun Fast-Expiry Bot — OKX-style")
        log.info(f"  Mode: LIVE")
        log.info(f"  API: {self.config.api_base_url}")
        log.info(f"  Wallet: {self.auth.wallet_address or 'NOT CONFIGURED'}")
        log.info(f"  BTC threshold: ±${self.config.btc_threshold_usd}")
        log.info(f"  Price source: predict.fun variantData (Chainlink — no Binance)")
        log.info(f"  Window: {self.config.execution_window_seconds}s")
        log.info(f"  Allocation: {self.config.account_allocation:.0%} of balance")
        log.info("=" * 60)

        self.keepalive.start()

        errors = self.config.validate()
        if errors:
            for e in errors:
                log.error(f"Config error: {e}")
            log.error("Fix config errors before running")
            return

        self.running = True

        try:
            self.auth.ensure_jwt()
            log.info("Authentication successful")
        except Exception as e:
            log.error(f"Authentication failed: {e}")
            if not self.config.is_testnet:
                return
            log.warning("Continuing in read-only mode (testnet)")

        try:
            await self._trading_loop()
        except KeyboardInterrupt:
            log.info("Interrupted by user")
        except Exception as e:
            log.error(f"Fatal error: {e}", exc_info=True)
        finally:
            await self.stop()

    async def _trading_loop(self):
        log.info("Entering trading loop…")
        while self.running:
            try:
                await self._tick()
                await asyncio.sleep(0.2)
            except Exception as e:
                log.error(f"Trading loop error: {e}")
                await asyncio.sleep(1)

    async def _tick(self):
        markets_raw = await self.scanner.scan()

        if not markets_raw:
            log.debug("Waiting for 5-min BTC CHAINLINK contract...")
            return

        market_id = min(
            markets_raw,
            key=lambda mid: _get_expiry(markets_raw[mid]) or float("inf")
        )
        raw    = markets_raw[market_id]
        vd     = raw.get("variantData") or {}
        symbol = (vd.get("priceFeedSymbol") or "").upper()
        strike = float(vd.get("startPrice", 0))
        expiry = _get_expiry(raw)

        if market_id != self._current_market_id:
            self._current_market_id = market_id
            self._current_strike    = strike
            self._current_expiry    = expiry

            current_px = vd.get("currentPrice", "?")
            log.info("-" * 50)
            log.info(f"NEXT CONTRACT  : {symbol}-{market_id}")
            log.info(f"Strike         : ${strike}")
            log.info(f"Current price  : ${current_px}  (predict.fun/Chainlink)")
            log.info(f"Expires        : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(expiry)) if expiry else '?'}")
            log.info(f"Question       : {raw.get('question', '')}")
            log.info("-" * 50)

        if expiry is None:
            return
        remaining = expiry - time.time()
        if remaining > self.config.execution_window_seconds:
            if int(remaining) % 10 == 0:
                log.info(f"Waiting... {remaining:.0f}s until execution window")
            return

        signal = self.strategy.evaluate_all({market_id: raw})

        if signal is None:
            return

        result = self.trader.execute_signal(signal)
        if result:
            order_id, outcome, sz = result
            log.warning(f"EXECUTING: marketId={signal.market_id} | outcome={outcome} | sz={sz}")
            log.info(f"ORDER PLACED — ordId={order_id} outcome={outcome.lower()} sz={sz}")

    async def stop(self):
        log.info("Stopping bot…")
        self.running = False
        self.keepalive.stop()


def main():
    config = Config()
    BotLogger(config)

    bot = PredictBot(config)

    def shutdown(signum, frame):
        log.info(f"Received signal {signum}, shutting down…")
        bot.running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    asyncio.run(bot.start())


if __name__ == "__main__":
    main()
