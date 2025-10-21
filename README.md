```
                                         .--.
                                        ( (`
                                     .-'   `-.
                                  .-'         `-.
                               .-'               `-.
                            .-'      CAPTAIN        `-.
                         .-'          AHAB             `-.
                      .-'        ___________               `-.
                   .-'      _.-'             `-.               `-.
                .-'     _.-'                     `-.               `-.
             .-'    _.-'                             `-.               `-.
          .-'   _.-'                                     `-.               `-.
       .-'  _.-'          🐋 WHALE WATCHER 🐋                `-.               `-.
    .-'_.-'                                                      `-._______________`-.
        ~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~^~
```

# Captain Ahab: Hyperliquid Whale Watcher

*"Call me Captain. Some years ago—never mind how long precisely—having little or no bitcoin in my wallet, and nothing particular to interest me on mainnet, I thought I would sail about a little and watch the whales..."*

**Thar she blows!** 🐋 Dead-simple monitoring service that hunts Hyperliquid whales, tracking large trades and deposits, and harpooning real-time alerts to your Slack or Discord.

## ⚓ Features

- **🐋 White Whale Monitoring**: Track suspected insider addresses (the elusive white whales) with alerts on ANY activity
- **🎣 Pod Detection**: Automatically hunt coordinated whale pods (insider trading clusters) in real-time
- **⚓ Self-Expanding Fleet**: When whale pods detected, all members added to perpetual watch
- **🎯 Large Position Tracking**: Harpoon alerts on shorts ≥ $25M and deposits ≥ $20M
- **🌊 Multi-Source Data**: Sails the official Hyperliquid API (with HypurrScan support ready)
- **📜 Ship's Log**: SQLite-based deduplication and historical cluster tracking
- **📯 Webhook Alerts**: Sound the alarm to Slack or Discord with Captain Ahab's voice
- **🔭 Status Reports**: Captain's log updates every 2 hours on the state of the hunt
- **🗺️ Historical Research**: Tools to investigate past whale sightings and insider incidents

## 🚢 Setting Sail (Quick Start)

### 1) Provision the vessel (Install flyctl)

```bash
# macOS
brew install flyctl

# Linux
curl -L https://fly.io/install.sh | sh

# Windows
iwr https://fly.io/install.ps1 -useb | iex
```

[Full installation guide](https://fly.io/docs/hands-on/install-flyctl/)

### 2) Board the ship (Login to Fly.io)

```bash
flyctl auth login
```

### 3) Outfit the Pequod (Create & configure app)

```bash
# Initialize app (don't deploy yet)
flyctl launch --no-deploy --copy-config

# Create persistent volume for SQLite database
flyctl volumes create alertvol --size 1 --region lax
```

### 4) Mark the charts (Set secrets/environment variables)

**Required:**

```bash
# Wallet addresses to monitor (comma-separated)
flyctl secrets set WATCH_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"

# VIP addresses - ANY activity triggers alerts
flyctl secrets set VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"

# Slack webhook
flyctl secrets set WEBHOOK_URL="https://hooks.slack.com/services/XXX/YYY/ZZZ"

# Or Discord webhook:
# flyctl secrets set WEBHOOK_URL="https://discord.com/api/webhooks/..." WEBHOOK_TARGET="discord"
```

**Optional thresholds:**

```bash
flyctl secrets set USD_SHORT_THRESHOLD="25000000" USD_DEPOSIT_THRESHOLD="20000000"
```

### 5) Launch the hunt (Deploy)

```bash
flyctl deploy
```

### 6) Check the crow's nest (Verify deployment)

```bash
# Check logs
flyctl logs -a hyperliquid-alerts

# Health check
curl https://hyperliquid-alerts.fly.dev/health
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `WATCH_ADDRESSES` | Comma-separated wallet addresses to monitor | Required |
| `VIP_ADDRESSES` | VIP wallets - ANY activity triggers alerts | Optional |
| `WEBHOOK_URL` | Slack or Discord webhook URL | Required |
| `WEBHOOK_TARGET` | `slack` or `discord` | `slack` |
| `USD_SHORT_THRESHOLD` | Minimum USD for short position alerts | `25000000` |
| `USD_DEPOSIT_THRESHOLD` | Minimum USD for deposit alerts | `20000000` |
| `POLL_SECONDS` | Polling interval in seconds | `30` |
| `LOOKBACK_MINUTES` | Initial lookback window | `10` |
| `HYPERLIQUID_API` | Hyperliquid API endpoint | `https://api.hyperliquid.xyz/info` |
| `DB_PATH` | SQLite database path | `/data/seen.db` |
| **Cluster Detection** | | |
| `CLUSTER_DETECTION_ENABLED` | Enable pod hunting (cluster detection) | `true` |
| `CLUSTER_TIME_WINDOW_MINUTES` | Time window for detecting coordinated activity | `60` |
| `CLUSTER_MIN_SCORE` | Minimum suspicion score to alert (0-100) | `70` |
| `CLUSTER_MIN_NOTIONAL` | Minimum total USD for cluster alert | `50000000` |
| `MARKET_SCAN_TOKENS` | Tokens to monitor for clusters (comma-separated) | `BTC,ETH` |
| `MARKET_MIN_TRADE_SIZE` | Minimum trade size to track for clusters | `5000000` |
| `ENABLE_MARKET_SCANNING` | Enable market-wide whale discovery | `true` |
| `MARKET_SCAN_INTERVAL_SECONDS` | How often to scan for new whales | `300` |
| `VIP_LOOKBACK_HOURS` | Lookback window for VIP wallets | `48` |

