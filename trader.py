import time
import random
from typing import Optional

from config import Config
from auth import PredictAuth
from predict_client import PredictClient
from models import TradeSignal
from logger import get_logger

log = get_logger("trader")

USDT_DECIMALS = 6
SHARE_DECIMALS = 18


def to_wei(value: float, decimals: int = SHARE_DECIMALS) -> str:
    return str(int(value * (10 ** decimals)))


class Trader:
    def __init__(
        self,
        config: Config,
        auth: PredictAuth,
        client: PredictClient,
    ):
        self.config = config
        self.auth = auth
        self.client = client

    def execute_signal(self, signal: TradeSignal) -> Optional[tuple[str, str, str]]:
        portfolio = self.client.get_portfolio()
        if portfolio:
            balance = float(
                portfolio.get("usdBalance")
                or portfolio.get("balance")
                or portfolio.get("availableBalance")
                or 0
            )
        else:
            balance = self.config.max_position_usdt
            log.warning(f"Could not fetch balance, using default ${balance:.2f}")

        value_usdt = balance * signal.size_alloc
        log.warning(
            f"EXECUTING: marketId={signal.market_id} | "
            f"outcome={signal.outcome} | "
            f"sz={value_usdt:.1f} | "
            f"costPerUnit=$0.9980 | "
            f"balance=${balance:.4f}"
        )

        _, market_raw = self.client.get_market_with_raw(signal.market_id)
        if not market_raw:
            log.error(f"Market {signal.market_id} not found")
            return None

        token_id = self._get_token_id(market_raw, signal.outcome)
        if not token_id:
            log.error(f"Token ID not found for {signal.outcome} in market {signal.market_id}")
            return None

        order_hash = self._submit_market_fok(
            market_raw=market_raw,
            token_id=token_id,
            value_usdt=value_usdt,
            outcome=signal.outcome,
        )
        if not order_hash:
            return None

        return order_hash, signal.outcome, f"{value_usdt:.1f}"

    def _submit_market_fok(
        self,
        market_raw: dict,
        token_id: str,
        value_usdt: float,
        outcome: str,
    ) -> Optional[str]:
        try:
            self.auth.ensure_jwt()
        except Exception as e:
            log.error(f"JWT auth failed: {e}")
            return None

        fee_bps = market_raw.get("feeRateBps", 0)
        is_neg_risk = market_raw.get("isNegRisk", False)
        is_yield_bearing = market_raw.get("isYieldBearing", False)

        value_usdt_wei = to_wei(value_usdt, USDT_DECIMALS)
        salt = str(random.randint(1, 2 ** 63 - 1))
        expiration = str(int(time.time()) + 30)

        maker = self.auth.wallet_address
        signer = self.auth.wallet_address
        taker = "0x0000000000000000000000000000000000000000"

        order_data = {
            "salt": salt,
            "maker": maker,
            "signer": signer,
            "taker": taker,
            "tokenId": str(token_id),
            "makerAmount": value_usdt_wei,
            "takerAmount": value_usdt_wei,
            "expiration": expiration,
            "nonce": "0",
            "feeRateBps": str(fee_bps),
            "side": 0,
            "signatureType": 0,
        }

        try:
            signed_order, order_hash = self.auth.sign_order(
                order_data,
                is_neg_risk=is_neg_risk,
                is_yield_bearing=is_yield_bearing,
            )
        except Exception as e:
            log.error(f"Order signing failed: {e}")
            return None

        body = {
            "data": {
                "order": {
                    "hash": order_hash,
                    "salt": signed_order["salt"],
                    "maker": maker,
                    "signer": signer,
                    "taker": taker,
                    "tokenId": str(token_id),
                    "makerAmount": signed_order["makerAmount"],
                    "takerAmount": signed_order["takerAmount"],
                    "expiration": signed_order["expiration"],
                    "nonce": signed_order["nonce"],
                    "feeRateBps": signed_order["feeRateBps"],
                    "side": signed_order["side"],
                    "signatureType": signed_order["signatureType"],
                    "signature": signed_order["signature"],
                },
                "pricePerShare": "1",
                "strategy": "MARKET",
                "slippageBps": "10000",
            }
        }

        try:
            resp = self.client.session.post(
                f"{self.client.base_url}/v1/oauth/orders",
                headers=self.auth.jwt_headers,
                json=body,
                timeout=10,
            )

            if resp.status_code in (200, 201):
                result = resp.json()
                if result.get("success"):
                    order_id = result.get("data", {}).get("orderId", order_hash)
                    log.info(
                        f"MARKET FOK submitted: market={market_raw['id']} "
                        f"outcome={outcome} value={value_usdt:.2f} USDT "
                        f"orderId={order_id}"
                    )
                    return order_id
                else:
                    log.error(f"Order failed: {result}")
                    return None
            else:
                log.error(
                    f"Order HTTP {resp.status_code}: {resp.text[:200]}"
                )
                return None

        except Exception as e:
            log.error(f"Order submission error: {e}")
            return None

    def _get_token_id(self, market_raw: dict, outcome: str) -> Optional[str]:
        for o in market_raw.get("outcomes", []):
            if o.get("name", "").upper() == outcome.upper():
                return o.get("onChainId", "")
        return None
