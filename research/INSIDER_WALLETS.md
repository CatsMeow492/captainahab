# Insider Trading Investigation

## October 14, 2025 - Trump Tariff Tweet Incident

### Event Timeline

**13:07 UTC** - Donald Trump tweets about significant tariff increases on China  
**Result:** Major crypto market selloff, particularly BTC/ETH

### Suspicious Activity Pattern

Multiple wallets opened large short positions in the 1-2 hours **before** the tweet, suggesting advance knowledge.

## Identified Wallets

### Confirmed Insiders

| Address | Status | Notes |
|---------|--------|-------|
| `0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae` | ✅ Confirmed | Large shorts before tweet, known suspect |
| `0x9eec9...` | 🔍 Partial | Need to complete address |
| `0x9263...` | 🔍 Partial | Need to complete address |

### Research Tasks

- [ ] Run `research/find_insider_cluster.py` to query historical data
- [ ] Check HypurrScan.io for Oct 14, 11:00-13:07 UTC, filter by large shorts
- [ ] Search Twitter: "hyperliquid insider trump tariff 0x9eec9" and "0x9263"
- [ ] Look for community discussions on Hyperliquid Discord
- [ ] Verify wallet profit/loss after the event (strong indicator)

## Cluster Detection Features

### Real-Time Monitoring

Captain Ahab now automatically detects suspicious trading clusters and:
1. **Alerts immediately** when patterns match known insider behavior
2. **Auto-adds wallets** to VIP watch list (perpetual monitoring)
3. **Scores suspicion** 0-100 based on timing, size, coordination
4. **Persists across restarts** - dynamically discovered VIPs saved to database

### Cluster Alert Triggers

A cluster alert fires when:
- 3+ large trades (>$5M each) within 60 minutes
- 2+ different wallets
- Total notional >$50M
- 80%+ directional alignment (all shorts or all longs)
- Suspicion score ≥70

### Example Cluster Alert

```
⚠️ SUSPICIOUS CLUSTER DETECTED ⚠️
"Thar she blows! A pod of whales hunting in formation!" 🐋

🔴 Suspicion Score: 87/100

📊 Cluster Details:
• Wallets: 4
• Token: BTC
• Total notional: $147,500,000
• Time window: 23.4 minutes
• Direction: SHORT
• Alignment: 100%

⏰ Timeline:
• First trade: 2025-10-14T12:34:22Z
• Last trade: 2025-10-14T12:57:48Z

🎯 Wallets:
• 0xabcd1234...56ef ($ 45.0M)
• 0xef123456...ab89 ($38.0M)
• 0x34567890...cdef ($32.5M)
• 0x789abcde...0123 ($32.0M)

🚨 ACTION: Adding all wallets to VIP watch list
```

## How to Run the Research

### Option 1: Quick Scan (Current Code)

```bash
cd /Users/taylormohney/Documents/GitHub/captainahab

# Make sure venv is activated
source venv/bin/activate

# Install httpx if not already installed
pip install httpx

# Run the research script
python research/find_insider_cluster.py
```

This will:
- Query Hyperliquid for the known suspect addresses
- Filter for large shorts in the 2-hour window before the tweet
- Output CSV and JSON with findings
- Suggest VIP_ADDRESSES to add

### Option 2: Expand the Search

Edit `research/find_insider_cluster.py`:

1. Add more addresses to `KNOWN_LARGE_TRADERS` (known whales)
2. Adjust `MIN_NOTIONAL_USD` threshold (lower to catch smaller positions)
3. Expand time window if needed

### Option 3: Manual HypurrScan Investigation

1. Go to https://hypurrscan.io
2. Navigate to transaction explorer
3. Filter by:
   - Date: October 14, 2025
   - Time: 11:00-13:07 UTC
   - Type: Short positions
   - Size: >$5M notional
4. Export results and cross-reference with research script findings

## What to Do with Findings

Once you identify the full cluster:

### 1. Update VIP Secrets

```bash
flyctl secrets set VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae,0xFULL_ADDRESS_1,0xFULL_ADDRESS_2,..."
```

### 2. Document Here

Update the "Confirmed Insiders" table above with:
- Complete addresses
- Estimated trade sizes
- Timing (how many minutes before tweet)
- Profitability (if data available)

### 3. Share Intelligence

Consider reporting to:
- Hyperliquid team (for platform integrity)
- Crypto Twitter (public awareness)
- Relevant authorities (if applicable)

## Ongoing Detection

Captain Ahab will now:
- ✅ Monitor all discovered insider wallets 24/7
- ✅ Detect new clusters as they form
- ✅ Auto-expand VIP list when suspicious activity detected
- ✅ Alert within minutes of coordinated trading

**The hunt is on!** 🐋⚓

---

**Last Updated:** October 19, 2025  
**Status:** Research in progress  
**Known Insiders:** 1 confirmed, 2 partial addresses  
**Cluster Detection:** ✅ ACTIVE