### 🐋 White Whale Monitoring (VIP Addresses)

The white whales—those most elusive and dangerous of creatures—receive special treatment:
- 🚨 Alerts on **ANY** trade activity (no threshold)
- 🚨 Alerts on **ANY** deposit/withdrawal (no threshold)
- Distinct alert formatting with VIP indicator

**Known White Whales** (suspected insider trading):
- `0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae` (the great white whale—confirmed)
- `0x9eec9...` (spotted in the distance—needs research)
- `0x9263...` (glimpsed beneath the waves—needs research)

*These leviathans allegedly took large short positions before Trump's tariff tweet, earning their place in our hunting log.*

To mark additional whales for the hunt:

```bash
flyctl secrets set VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae,0x9eec9...,0x9263..."
```

### 🎣 Pod Detection (Cluster Monitoring)

**"A pod! A pod of leviathans, hunting in coordinated fashion!"**

Captain Ahab now automatically detects **suspicious whale pods** — coordinated trading patterns that suggest insider knowledge:

**What Triggers a Cluster Alert:**
- 3+ large trades (>$5M each) within 60 minutes
- 2+ different wallets coordinating
- Total notional ≥ $50M
- 80%+ directional alignment (all SHORT or all LONG)
- Suspicion score ≥ 70/100

**Advanced Pattern Detection (NEW):**
- 📏 **Size Clustering**: Trades with similar notional values (CV < 30%)
- 🔗 **Cross-Token Coordination**: Same wallets trading multiple tokens simultaneously
- ⚡ **Lightning Clusters**: All trades within 60 seconds
- 🆕 **New Wallet Bonus**: Recently created wallets get higher suspicion scores
- 📊 **Dynamic Thresholds**: Adapts to market conditions using percentile analysis

**Suspicion Scoring (0-100):**
- Timing tightness: 0-30 pts (tighter = higher score)
- Notional size: 0-20 pts (larger = higher score)
- Wallet count: 0-15 pts (more wallets = higher score)
- Wallet age: 0-10 pts (newer = higher score)
- Directional alignment: 0-10 pts (95%+ same direction)
- **NEW** Size clustering: 0-15 pts (similar trade sizes)
- **NEW** Cross-token: 0-10 pts (multi-token coordination)
- **NEW** Timing precision: 0-10 pts (all within 60s)

**When Detected:**
1. 🚨 Instant Slack alert with full details
2. 💾 Cluster logged to database
3. ⚓ **ALL cluster wallets auto-added to VIP list**
4. 🔭 Future monitoring: Every move = instant alert

**Example Enhanced Cluster Alert:**
```
⚠️ SUSPICIOUS CLUSTER DETECTED ⚠️
"Thar she blows! A pod of whales hunting in formation!" 🐋

🔴 Suspicion Score: 92/100

📊 Cluster Details:
• Wallets: 4
• Token: BTC
• Total notional: $147,500,000
• Time window: 3.2 minutes
• Direction: SHORT
• Alignment: 100%

🎯 Pattern Indicators:
• 📏 Size clustering: 87% similar
• 🔗 Cross-token: 2 tokens
• ⚡ Lightning fast: <60s

⏰ Timeline:
• First trade: 2025-10-14T13:05:12Z
• Last trade: 2025-10-14T13:08:24Z

🐋 Wallets:
• 0xabcd...ef12 ($45.2M)
• 0x1234...5678 ($38.7M)
• 0x9abc...def0 ($32.8M)
• 0xfedc...ba98 ($30.8M)

🚨 ACTION: Adding all wallets to VIP watch list
```

**The beauty:** Your surveillance net **grows automatically** as suspicious activity detected!

### 🗺️ Historical Research

**Investigate past insider incidents:**

```bash
# Run the Oct 14, 2025 tariff incident research
cd /Users/taylormohney/Documents/GitHub/captainahab
source venv/bin/activate
python research/find_insider_cluster.py
```

This queries Hyperliquid for suspicious trading patterns before major news events.

