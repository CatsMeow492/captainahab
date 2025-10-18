# Deployment Instructions

## Current Status

✅ **Completed:**
- All project files created and tested
- Hyperliquid API integration working (tested with 2000+ fills, 39 ledger updates)
- Fly.io app created: `hyperliquid-alerts`
- Volume created: `alertvol` (1GB, LAX region)

⚠️ **Blocked:**
- Deployment failing due to Fly.io infrastructure issue (401 Unauthorized from internal registry)
- Error: `unexpected status from HEAD request to http://_api.internal:5000/v2/hyperliquid-alerts/blobs/...`

## Slack Webhook Setup

**Important:** You provided OAuth credentials, but we need an **Incoming Webhook URL** instead.

### How to Create a Slack Incoming Webhook:

1. Go to https://api.slack.com/apps
2. Select your app **"App ID: A09M45MEJ11"** or create a new one
3. In the left sidebar, click **"Incoming Webhooks"**
4. Toggle **"Activate Incoming Webhooks"** to **ON**
5. Click **"Add New Webhook to Workspace"**
6. Select the channel where you want alerts (#trading, #alerts, etc.)
7. Click **"Allow"**
8. Copy the webhook URL (format: `https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXX`)

### Alternative: Simple Webhook App

If you want a simpler approach:

1. Go to https://YOUR_WORKSPACE.slack.com/apps
2. Search for "Incoming Webhooks"
3. Click "Add to Slack"
4. Choose a channel
5. Copy the webhook URL

## Complete Deployment Steps (When Fly.io is Fixed)

### 1. Set the Slack Webhook

```bash
cd /Users/taylormohney/Documents/GitHub/captainahab

# Replace with your ACTUAL webhook URL
flyctl secrets set WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
```

### 2. Configure Watch Addresses

```bash
# Set addresses to monitor
flyctl secrets set WATCH_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae,OTHER_ADDRESSES"

# Set VIP addresses (ANY activity triggers alerts)
flyctl secrets set VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
```

###  3. Try Deploying Again

```bash
# Check if Fly.io issue is resolved
flyctl doctor

# If doctor shows all PASSED, try deploying:
flyctl deploy
```

### 4. If deployment still fails:

**Option A:** Contact Fly.io Support

```bash
# Include this error in your support ticket:
# "401 Unauthorized from http://_api.internal:5000 when pushing to registry"
```

**Option B:** Try alternative deployment method

Create a `.dockerignore` file:
```
venv/
__pycache__/
*.pyc
.git/
.env
*.md
```

Then try:
```bash
flyctl deploy --strategy immediate
```

### 5. Verify Deployment

Once deployment succeeds:

```bash
# Check logs
flyctl logs

# Health check
curl https://hyperliquid-alerts.fly.dev/health

# Monitor in real-time
flyctl logs -f
```

## Testing Alerts

Once deployed, the service will:

1. **Poll every 30 seconds** for activity on watched addresses
2. **VIP addresses** (0xb317...) trigger alerts on ANY activity
3. **Regular addresses** trigger alerts only for:
   - Shorts ≥ $25M notional
   - Deposits ≥ $20M

### Test Alert Format (Slack)

You should see messages like:

```
🚨 VIP WALLET Hyperliquid Alert – 0xb317d2bc...07283ae

🚨 VIP TRADE
• Type: SELL SHORT_OPEN
• Token: BTC-USD  
• Amount: 1000 @ $95,000
• Notional: $95,000,000
• Time (UTC): 2025-10-18T23:45:00Z
• Tx: 0xabc123...
```

## Research: Missing VIP Addresses

You mentioned two other addresses from the Trump tariff incident:
- `0x9eec9...` (partial)
- `0x9263...` (partial)

### How to Find Them:

1. **Check Hyperliquid Explorer:**
   - Go to https://app.hyperliquid.xyz/explorer
   - Look for large short positions opened around the tariff tweet time

2. **Check HypurrScan:**
   - https://hypurrscan.io
   - Filter by large BTC/ETH shorts
   - Look for timing correlation with the news

3. **Twitter/Social Media:**
   - Search for discussion about the insider trading incident
   - Look for addresses mentioned in community discussions

4. **Once found, add them:**
```bash
flyctl secrets set VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae,0x9eec9FULL_ADDRESS,0x9263FULL_ADDRESS"
```

## Configuration Reference

### Environment Variables (already set in fly.toml)

| Variable | Value | Description |
|----------|-------|-------------|
| `POLL_SECONDS` | `30` | How often to check for new activity |
| `LOOKBACK_MINUTES` | `10` | Initial lookback window |
| `USD_SHORT_THRESHOLD` | `25000000` | Minimum USD for regular short alerts |
| `USD_DEPOSIT_THRESHOLD` | `20000000` | Minimum USD for regular deposit alerts |

### Secrets (need to be set)

| Secret | Status | Example |
|--------|--------|---------|
| `WEBHOOK_URL` | ❌ **REQUIRED** | `https://hooks.slack.com/services/...` |
| `WATCH_ADDRESSES` | ⚠️ Placeholder set | `0xb317...,0x9eec9...,0x9263...` |
| `VIP_ADDRESSES` | ⚠️ Partial set | `0xb317...` (add other two) |

## Troubleshooting

### Deployment Issues

If you continue getting 401 errors after Fly.io fixes:

```bash
# Re-authenticate
flyctl auth login

# Restart agent
flyctl agent restart

# Try with fresh app
flyctl apps destroy hyperliquid-alerts --yes
flyctl launch --no-deploy --copy-config --yes
flyctl volumes create alertvol --size 1 --region lax --yes
flyctl secrets set WEBHOOK_URL="..." WATCH_ADDRESSES="..." VIP_ADDRESSES="..."
flyctl deploy
```

### No Alerts Appearing

1. Check logs for API errors:
```bash
flyctl logs | grep ERROR
```

2. Verify addresses are correct (lowercase):
```bash
flyctl secrets list
```

3. Test webhook manually:
```bash
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"Test alert from Hyperliquid monitor"}' \
  YOUR_WEBHOOK_URL
```

4. Check the wallet actually has activity:
   - https://app.hyperliquid.xyz/explorer

### High Memory Usage

If app keeps restarting:

```bash
# Scale up memory
flyctl scale memory 512

# Or in fly.toml, change:
[[vm]]
  memory = '512mb'  # or '1gb'
```

## Next Steps (In Order)

1. ✅ ~~Create project files~~ **DONE**
2. ✅ ~~Test Hyperliquid API~~ **DONE** 
3. ✅ ~~Create Fly.io app and volume~~ **DONE**
4. ⏳ **Wait for Fly.io infrastructure fix**
5. 🔲 Get Slack Incoming Webhook URL
6. 🔲 Research complete addresses for 0x9eec9... and 0x9263...
7. 🔲 Set secrets (WEBHOOK_URL, addresses)
8. 🔲 Deploy successfully
9. 🔲 Monitor logs and verify alerts

## Support

- **Fly.io Status:** https://status.fly.io
- **Fly.io Community:** https://community.fly.io
- **Hyperliquid Discord:** https://discord.gg/hyperliquid

---

**Last Updated:** October 18, 2025  
**Deployment Status:** Blocked by Fly.io infrastructure issue  
**Ready to Deploy:** Yes (pending Fly.io fix)

