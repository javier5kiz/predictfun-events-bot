# Predict.fun Events Contrast Bot

A Python trading bot for [Predict.fun](https://predict.fun) prediction markets that uses **Binance spot prices** as a reference oracle to find mispriced markets and trade them via the official Predict.fun WebSocket + REST API.

## How It Works

```
Binance Spot Price  ──▶  Fair Value Oracle  ──▶  Compare to Market Price
                                                        │
                                                        ▼
                                              Signal Generated (edge > threshold)
                                                        │
                                                        ▼
                                              Risk Check ──▶ Execute Order (if approved)
```

The bot monitors crypto-related prediction markets (e.g., *"Will BTC be above $100,000?"*) and compares the market's implied probability (from the orderbook mid-price) against a fair-value probability derived from the current Binance spot price. When the gap ("edge") exceeds a configurable threshold, it generates a trade signal and submits a signed LIMIT order to Predict.fun.

## Features

- **WebSocket Realtime Feed** — Subscribes to `predictOrderbook`, `predictMarketStatus`, and `predictTradingStatus` topics for live market data
- **Binance Spot Oracle** — Fetches real-time spot prices to derive fair-value probabilities
- **EIP-712 Order Signing** — Signs orders using your wallet's private key
- **Risk Management** — Position limits, daily loss limits, per-trade stop-loss and take-profit
- **Auto-Reconnect** — Exponential backoff reconnection with automatic resubscription
- **Docker Support** — Runs in Docker with `docker-compose`
- **Dry-Run Mode** — Test the strategy without submitting real orders

## Project Structure

```
Predict.fun-bot/
├── bot.py              # Main entry point — orchestrates all components
├── config.py           # Configuration (env vars, trading parameters)
├── auth.py             # API key auth, JWT retrieval, EIP-712 order signing
├── predict_client.py   # REST API client (markets, orders, positions)
├── websocket_client.py # WebSocket client (heartbeat, subscriptions, reconnect)
├── market_scanner.py   # Market discovery + live data cache
├── binance_oracle.py   # Binance spot price fetcher + fair value derivation
├── strategy.py         # Contrast strategy (edge detection, signal generation)
├── trader.py           # Order execution (build, sign, submit)
├── risk.py             # Risk management (position limits, SL/TP, daily P&L)
├── logger.py           # Structured logging with file rotation
├── models.py           # Typed data models (Market, Order, Orderbook, etc.)
├── requirements.txt    # Python dependencies
├── Dockerfile          # Docker image definition
├── docker-compose.yml  # Docker Compose configuration
├── .env.example        # Example environment configuration
└── README.md           # This file
```

## Quick Start

### 1. Prerequisites

- Python 3.10+
- A BNB Chain wallet with USDT for trading
- (Mainnet) A Predict.fun API key — get one from their [Discord](https://discord.gg/predictdotfun)
- (Testnet) No API key required

### 2. Local Setup

```bash
# Clone the repo
git clone https://github.com/javier5kiz/Predict.fun-bot.git
cd Predict.fun-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your wallet private key, API key, and trading parameters
```

### 3. Run

```bash
# Dry run (no real orders — just scan and log signals)
python bot.py --dry-run

# Scan only (find opportunities without trading)
python bot.py --scan-only

# Live trading
python bot.py
```

### 4. Docker

```bash
cp .env.example .env
# Edit .env

docker-compose up -d
docker-compose logs -f
```

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `PREDICT_API_URL` | `https://api-testnet.predict.fun` | REST API base URL |
| `PREDICT_WS_URL` | `wss://ws.predict.fun/ws` | WebSocket endpoint |
| `PREDICT_API_KEY` | (empty) | API key (required for mainnet) |
| `WALLET_PRIVATE_KEY` | (empty) | EOA wallet private key |
| `MIN_EDGE` | `0.02` | Minimum edge to trigger a trade (2%) |
| `MAX_POSITION_USDT` | `100` | Maximum position size in USDT |
| `MAX_CONCURRENT_POSITIONS` | `5` | Max simultaneous open positions |
| `DAILY_LOSS_LIMIT_USDT` | `50` | Daily loss limit (halts trading) |
| `PER_TRADE_STOP_LOSS` | `0.15` | Stop loss (15% below entry) |
| `PER_TRADE_TAKE_PROFIT` | `0.30` | Take profit (30% above entry) |
| `SCAN_INTERVAL_SECONDS` | `60` | Market rescan interval |

## WebSocket Topics Used

| Topic | Description |
|---|---|
| `predictOrderbook/{marketId}` | Live orderbook (bids, asks) |
| `predictMarketStatus/{marketId}` | Market lifecycle + outcome prices |
| `predictTradingStatus/{marketId}` | Whether the market is matching orders |
| `predictWalletEvents/{jwt}` | Live events for your own orders |

## Disclaimer

This bot is for educational purposes. Always test on testnet first. Never trade with money you can't afford to lose.

## License

MIT