See `research/INSIDER_WALLETS.md` for details on the Trump tariff investigation.

## Data Sources

### Official Hyperliquid API

Currently using `https://api.hyperliquid.xyz/info` (POST) with:

- **`userFills`**: Perpetual trades and fills
  - Detects short positions (sell side)
  - Calculates notional USD values
  
- **`userNonFundingLedgerUpdates`**: Deposits and withdrawals
  - Tracks USDC/USDT movements
  - Monitors deposit amounts

### HypurrScan Integration (Future)

The codebase includes placeholder support for HypurrScan endpoints. To integrate:

1. Obtain API documentation from [HypurrScan](https://hypurrscan.io)
2. Update `fetch_perps()` and `fetch_transfers()` in `app/main.py`
3. Map response fields to expected schema
4. Redeploy: `flyctl deploy`

**Expected schema:**
- `type`: Action type (e.g., "Open Short", "Deposit")
- `token`: Asset symbol (e.g., "BTC-USD", "USDC")
- `amount`: Position size or transfer amount
- `px`: Price (for trades)
- `usdamount`: USD value
- `hash`: Transaction hash
- `time`: Timestamp in milliseconds

## Alert Types

### Large Deposit
```
💰 Large Deposit
• Token: USDC
• USD: $80,000,000
• Time (UTC): 2025-01-20T14:23:45Z
• Tx: 0xabc123...
```

### Large Short Open
```
🚨 Very Large Short OPEN
• BTC-USD size: 5000 @ $92,500
• Notional: $462,500,000
• Time (UTC): 2025-01-20T14:25:12Z
• Tx: 0xdef456...
```

### VIP Activity
```
🚨 VIP TRADE
• Type: SELL SHORT_OPEN
• Token: ETH-USD
• Amount: 10000 @ $3,200
• Notional: $32,000,000
• Time (UTC): 2025-01-20T14:30:00Z
• Tx: 0x789abc...
```

## Monitoring & Maintenance

### View logs

```bash
flyctl logs -a hyperliquid-alerts
```

### Update configuration

```bash
# Update secrets
flyctl secrets set POLL_SECONDS="60"

# Redeploy
flyctl deploy
```

### Scale resources

```bash
# List machines
flyctl machines list

# Scale volume
flyctl volumes extend alertvol --size 2
```

## Troubleshooting

### No alerts appearing

1. Check logs: `flyctl logs -a hyperliquid-alerts`
2. Verify webhook URL is correct
3. Test webhook manually with curl
4. Ensure addresses are lowercase in configuration

### API errors

- Check Hyperliquid API status
- Verify rate limits aren't exceeded
- Confirm address format is correct

### Database issues

- Volume must be mounted at `/data`
- Check volume exists: `flyctl volumes list`
- Recreate if needed: `flyctl volumes create alertvol --size 1 --region lax`

## Development

### Local testing

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export WATCH_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
export VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
export WEBHOOK_URL="https://hooks.slack.com/services/..."
export DB_PATH="./local_seen.db"

# Run locally
uvicorn app.main:app --reload
```

### Docker testing

```bash
docker build -t hyperliquid-alerts .
docker run -p 8080:8080 \
  -e WATCH_ADDRESSES="0xb317..." \
  -e VIP_ADDRESSES="0xb317..." \
  -e WEBHOOK_URL="https://hooks.slack.com/..." \
  hyperliquid-alerts
```

## 🛳️ The Pequod's Architecture

```
┌─────────────────┐
│   The Pequod    │  ⚓ (Fly.io + FastAPI)
│  Captain Ahab   │
└────────┬────────┘
         │
    ┌────▼────┐
    │ Lookout │ 👁️ (polls every 30s)
    └────┬────┘
         │
    ┌────▼────────────────┐
    │  The Seven Seas     │ 🌊
    │  (Hyperliquid API)  │
    │  - userFills        │
    │  - userNonFunding   │
    │    LedgerUpdates    │
    └────┬────────────────┘
         │
    ┌────▼────────┐
    │   Spotter   │ 🔭 (White Whale vs regular)
    └────┬────────┘
         │
    ┌────▼────────┐
    │  Ship's Log │ 📖 (SQLite deduplication)
    └────┬────────┘
         │
    ┌────▼────────┐
    │  Harpoons   │ 🎯 (Slack/Discord webhooks)
    └─────────────┘
```

## 📜 License

MIT

## 🌊 Ahoy, Sailor!

*"It is not down on any map; true places never are."*

For issues or questions on your whaling voyage:
- Check [Hyperliquid Docs](https://hyperliquid.gitbook.io/)
- Review [Fly.io Documentation](https://fly.io/docs/)
- Signal from the crow's nest (open an issue in this repository)

---

```
        "From hell's heart I short thee; for hate's sake I spit my last USDC at thee."
                                                        — Captain Ahab (probably)
```
