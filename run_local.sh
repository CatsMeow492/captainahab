#!/bin/bash

# Hyperliquid Alert Service - Local Runner
# Run this while waiting for Fly.io deployment to be fixed

# Set your Slack or Discord webhook URL
export WEBHOOK_URL="${WEBHOOK_URL:-https://hooks.slack.com/services/YOUR/WEBHOOK/HERE}"
export WATCH_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae,0x4f9A37Bc2A4a2861682c0e9BE1F9417Df03CC27C"
export VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae,0x4f9A37Bc2A4a2861682c0e9BE1F9417Df03CC27C"
export WEBHOOK_TARGET="slack"
export DB_PATH="./local_seen.db"
export POLL_SECONDS="30"
export LOOKBACK_MINUTES="10"
export USD_SHORT_THRESHOLD="25000000"
export USD_DEPOSIT_THRESHOLD="20000000"

# Cluster detection
export CLUSTER_DETECTION_ENABLED="true"
export CLUSTER_TIME_WINDOW_MINUTES="60"
export CLUSTER_MIN_SCORE="70"
export CLUSTER_MIN_NOTIONAL="50000000"
export MARKET_SCAN_TOKENS="BTC,ETH"
export MARKET_MIN_TRADE_SIZE="5000000"

echo "🚀 Starting Captain Ahab - Whale Watcher (Local Mode)"
echo "🐋 CLUSTER DETECTION ENABLED"
echo "📊 Monitoring: $WATCH_ADDRESSES"
echo "🚨 VIP: $VIP_ADDRESSES"
echo "📨 Slack webhook configured"
echo "⏱️  Poll interval: ${POLL_SECONDS}s"
echo ""
echo "Press Ctrl+C to stop"
echo "=========================================="
echo ""

# Activate venv and run
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080

