"""
Data models for the Predict.fun bot.
Typed structures for markets, orders, orderbook updates, and wallet events.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any
import time


class Side(Enum):
    BUY = 0
    SELL = 1


class OrderStrategy(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    INVALIDATED = "INVALIDATED"


class MarketStatus(Enum):
    REGISTERED = "REGISTERED"
    PRICE_PROPOSED = "PRICE_PROPOSED"
    PRICE_DISPUTED = "PRICE_DISPUTED"
    PAUSED = "PAUSED"
    UNPAUSED = "UNPAUSED"
    RESOLVED = "RESOLVED"
    REMOVED = "REMOVED"


class TradingStatus(Enum):
    OPEN = "OPEN"
    MATCHING_NOT_ENABLED = "MATCHING_NOT_ENABLED"
    CANCEL_ONLY = "CANCEL_ONLY"
    CLOSED = "CLOSED"


class WalletEventType(Enum):
    ORDER_ACCEPTED = "orderAccepted"
    ORDER_NOT_ACCEPTED = "orderNotAccepted"
    ORDER_EXPIRED = "orderExpired"
    ORDER_CANCELLED = "orderCancelled"
    ORDER_TX_SUBMITTED = "orderTransactionSubmitted"
    ORDER_TX_SUCCESS = "orderTransactionSuccess"
    ORDER_TX_FAILED = "orderTransactionFailed"


@dataclass
class Outcome:
    name: str
    index_set: int
    on_chain_id: str
    status: Optional[str] = None
    price: Optional[str] = None  # from market status updates


@dataclass
class MarketStats:
    total_liquidity_usd: float = 0.0
    liquidity_3c_ask_usd: float = 0.0
    volume_total_usd: float = 0.0
    volume_24h_usd: float = 0.0


@dataclass
class Market:
    """Represents a Predict.fun prediction market."""
    id: int
    title: str
    question: str
    description: str
    trading_status: str
    status: str
    is_visible: bool = True
    is_neg_risk: bool = False
    is_yield_bearing: bool = False
    fee_rate_bps: int = 0
    condition_id: str = ""
    resolver_address: str = ""
    outcomes: list[Outcome] = field(default_factory=list)
    spread_threshold: float = 0.0
    share_threshold: int = 0
    is_boosted: bool = False
    category_slug: str = ""
    created_at: str = ""
    decimal_precision: int = 6
    market_variant: str = ""
    stats: Optional[MarketStats] = None
    image_url: str = ""
    expires_at: Optional[str] = None

    @classmethod
    def from_api(cls, data: dict) -> "Market":
        outcomes = [
            Outcome(
                name=o.get("name", ""),
                index_set=o.get("indexSet", 0),
                on_chain_id=o.get("onChainId", ""),
                status=o.get("status"),
            )
            for o in data.get("outcomes", [])
        ]
        stats_data = data.get("stats") or {}
        stats = MarketStats(
            total_liquidity_usd=stats_data.get("totalLiquidityUsd", 0.0),
            liquidity_3c_ask_usd=stats_data.get("liquidity3cAskUsd", 0.0),
            volume_total_usd=stats_data.get("volumeTotalUsd", 0.0),
            volume_24h_usd=stats_data.get("volume24hUsd", 0.0),
        ) if stats_data else None

        expires_at = (
            data.get("expiresAt")
            or data.get("closeAt")
            or data.get("endDate")
            or (data.get("variantData") or {}).get("closeTime")
        )

        return cls(
            id=data["id"],
            title=data.get("title", ""),
            question=data.get("question", ""),
            description=data.get("description", ""),
            trading_status=data.get("tradingStatus", ""),
            status=data.get("status", ""),
            is_visible=data.get("isVisible", True),
            is_neg_risk=data.get("isNegRisk", False),
            is_yield_bearing=data.get("isYieldBearing", False),
            fee_rate_bps=data.get("feeRateBps", 0),
            condition_id=data.get("conditionId", ""),
            resolver_address=data.get("resolverAddress", ""),
            outcomes=outcomes,
            spread_threshold=data.get("spreadThreshold", 0.0),
            share_threshold=data.get("shareThreshold", 0),
            is_boosted=data.get("isBoosted", False),
            category_slug=data.get("categorySlug", ""),
            created_at=data.get("createdAt", ""),
            decimal_precision=data.get("decimalPrecision", 6),
            market_variant=data.get("marketVariant", ""),
            stats=stats,
            image_url=data.get("imageUrl", ""),
            expires_at=expires_at,
        )

    @property
    def is_tradable(self) -> bool:
        return (
            self.trading_status == TradingStatus.OPEN.value
            and self.status in (MarketStatus.UNPAUSED.value, MarketStatus.REGISTERED.value)
            and self.is_visible
        )


@dataclass
class OrderbookLevel:
    """A single price level in the orderbook: [price, quantity]."""
    price: float
    quantity: float

    @classmethod
    def from_array(cls, arr: list) -> "OrderbookLevel":
        return cls(price=arr[0], quantity=arr[1])


@dataclass
class OrderbookUpdate:
    """Live orderbook data pushed via the predictOrderbook WS topic."""
    market_id: int
    update_timestamp_ms: int
    order_count: int
    asks: list[OrderbookLevel] = field(default_factory=list)
    bids: list[OrderbookLevel] = field(default_factory=list)
    last_order_settled: Optional[dict] = None
    settlements_pending: Optional[dict] = None

    @classmethod
    def from_ws(cls, data: dict) -> "OrderbookUpdate":
        return cls(
            market_id=data.get("marketId", 0),
            update_timestamp_ms=data.get("updateTimestampMs", 0),
            order_count=data.get("orderCount", 0),
            asks=[OrderbookLevel.from_array(l) for l in data.get("asks", [])],
            bids=[OrderbookLevel.from_array(l) for l in data.get("bids", [])],
            last_order_settled=data.get("lastOrderSettled"),
            settlements_pending=data.get("settlementsPending"),
        )

    @property
    def best_bid(self) -> Optional[OrderbookLevel]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[OrderbookLevel]:
        return self.asks[0] if self.asks else None

    @property
    def spread(self) -> float:
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return 1.0  # no liquidity = max spread

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None


@dataclass
class MarketStatusUpdate:
    """From the predictMarketStatus WS topic."""
    market_id: int
    status: str
    timestamp_ms: int
    outcomes: list[dict] = field(default_factory=list)

    @classmethod
    def from_ws(cls, data: dict) -> "MarketStatusUpdate":
        return cls(
            market_id=data.get("marketId", 0),
            status=data.get("status", ""),
            timestamp_ms=data.get("tsMs", 0),
            outcomes=data.get("marketOutcomes", []),
        )


@dataclass
class WalletEvent:
    """Live wallet event from predictWalletEvents WS topic."""
    event_type: str
    order_id: str
    order_hash: str
    timestamp: int
    wallet_address: str = ""
    details: dict = field(default_factory=dict)

    @classmethod
    def from_ws(cls, data: dict) -> "WalletEvent":
        return cls(
            event_type=data.get("type", ""),
            order_id=data.get("orderId", ""),
            order_hash=data.get("orderHash", ""),
            timestamp=data.get("timestamp", 0),
            wallet_address=data.get("walletAddress", ""),
            details=data.get("details", {}),
        )

    @property
    def market_id(self) -> int:
        return self.details.get("marketId", 0)

    @property
    def outcome(self) -> str:
        return self.details.get("outcome", "")

    @property
    def price(self) -> str:
        return self.details.get("price", "")

    @property
    def quantity(self) -> str:
        return self.details.get("quantity", "")

    @property
    def strategy_type(self) -> str:
        return self.details.get("strategyType", "")


@dataclass
class Position:
    """Tracks an open position."""
    market_id: int
    outcome: str
    side: str
    entry_price: float
    quantity: float
    value_usdt: float
    opened_at: float = field(default_factory=time.time)
    order_hash: str = ""


@dataclass
class OrderRequest:
    """Represents an order to be submitted to the API."""
    market_id: int
    side: Side
    strategy: OrderStrategy
    token_id: str          # outcome on-chain ID
    maker: str
    signer: str
    maker_amount: str      # in wei
    taker_amount: str     # in wei
    price_per_share: str
    fee_rate_bps: int = 0
    nonce: int = 0
    expiration: int = 0
    slippage_bps: int = 0
    signature: str = ""
    hash: str = ""
    salt: str = ""
    signature_type: int = 0


@dataclass
class TradeSignal:
    """A signal generated by the strategy engine."""
    market_id: int
    outcome: str
    side: str
    suggested_price: float
    fair_value: float
    market_price: float
    edge: float
    confidence: float
    size_alloc: float = 1.0
    paired_with: Optional[int] = None
    timestamp: float = field(default_factory=time.time)
