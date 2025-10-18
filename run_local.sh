#!/bin/bash

# Hyperliquid Alert Service - Local Runner
# Run this while waiting for Fly.io deployment to be fixed

export WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
export WATCH_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
export VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
export WEBHOOK_TARGET="slack"
export DB_PATH="./local_seen.db"
export POLL_SECONDS="30"
export LOOKBACK_MINUTES="10"
export USD_SHORT_THRESHOLD="25000000"
export USD_DEPOSIT_THRESHOLD="20000000"

echo "🚀 Starting Hyperliquid Alert Service (Local Mode)"
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

