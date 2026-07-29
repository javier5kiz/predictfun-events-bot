"""
Authentication module for the Predict.fun API.
Handles API key auth, EIP-712 message signing, and JWT token retrieval.
"""

import json
import time
import logging
from typing import Optional

import requests
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

from config import Config
from logger import get_logger

log = get_logger("auth")


def _normalise_private_key(raw: str) -> str:
    """
    Ensure the private key is a valid 0x-prefixed 64-char hex string.
    eth-account / HexBytes will crash with 'Non-hexadecimal digit found'
    if the key has no 0x prefix or contains whitespace.
    """
    key = raw.strip()
    if not key:
        return key
    # Strip any accidental quotes that might come in from .env
    key = key.strip('"').strip("'")
    if not key.startswith("0x"):
        key = "0x" + key
    return key


class PredictAuth:
    """Manages API key + JWT authentication and EIP-712 order signing."""

    def __init__(self, config: Config):
        self.config = config
        self.api_key = config.api_key
        self.api_base = config.api_base_url

        raw_key = config.wallet_private_key or ""
        self.wallet_private_key = _normalise_private_key(raw_key)

        if self.wallet_private_key:
            try:
                self.account = Account.from_key(self.wallet_private_key)
                self.wallet_address = self.account.address
            except Exception as e:
                log.error(f"Invalid WALLET_PRIVATE_KEY: {e}")
                self.account = None
                self.wallet_address = ""
        else:
            self.account = None
            self.wallet_address = ""

        self.jwt_token: Optional[str] = None
        self.jwt_expires_at: float = 0
        self._w3 = Web3(Web3.HTTPProvider(config.rpc_provider_url))

    # ── API Key header ──────────────────────────────────────────────

    @property
    def api_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    # ── JWT for order endpoints ──────────────────────────────────────

    @property
    def jwt_headers(self) -> dict:
        headers = self.api_headers
        if self.jwt_token:
            headers["Authorization"] = f"Bearer {self.jwt_token}"
        return headers

    def ensure_jwt(self) -> str:
        if self.jwt_token and time.time() < self.jwt_expires_at - 60:
            return self.jwt_token
        return self._fetch_jwt()

    def _fetch_jwt(self) -> str:
        """
        Auth flow:
        1. GET /v1/auth/message  → get the message to sign
        2. Sign with wallet private key (EIP-191 personal_sign)
        3. POST /v1/auth         → exchange signature for JWT
        """
        if not self.account:
            raise RuntimeError("No wallet private key configured — cannot authenticate")

        log.info("Fetching auth message from Predict.fun…")
        resp = requests.get(
            f"{self.api_base}/v1/auth/message",
            headers=self.api_headers,
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            raise RuntimeError(f"Auth message request failed: {body}")

        message = body["data"]["message"]
        log.debug(f"Auth message: {message}")

        signed = self.account.sign_message(encode_defunct(text=message))
        sig_bytes = signed.signature
        signature = "0x" + sig_bytes.hex()

        log.info("Auth message signed, exchanging for JWT…")

        resp = requests.post(
            f"{self.api_base}/v1/auth",
            headers=self.api_headers,
            json={
                "signer": self.wallet_address,
                "signature": signature,
                "message": message,
            },
            timeout=10,
        )
        resp.raise_for_status()
        body = resp.json()

        if not body.get("success"):
            raise RuntimeError(f"JWT request failed: {body}")

        self.jwt_token = body["data"]["token"]
        self.jwt_expires_at = time.time() + 3500
        log.info("JWT token obtained successfully ✓")
        return self.jwt_token

    # ── EIP-712 Order Signing ────────────────────────────────────────

    def sign_order(self, order: dict, is_neg_risk: bool, is_yield_bearing: bool) -> tuple[dict, str]:
        """
        Sign an EIP-712 typed-data order for Predict.fun.

        All uint256 fields must be Python ints (not strings) when signing —
        eth-account passes them through HexBytes internally which breaks on
        decimal strings. String-encoding happens after signing for the API payload.
        """
        exchange_address = _get_exchange_address(is_neg_risk)

        # ── Coerce types: uint256 → int, address → checksum str ──────
        salt        = int(order.get("salt", 0))
        maker       = Web3.to_checksum_address(order["maker"])
        signer      = Web3.to_checksum_address(order["signer"])
        taker       = Web3.to_checksum_address(
                          order.get("taker", "0x0000000000000000000000000000000000000000"))
        token_id    = int(order["tokenId"])
        maker_amt   = int(order["makerAmount"])
        taker_amt   = int(order["takerAmount"])
        expiration  = int(order.get("expiration", 0))
        nonce       = int(order.get("nonce", 0))
        fee_bps     = int(order.get("feeRateBps", 0))
        side        = int(order.get("side", 0))
        sig_type    = int(order.get("signatureType", 0))

        domain = {
            "name":              "CTF Exchange",
            "version":           "1",
            "chainId":           self.config.chain_id,
            "verifyingContract": Web3.to_checksum_address(exchange_address),
        }

        message_types = {
            "Order": [
                {"name": "salt",          "type": "uint256"},
                {"name": "maker",         "type": "address"},
                {"name": "signer",        "type": "address"},
                {"name": "taker",         "type": "address"},
                {"name": "tokenId",       "type": "uint256"},
                {"name": "makerAmount",   "type": "uint256"},
                {"name": "takerAmount",   "type": "uint256"},
                {"name": "expiration",    "type": "uint256"},
                {"name": "nonce",         "type": "uint256"},
                {"name": "feeRateBps",    "type": "uint256"},
                {"name": "side",          "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
            ]
        }

        message_data = {
            "salt":          salt,
            "maker":         maker,
            "signer":        signer,
            "taker":         taker,
            "tokenId":       token_id,
            "makerAmount":   maker_amt,
            "takerAmount":   taker_amt,
            "expiration":    expiration,
            "nonce":         nonce,
            "feeRateBps":    fee_bps,
            "side":          side,
            "signatureType": sig_type,
        }

        # sign_typed_data is the clean API in eth-account >= 0.9
        signed = self.account.sign_typed_data(
            domain_data=domain,
            message_types=message_types,
            message_data=message_data,
        )

        sig_hex = "0x" + signed.signature.hex()
        order_hash = "0x" + signed.messageHash.hex() if hasattr(signed, "messageHash") else ""

        # API payload uses string-encoded ints
        signed_order = {
            "salt":          str(salt),
            "maker":         maker,
            "signer":        signer,
            "taker":         taker,
            "tokenId":       str(token_id),
            "makerAmount":   str(maker_amt),
            "takerAmount":   str(taker_amt),
            "expiration":    str(expiration),
            "nonce":         str(nonce),
            "feeRateBps":    str(fee_bps),
            "side":          side,
            "signatureType": sig_type,
            "signature":     sig_hex,
        }

        log.debug(f"Order signed: hash={order_hash[:20]}…")
        return signed_order, order_hash


# ── Contract addresses ───────────────────────────────────────────────

_CTF_EXCHANGE          = "0x8BC0922DD795CfD3aA765B6893Ee93dF5e5cDf69"
_NEG_RISK_CTF_EXCHANGE = "0xC5d561CDbA1dF24F58B3f1B6F4D646f7d3A11C12"


def _get_exchange_address(is_neg_risk: bool) -> str:
    return _NEG_RISK_CTF_EXCHANGE if is_neg_risk else _CTF_EXCHANGE
