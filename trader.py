import time
import random
from typing import Optional

from config import Config
from auth import PredictAuth
from predict_client import PredictClient
from models import TradeSignal
from logger import get_logger

log = get_logger("trader")

USDT_DECIMALS  = 6
SHARE_DECIMALS = 18


def to_wei(value: float, decimals: int = SHARE_DECIMALS) -> str:
    return str(int(value * (10 ** decimals)))


class Trader:
    def __init__(self, config: Config, auth: PredictAuth, client: PredictClient):
        self.config = config
        self.auth   = auth
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

        # Always do a fresh market fetch so we get full outcomes with token IDs
        _, market_raw = self.client.get_market_with_raw(signal.market_id)
        if not market_raw:
            log.error(f"Market {signal.market_id} not found")
            return None

        # Debug: log what outcomes look like so we can trace mismatches
        outcomes_raw = market_raw.get("outcomes", [])
        log.debug(f"Market {signal.market_id} outcomes raw: {outcomes_raw}")

        token_id = self._get_token_id(market_raw, signal.outcome)
        if not token_id:
            log.error(
                f"Token ID not found for {signal.outcome} in market {signal.market_id}. "
                f"Outcomes: {outcomes_raw}"
            )
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

    def _get_token_id(self, market_raw: dict, outcome: str) -> Optional[str]:
        """
        Find the on-chain token ID for a given outcome name (YES / NO).

        predict.fun API may return token IDs under different field names:
          - outcomes[].onChainId       (camelCase)
          - outcomes[].tokenId         (alternative)
          - outcomes[].token_id        (snake_case variant)
          - outcomes[].id              (fallback)

        Outcome name matching is case-insensitive.
        """
        target = outcome.upper().strip()

        for o in market_raw.get("outcomes", []):
            name = (o.get("name") or o.get("label") or "").upper().strip()
            if name != target:
                continue

            # Try all known token ID field names
            token_id = (
                o.get("onChainId")
                or o.get("tokenId")
                or o.get("token_id")
                or o.get("id")
            )
            if token_id:
                log.debug(f"Token ID for {outcome}: {token_id}")
                return str(token_id)

        # Second pass: try variantData.outcomes if top-level outcomes is empty
        vd = market_raw.get("variantData") or {}
        for o in vd.get("outcomes", []):
            name = (o.get("name") or o.get("label") or "").upper().strip()
            if name != target:
                continue
            token_id = (
                o.get("onChainId")
                or o.get("tokenId")
                or o.get("token_id")
                or o.get("id")
            )
            if token_id:
                log.debug(f"Token ID (variantData) for {outcome}: {token_id}")
                return str(token_id)

        return None

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

        fee_bps          = market_raw.get("feeRateBps", 0)
        is_neg_risk      = market_raw.get("isNegRisk", False)
        is_yield_bearing = market_raw.get("isYieldBearing", False)

        value_usdt_wei = to_wei(value_usdt, USDT_DECIMALS)
        salt           = str(random.randint(1, 2 ** 63 - 1))
        expiration     = str(int(time.time()) + 30)

        maker  = self.auth.wallet_address
        signer = self.auth.wallet_address
        taker  = "0x0000000000000000000000000000000000000000"

        order_data = {
            "salt":          salt,
            "maker":         maker,
            "signer":        signer,
            "taker":         taker,
            "tokenId":       str(token_id),
            "makerAmount":   value_usdt_wei,
            "takerAmount":   value_usdt_wei,
            "expiration":    expiration,
            "nonce":         "0",
            "feeRateBps":    str(fee_bps),
            "side":          0,
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
                    "hash":          order_hash,
                    "salt":          signed_order["salt"],
                    "maker":         maker,
                    "signer":        signer,
                    "taker":         taker,
                    "tokenId":       str(token_id),
                    "makerAmount":   signed_order["makerAmount"],
                    "takerAmount":   signed_order["takerAmount"],
                    "expiration":    signed_order["expiration"],
                    "nonce":         signed_order["nonce"],
                    "feeRateBps":    signed_order["feeRateBps"],
                    "side":          signed_order["side"],
                    "signatureType": signed_order["signatureType"],
                    "signature":     signed_order["signature"],
                },
                "pricePerShare": "1",
                "strategy":      "MARKET",
                "slippageBps":   "10000",
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
                log.error(f"Order HTTP {resp.status_code}: {resp.text[:300]}")
                return None
        except Exception as e:
            log.error(f"Order submission error: {e}")
            return None
