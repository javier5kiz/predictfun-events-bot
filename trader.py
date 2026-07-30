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
        # ── Fetch REAL on-chain USDT balance ──────────────────────────
        # Check the Predict Account (smart wallet) first, fall back to EOA
        balance_address = self.auth.maker_address
        log.info(f"Checking USDT balance for: {balance_address}")

        balance = self.client.get_usdt_balance(balance_address)
        if balance is None or balance <= 0:
            # Also try EOA in case funds are there
            if balance_address != self.auth.wallet_address:
                log.info(f"No balance in smart wallet, checking EOA: {self.auth.wallet_address}")
                balance = self.client.get_usdt_balance(self.auth.wallet_address)
            if balance is None or balance <= 0:
                log.warning(
                    f"Could not fetch on-chain USDT balance for {balance_address}"
                    f"{' or EOA ' + self.auth.wallet_address if balance_address != self.auth.wallet_address else ''}, "
                    f"using config default ${self.config.max_position_usdt:.2f}"
                )
                balance = self.config.max_position_usdt
            else:
                log.info(f"Real USDT balance (EOA): ${balance:.2f}")
        else:
            log.info(f"Real USDT balance (smart wallet): ${balance:.2f}")

        value_usdt = balance * signal.size_alloc
        if value_usdt < 1.0:
            log.warning(
                f"Position size too small: ${value_usdt:.2f} "
                f"(balance=${balance:.2f} alloc={signal.size_alloc:.0%}). Skipping."
            )
            return None

        log.warning(
            f"EXECUTING: marketId={signal.market_id} | "
            f"outcome={signal.outcome} | "
            f"sz={value_usdt:.1f} | "
            f"maker={self.auth.maker_address} | "
            f"sigType={self.auth.signature_type} | "
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

        Predict.fun returns token IDs in the outcomes array, typically
        under the `onChainId` field. We also check alternative field names
        and variantData.outcomes as a secondary source.

        For binary markets where outcome names are empty or don't match,
        falls back to index-based lookup (outcomes[0] = YES, outcomes[1] = NO).
        """
        target = outcome.upper().strip()
        outcomes = market_raw.get("outcomes", [])

        # ── Pass 1: match by name ─────────────────────────────────────
        for o in outcomes:
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
                log.debug(f"Token ID for {outcome}: {token_id}")
                return str(token_id)

        # ── Pass 2: variantData.outcomes ──────────────────────────────
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

        # ── Pass 3: index-based fallback for binary markets ───────────
        # If we have exactly 2 outcomes, assume [0]=YES, [1]=NO
        if len(outcomes) == 2:
            idx = 0 if target == "YES" else 1
            o = outcomes[idx]
            token_id = (
                o.get("onChainId")
                or o.get("tokenId")
                or o.get("token_id")
                or o.get("id")
            )
            if token_id:
                log.debug(f"Token ID (index fallback #{idx}): {token_id}")
                return str(token_id)

        # ── Pass 4: if only 1 outcome, use it ─────────────────────────
        if len(outcomes) == 1:
            token_id = (
                outcomes[0].get("onChainId")
                or outcomes[0].get("tokenId")
                or outcomes[0].get("token_id")
                or outcomes[0].get("id")
            )
            if token_id:
                log.debug(f"Token ID (single outcome fallback): {token_id}")
                return str(token_id)

        log.error(
            f"_get_token_id exhausted all strategies. "
            f"outcome={outcome}, outcomes_count={len(outcomes)}, "
            f"outcome_data={[(o.get('name'), o.get('onChainId'), o.get('tokenId')) for o in outcomes]}"
        )
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

        # maker = the address holding USDT (Predict Account or EOA)
        # signer = the EOA that signs the order
        maker  = self.auth.maker_address
        signer = self.auth.wallet_address
        taker  = "0x0000000000000000000000000000000000000000"
        sig_type = self.auth.signature_type

        log.info(
            f"Order params: maker={maker} signer={signer} "
            f"sigType={sig_type} negRisk={is_neg_risk} yieldBearing={is_yield_bearing}"
        )

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
            "signatureType": sig_type,
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

        # Guard: order_hash must be a non-empty string
        if not order_hash:
            log.error("Order hash is empty — signing may have failed silently")
            return None

        body = {
            "data": {
                "order": {
                    "hash":          str(order_hash),
                    "salt":          str(signed_order["salt"]),
                    "maker":         str(signed_order["maker"]),
                    "signer":        str(signed_order["signer"]),
                    "taker":         str(signed_order["taker"]),
                    "tokenId":       str(token_id),
                    "makerAmount":   str(signed_order["makerAmount"]),
                    "takerAmount":   str(signed_order["takerAmount"]),
                    "expiration":    str(signed_order["expiration"]),
                    "nonce":         str(signed_order["nonce"]),
                    "feeRateBps":    str(signed_order["feeRateBps"]),
                    "side":          int(signed_order["side"]),
                    "signatureType": int(signed_order["signatureType"]),
                    "signature":     str(signed_order["signature"]),
                },
                "pricePerShare": "1",
                "strategy":      "MARKET",
                "slippageBps":   "10000",
                "isFillOrKill":  True,
            }
        }

        try:
            resp = self.client.session.post(
                f"{self.client.base_url}/v1/orders",
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
