# Fly.io Deployment Issue Report

## Issue Summary

**Status:** BLOCKED  
**Error:** 401 Unauthorized from Fly.io internal registry  
**Date:** October 18, 2025  
**App:** hyperliquid-alerts

## Error Details

```
ERROR: failed to push registry.fly.io/hyperliquid-alerts:deployment-XXXXX: 
unexpected status from HEAD request to http://_api.internal:5000/v2/hyperliquid-alerts/blobs/sha256:XXXXX?ns=registry.fly.io: 
401 Unauthorized
```

## What We've Tried

1. ✅ Re-authenticated: `flyctl auth login` (successful)
2. ✅ Restarted agent: `flyctl agent restart`
3. ✅ Destroyed and recreated app (fresh credentials)
4. ✅ Created fresh volume
5. ✅ Set secrets successfully
6. ❌ Deploy fails every time at image push stage

## Technical Context

- **Build completes successfully** (all layers cached)
- **Failure occurs during push** to Fly.io's internal registry
- **Error is consistent** across multiple deploy attempts
- **Fresh auth tokens** don't resolve the issue
- **Internal API** (`_api.internal:5000`) returns 401

## Current Configuration

### App Details
- **Name:** hyperliquid-alerts
- **Region:** lax (Los Angeles)
- **Organization:** personal (Taylor Mohney)
- **Volume:** alertvol (1GB, zone c120)

### Secrets Set
- ✅ WEBHOOK_URL (Slack)
- ✅ WATCH_ADDRESSES
- ✅ VIP_ADDRESSES

### Build Method
- Using: Depot builder (default)
- Dockerfile: Python 3.11-slim base

## Next Steps

### Option 1: Report to Fly.io Support

Submit a support ticket with:
- Error message above
- App name: `hyperliquid-alerts`
- Account: `taylormohneytech@gmail.com`
- Request: Investigation of internal registry 401 errors

**Support channels:**
- Community: https://community.fly.io
- Email: support@fly.io
- Discord: https://fly.io/discord

### Option 2: Alternative Deployment Platforms

If Fly.io can't be resolved quickly, consider:

#### Railway.app
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Deploy
railway up
```

#### Render.com
- Web UI deployment
- Connect GitHub repo
- Auto-deploy from Dockerfile

#### DigitalOcean App Platform
- Web UI deployment
- Similar pricing to Fly.io
- Direct Docker support

### Option 3: Self-Hosted

Run on a VPS:
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Build and run
cd /path/to/captainahab
docker build -t hyperliquid-alerts .
docker run -d \
  -e WEBHOOK_URL="https://hooks.slack.com/services/..." \
  -e WATCH_ADDRESSES="0xb317..." \
  -e VIP_ADDRESSES="0xb317..." \
  -p 8080:8080 \
  --restart unless-stopped \
  hyperliquid-alerts
```

## Application Status

The application itself is **100% ready**:
- ✅ Code tested and working
- ✅ API integration validated (2000+ fills retrieved)
- ✅ Slack webhook configured
- ✅ Dockerfile builds successfully
- ✅ All dependencies resolved

**Only blocker:** Fly.io infrastructure issue

## Workaround: Local Testing

While waiting for Fly.io fix, you can test locally:

```bash
cd /Users/taylormohney/Documents/GitHub/captainahab

# Set environment variables
export WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
export WATCH_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
export VIP_ADDRESSES="0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae"
export DB_PATH="./local_seen.db"

# Install dependencies (in venv)
source venv/bin/activate
pip install -r requirements.txt

# Run
uvicorn app.main:app --reload
```

Then keep it running in a terminal. You'll get alerts in Slack when the VIP wallet trades!

## Contact Fly.io

If you want to report this yourself:

**Community Forum Post Template:**
```
Subject: 401 Unauthorized from internal registry during deployment

I'm getting persistent 401 errors when deploying to Fly.io:

Error: failed to push registry.fly.io/hyperliquid-alerts:deployment-XXXXX: 
unexpected status from HEAD request to http://_api.internal:5000/v2/hyperliquid-alerts/blobs/...

- Fresh authentication: ✅
- Agent restart: ✅
- Recreated app: ✅
- Multiple deploy attempts: All fail at push stage

Build completes successfully, but push to internal registry fails.

App: hyperliquid-alerts
Account: taylormohneytech@gmail.com
Region: lax

Any known issues with the internal registry service?
```

---

**Updated:** October 18, 2025 23:55 UTC  
**Status:** Waiting for Fly.io infrastructure fix

