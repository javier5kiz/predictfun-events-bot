"""
REST API client for Predict.fun.
Handles market discovery, order management, and position queries.
"""

import time
import logging
from typing import Optional, Any

import requests
from web3 import Web3

from config import Config
from auth import PredictAuth
from models import Market, MarketStats, OrderStatus, OrderStrategy
from logger import get_logger

log = get_logger("predict_client")

# USDT contract on BNB Mainnet (6 decimals)
_USDT_CONTRACT = "0x55d398326f99059fF775485246999027B3197955"
_BALANCEOF_ABI = [{"constant": True, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "payable": False, "stateMutability": "view", "type": "function"}]


class PredictClient:
    """Thin wrapper around the Predict.fun REST API."""

    def __init__(self, config: Config, auth: PredictAuth):
        self.config = config
        self.auth = auth
        self.base_url = config.api_base_url
        self.session = requests.Session()
        self._w3 = Web3(Web3.HTTPProvider(config.rpc_provider_url))

    # ── Markets ──────────────────────────────────────────────────────

    def get_markets(
        self,
        status: str = "OPEN",
        market_variant: Optional[str] = None,
        sort: Optional[str] = None,
        first: int = 50,
        after: Optional[str] = None,
    ) -> tuple[list[Market], Optional[str]]:
        params = {"status": status, "first": str(first)}
        if market_variant:
            params["marketVariant"] = market_variant
        if sort:
            params["sort"] = sort
        if after:
            params["after"] = after

        resp = self.session.get(
            f"{self.base_url}/v1/markets",
            headers=self.auth.api_headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            log.warning(f"get_markets returned success=false: {body}")
            return [], None

        markets = [Market.from_api(m) for m in body.get("data", [])]
        cursor = body.get("cursor")
        log.debug(f"Fetched {len(markets)} markets (cursor={cursor})")
        return markets, cursor

    def get_markets_with_raw(
        self,
        status: str = "OPEN",
        market_variant: Optional[str] = None,
        sort: Optional[str] = None,
        first: int = 50,
        after: Optional[str] = None,
    ) -> tuple[list[Market], list[dict], Optional[str]]:
        params = {"status": status, "first": str(first)}
        if market_variant:
            params["marketVariant"] = market_variant
        if sort:
            params["sort"] = sort
        if after:
            params["after"] = after

        resp = self.session.get(
            f"{self.base_url}/v1/markets",
            headers=self.auth.api_headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            return [], [], None

        raw_list = body.get("data", [])
        markets = [Market.from_api(m) for m in raw_list]
        cursor = body.get("cursor")
        return markets, raw_list, cursor

    def get_all_markets_with_raw(
        self,
        status: str = "OPEN",
        market_variant: Optional[str] = None,
        sort: Optional[str] = None,
        max_pages: int = 20,
    ) -> tuple[list[Market], list[dict]]:
        all_markets: list[Market] = []
        all_raws: list[dict] = []
        cursor = None

        for _ in range(max_pages):
            markets, raws, cursor = self.get_markets_with_raw(
                status=status,
                market_variant=market_variant,
                sort=sort,
                after=cursor,
            )
            all_markets.extend(markets)
            all_raws.extend(raws)
            if not cursor:
                break

        log.info(f"Total markets fetched: {len(all_markets)}")
        return all_markets, all_raws

    def get_market(self, market_id: int) -> Optional[Market]:
        resp = self.session.get(
            f"{self.base_url}/v1/markets/{market_id}",
            headers=self.auth.api_headers,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success"):
            return Market.from_api(body["data"])
        return None

    def get_market_with_raw(self, market_id: int) -> tuple[Optional[Market], Optional[dict]]:
        resp = self.session.get(
            f"{self.base_url}/v1/markets/{market_id}",
            headers=self.auth.api_headers,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success"):
            raw = body["data"]
            return Market.from_api(raw), raw
        return None, None

    def get_market_stats(self, market_id: int) -> Optional[MarketStats]:
        resp = self.session.get(
            f"{self.base_url}/v1/markets/{market_id}/stats",
            headers=self.auth.api_headers,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success") and body.get("data"):
            d = body["data"]
            return MarketStats(
                total_liquidity_usd=d.get("totalLiquidityUsd", 0.0),
                liquidity_3c_ask_usd=d.get("liquidity3cAskUsd", 0.0),
                volume_total_usd=d.get("volumeTotalUsd", 0.0),
                volume_24h_usd=d.get("volume24hUsd", 0.0),
            )
        return None

    def get_orderbook(self, market_id: int) -> Optional[dict]:
        resp = self.session.get(
            f"{self.base_url}/v1/markets/{market_id}/orderbook",
            headers=self.auth.api_headers,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success"):
            return body.get("data")
        return None

    # ── Orders ───────────────────────────────────────────────────────

    def get_orders(self, status: Optional[str] = None, first: int = 50) -> list[dict]:
        self.auth.ensure_jwt()
        params = {"first": str(first)}
        if status:
            params["status"] = status

        resp = self.session.get(
            f"{self.base_url}/v1/orders",
            headers=self.auth.jwt_headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success"):
            return body.get("data", [])
        return []

    def get_order(self, order_hash: str) -> Optional[dict]:
        self.auth.ensure_jwt()
        resp = self.session.get(
            f"{self.base_url}/v1/orders/{order_hash}",
            headers=self.auth.jwt_headers,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success"):
            return body.get("data")
        return None

    def cancel_order(self, order_hash: str, is_neg_risk: bool, is_yield_bearing: bool) -> bool:
        self.auth.ensure_jwt()
        log.info(f"Cancelling order {order_hash} (negRisk={is_neg_risk})")
        try:
            resp = self.session.delete(
                f"{self.base_url}/v1/orders/{order_hash}",
                headers=self.auth.jwt_headers,
                json={"isNegRisk": is_neg_risk, "isYieldBearing": is_yield_bearing},
                timeout=10,
            )
            if resp.status_code in (200, 204):
                log.info(f"Order {order_hash} cancelled successfully")
                return True
            else:
                log.error(f"Cancel order HTTP {resp.status_code}: {resp.text[:200]}")
                return False
        except Exception as e:
            log.error(f"Cancel order error: {e}")
            return False

    def get_order_matches(
        self,
        market_id: Optional[int] = None,
        signer_address: Optional[str] = None,
        min_value_usdt_wei: Optional[str] = None,
        first: int = 50,
    ) -> list[dict]:
        self.auth.ensure_jwt()
        params = {"first": str(first)}
        if market_id:
            params["marketId"] = str(market_id)
        if signer_address:
            params["signerAddress"] = signer_address
        if min_value_usdt_wei:
            params["minValueUsdtWei"] = min_value_usdt_wei

        resp = self.session.get(
            f"{self.base_url}/v1/orders/matches",
            headers=self.auth.jwt_headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success"):
            return body.get("data", [])
        return []

    # ── Positions ────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        self.auth.ensure_jwt()
        resp = self.session.get(
            f"{self.base_url}/v1/oauth/positions",
            headers=self.auth.jwt_headers,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()
        if body.get("success"):
            return body.get("data", [])
        return []

    # ── Balance (on-chain via USDT balanceOf) ─────────────────────────

    def get_usdt_balance(self, wallet_address: str) -> Optional[float]:
        """
        Fetch the real USDT balance on BNB Mainnet by calling
        balanceOf on the USDT contract directly.

        The Predict.fun REST API does not expose a portfolio/balance
        endpoint — the balance lives on-chain.
        """
        try:
            if not wallet_address:
                log.warning("Cannot fetch balance: no wallet address")
                return None

            contract = self._w3.eth.contract(
                address=Web3.to_checksum_address(_USDT_CONTRACT),
                abi=_BALANCEOF_ABI,
            )
            raw_balance = contract.functions.balanceOf(
                Web3.to_checksum_address(wallet_address)
            ).call()
            # USDT on BSC uses 6 decimals
            balance = raw_balance / (10 ** 6)
            log.info(f"USDT balance: {balance:.2f} (raw={raw_balance})")
            return balance
        except Exception as e:
            log.error(f"Failed to fetch USDT balance on-chain: {e}")
            return None

    # ── Account ──────────────────────────────────────────────────────

    def get_account(self) -> Optional[dict]:
        """GET /v1/account — connected account info (name, address, points)."""
        try:
            self.auth.ensure_jwt()
            resp = self.session.get(
                f"{self.base_url}/v1/account",
                headers=self.auth.jwt_headers,
                timeout=10,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("success"):
                return body.get("data")
        except Exception as e:
            log.debug(f"Account fetch failed: {e}")
        return None

    # ── Health ──────────────────────────────────────────────────────

    def health_check(self) -> bool:
        try:
            markets, _ = self.get_markets(first=1)
            return len(markets) > 0
        except Exception as e:
            log.error(f"Health check failed: {e}")
            return False
