import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    api_base_url: str = os.getenv(
        "PREDICT_API_URL",
        "https://api.predict.fun",
    )
    ws_url: str = os.getenv(
        "PREDICT_WS_URL",
        "wss://ws.predict.fun/ws",
    )
    api_key: str = os.getenv("PREDICT_API_KEY", "")

    wallet_private_key: str = os.getenv("WALLET_PRIVATE_KEY", "")
    rpc_provider_url: str = os.getenv(
        "RPC_PROVIDER_URL",
        "https://bsc-dataseed.binance.org",
    )

    btc_threshold_usd: float = float(os.getenv("BTC_THRESHOLD_USD", "5"))
    execution_window_seconds: float = float(os.getenv("EXECUTION_WINDOW_SECONDS", "2.0"))
    account_allocation: float = float(os.getenv("ACCOUNT_ALLOCATION", "0.80"))
    max_position_usdt: float = float(os.getenv("MAX_POSITION_USDT", "100"))

    max_conn_attempts: int = int(os.getenv("MAX_CONN_ATTEMPTS", "10"))
    max_retry_interval_ms: int = int(os.getenv("MAX_RETRY_INTERVAL_MS", "30000"))
    heartbeat_timeout_seconds: int = int(os.getenv("HEARTBEAT_TIMEOUT", "45"))

    scan_interval_seconds: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "1"))

    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "predict_bot.log")

    chain_id: int = int(os.getenv("CHAIN_ID", "56"))

    @property
    def is_testnet(self) -> bool:
        return "testnet" in self.api_base_url

    def validate(self) -> list[str]:
        errors = []
        if not self.api_key and not self.is_testnet:
            errors.append("PREDICT_API_KEY is required for mainnet")
        if not self.wallet_private_key:
            errors.append("WALLET_PRIVATE_KEY is required for order signing")
        return errors
