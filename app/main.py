import os
import json
import asyncio
import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

# -------------------------
# Config via env vars
# -------------------------
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", "10"))

WATCH_ADDRESSES = [a.strip() for a in os.getenv("WATCH_ADDRESSES","").split(",") if a.strip()]
VIP_ADDRESSES = [a.strip().lower() for a in os.getenv("VIP_ADDRESSES","").split(",") if a.strip()]
USD_SHORT_THRESHOLD = float(os.getenv("USD_SHORT_THRESHOLD", "25000000"))
USD_DEPOSIT_THRESHOLD = float(os.getenv("USD_DEPOSIT_THRESHOLD", "20000000"))

# Cluster detection config
CLUSTER_DETECTION_ENABLED = os.getenv("CLUSTER_DETECTION_ENABLED", "true").lower() == "true"
CLUSTER_TIME_WINDOW_MINUTES = int(os.getenv("CLUSTER_TIME_WINDOW_MINUTES", "60"))
CLUSTER_MIN_SCORE = int(os.getenv("CLUSTER_MIN_SCORE", "70"))
CLUSTER_MIN_NOTIONAL = float(os.getenv("CLUSTER_MIN_NOTIONAL", "50000000"))
MARKET_SCAN_TOKENS = [t.strip() for t in os.getenv("MARKET_SCAN_TOKENS", "BTC,ETH").split(",") if t.strip()]
MARKET_MIN_TRADE_SIZE = float(os.getenv("MARKET_MIN_TRADE_SIZE", "5000000"))

WEBHOOK_URL = os.getenv("WEBHOOK_URL","")  # Slack or Discord
WEBHOOK_TARGET = os.getenv("WEBHOOK_TARGET","slack").lower()  # "slack" or "discord"

# Hyperliquid API (official)
HYPERLIQUID_API = os.getenv("HYPERLIQUID_API", "https://api.hyperliquid.xyz/info")

# HypurrScan base (optional, for when endpoints become available)
HYPURR_BASE = os.getenv("HYPURR_BASE", "https://hypurrscan.io")

# SQLite for de-duplication
DB_PATH = os.getenv("DB_PATH", "/data/seen.db")

app = FastAPI()

# -------------------------
# Stats tracking
# -------------------------
stats = {
    "start_time": None,
    "scans_completed": 0,
    "api_calls_successful": 0,
    "api_calls_failed": 0,
    "alerts_sent": 0,
    "last_hyperliquid_check": None,
    "hyperliquid_status": "unknown",
    "clusters_detected": 0,
    "wallets_added_to_vip": 0,
    "market_scans_completed": 0
}

# -------------------------
# Utilities
# -------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat()

def sha_key(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode())
        h.update(b"|")
    return h.hexdigest()

def is_vip(address: str) -> bool:
    """Check if an address is a VIP address (insider watch)"""
    return address.lower() in VIP_ADDRESSES

# -------------------------
# Tiny state store (SQLite)
# -------------------------
def ensure_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS seen(
            digest TEXT PRIMARY KEY,
            ts INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS cursors(
            source TEXT PRIMARY KEY,
            last_ms INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS trading_baselines(
            token TEXT,
            hour_utc INTEGER,
            median_short_size REAL,
            median_short_count INTEGER,
            unique_traders INTEGER,
            updated_at INTEGER,
            PRIMARY KEY (token, hour_utc)
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS market_trades(
            trade_id TEXT PRIMARY KEY,
            wallet TEXT,
            token TEXT,
            side TEXT,
            notional REAL,
            timestamp_ms INTEGER,
            wallet_age_days INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS suspicious_clusters(
            cluster_id TEXT PRIMARY KEY,
            wallets TEXT,
            token TEXT,
            total_notional REAL,
            trade_count INTEGER,
            time_window_minutes REAL,
            suspicion_score INTEGER,
            first_trade_ms INTEGER,
            last_trade_ms INTEGER,
            news_event TEXT,
            created_at INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS vip_wallets(
            address TEXT PRIMARY KEY,
            reason TEXT,
            added_at INTEGER
        )""")
        con.commit()

def mark_seen(digest: str):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("INSERT OR IGNORE INTO seen(digest, ts) VALUES(?, ?)",
                    (digest, int(now_utc().timestamp())))
        con.commit()

def is_seen(digest: str) -> bool:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT 1 FROM seen WHERE digest=?", (digest,))
        return cur.fetchone() is not None

def get_cursor(source: str, fallback_ms: int) -> int:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT last_ms FROM cursors WHERE source=?", (source,))
        row = cur.fetchone()
        if row and row[0]:
            return int(row[0])
        else:
            con.execute("INSERT OR REPLACE INTO cursors(source,last_ms) VALUES(?,?)",
                        (source, fallback_ms))
            con.commit()
            return fallback_ms

def set_cursor(source: str, ms: int):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("INSERT OR REPLACE INTO cursors(source,last_ms) VALUES(?,?)",
                    (source, ms))
        con.commit()

# -------------------------
# Hyperliquid API fetchers
# -------------------------
async def http_post_json(url: str, payload: dict) -> Any:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json=payload, headers={"User-Agent":"fly-hl-bot/1.0"})
        r.raise_for_status()
        return r.json()

async def fetch_perps(address: str, since_ms: int) -> List[Dict[str, Any]]:
    """
    Fetch perpetual fills/trades for an address using official Hyperliquid API.
    Uses userFills endpoint to get trade history.
    """
    try:
        payload = {
            "type": "userFills",
            "user": address
        }
        data = await http_post_json(HYPERLIQUID_API, payload)
        stats["api_calls_successful"] += 1
        stats["last_hyperliquid_check"] = now_utc()
        stats["hyperliquid_status"] = "healthy"
        
        # Filter by timestamp - userFills returns chronological fills
        # Structure: list of fills with {coin, px, sz, side, time, ...}
        filtered = []
        if isinstance(data, list):
            for fill in data:
                fill_time_ms = int(fill.get("time", 0))
                if fill_time_ms >= since_ms:
                    # Normalize the data structure
                    side = fill.get("side", "").lower()
                    coin = fill.get("coin", "")
                    size = abs(float(fill.get("sz", 0)))
                    px = float(fill.get("px", 0))
                    notional = size * px
                    
                    # Determine if it's a short open (sell side)
                    is_short = side in ["sell", "short"]
                    
                    filtered.append({
                        "type": "short_open" if is_short else "long_open",
                        "token": coin,
                        "amount": size,
                        "px": px,
                        "usdamount": notional,
                        "notional": notional,
                        "hash": fill.get("tid", ""),  # trade ID
                        "time": fill_time_ms,
                        "side": side,
                        "oid": fill.get("oid", "")
                    })
        
        return filtered
    except Exception as e:
        stats["api_calls_failed"] += 1
        stats["hyperliquid_status"] = "error"
        print(f"[ERROR] fetch_perps for {address}: {e}")
        
        # Send alert if error rate is high
        total_calls = stats["api_calls_successful"] + stats["api_calls_failed"]
        if total_calls > 10 and stats["api_calls_failed"] / total_calls > 0.3:
            success_rate = f"{(stats['api_calls_successful'] / total_calls * 100):.1f}%"
            await send_status_message("api_error", {"error": str(e), "success_rate": success_rate})
        
        return []

async def fetch_transfers(address: str, since_ms: int) -> List[Dict[str, Any]]:
    """
    Fetch deposits/withdrawals for an address using official Hyperliquid API.
    Uses userNonFundingLedgerUpdates endpoint.
    """
    try:
        payload = {
            "type": "userNonFundingLedgerUpdates",
            "user": address
        }
        data = await http_post_json(HYPERLIQUID_API, payload)
        stats["api_calls_successful"] += 1
        stats["last_hyperliquid_check"] = now_utc()
        stats["hyperliquid_status"] = "healthy"
        
        # Filter by timestamp and type
        # Structure: list of ledger updates with {time, hash, delta: {type, usdc, ...}}
        filtered = []
        if isinstance(data, list):
            for update in data:
                update_time_ms = int(update.get("time", 0))
                if update_time_ms >= since_ms:
                    delta = update.get("delta", {})
                    
                    # Handle different delta types
                    if isinstance(delta, dict):
                        delta_type = delta.get("type", "")
                        
                        # Deposits and withdrawals
                        if delta_type in ["deposit", "withdraw", "internalTransfer"]:
                            usdc_amount = abs(float(delta.get("usdc", 0)))
                            
                            filtered.append({
                                "type": delta_type.capitalize(),
                                "token": "USDC",
                                "usdamount": usdc_amount,
                                "hash": update.get("hash", ""),
                                "time": update_time_ms
                            })
        
        return filtered
    except Exception as e:
        stats["api_calls_failed"] += 1
        stats["hyperliquid_status"] = "error"
        print(f"[ERROR] fetch_transfers for {address}: {e}")
        
        # Send alert if error rate is high
        total_calls = stats["api_calls_successful"] + stats["api_calls_failed"]
        if total_calls > 10 and stats["api_calls_failed"] / total_calls > 0.3:
            success_rate = f"{(stats['api_calls_successful'] / total_calls * 100):.1f}%"
            await send_status_message("api_error", {"error": str(e), "success_rate": success_rate})
        
        return []

# -------------------------
# Cluster Detection
# -------------------------
def calculate_suspicion_score(cluster_data: Dict[str, Any]) -> int:
    """
    Score 0-100 for insider trading likelihood
    """
    score = 0
    
    # Timing tightness (0-30 pts)
    time_span = cluster_data.get('time_span', 60)
    if time_span < 5:
        score += 30
    elif time_span < 15:
        score += 20
    elif time_span < 30:
        score += 10
        
    # Notional size (0-25 pts)
    total_notional = cluster_data.get('total_notional', 0)
    notional_100m = total_notional / 100_000_000
    score += min(25, int(notional_100m * 10))
    
    # Wallet count (0-20 pts)
    wallet_count = cluster_data.get('wallet_count', 0)
    score += min(20, wallet_count * 5)
    
    # Wallet age - newer is more suspicious (0-15 pts)
    avg_wallet_age = cluster_data.get('avg_wallet_age', 30)
    if avg_wallet_age < 3:
        score += 15
    elif avg_wallet_age < 7:
        score += 10
    elif avg_wallet_age < 14:
        score += 5
        
    # Directional alignment (0-10 pts)
    alignment = cluster_data.get('alignment', 0.5)
    if alignment > 0.8:
        score += int((alignment - 0.8) * 50)
    
    return min(100, score)

def detect_trading_cluster(trades: List[Dict[str, Any]], window_minutes: int = None) -> Optional[Dict[str, Any]]:
    """
    Detect coordinated trading patterns
    
    Returns cluster if:
    - 3+ trades within window
    - 2+ unique wallets
    - Total notional > threshold
    - 80%+ directional alignment
    """
    if not trades or len(trades) < 3:
        return None
    
    if window_minutes is None:
        window_minutes = CLUSTER_TIME_WINDOW_MINUTES
        
    timestamps = [int(t.get('timestamp_ms', t.get('time', 0))) for t in trades]
    wallets = list(set(t.get('wallet', t.get('address', '')) for t in trades))
    
    if len(wallets) < 2:
        return None
        
    # Calculate time span in minutes
    time_span = (max(timestamps) - min(timestamps)) / (1000 * 60)
    if time_span > window_minutes:
        return None
        
    # Calculate total notional
    total_notional = sum(float(t.get('notional', 0)) for t in trades)
    if total_notional < CLUSTER_MIN_NOTIONAL:
        return None
        
    # Calculate directional alignment
    sides = [t.get('side', '').lower() for t in trades]
    sell_count = sum(1 for s in sides if s in ['sell', 'short'])
    buy_count = sum(1 for s in sides if s in ['buy', 'long'])
    alignment = max(sell_count, buy_count) / len(sides) if sides else 0.5
    
    if alignment < 0.8:
        return None
        
    # Determine dominant direction
    direction = "SHORT" if sell_count > buy_count else "LONG"
    
    # Get token (most common in cluster)
    tokens = [t.get('token', '') for t in trades if t.get('token')]
    token = max(set(tokens), key=tokens.count) if tokens else "Multiple"
    
    # Calculate average wallet age (use placeholder for now)
    avg_wallet_age = 30  # TODO: implement wallet age lookup
    
    # Calculate suspicion score
    cluster_data = {
        'wallet_count': len(wallets),
        'total_notional': total_notional,
        'time_span': time_span,
        'alignment': alignment,
        'avg_wallet_age': avg_wallet_age
    }
    score = calculate_suspicion_score(cluster_data)
    
    if score < CLUSTER_MIN_SCORE:
        return None
        
    # Create cluster ID
    cluster_id = sha_key(
        str(min(timestamps)),
        str(len(wallets)),
        token,
        direction
    )
    
    return {
        'cluster_id': cluster_id,
        'wallets': wallets,
        'trades': trades,
        'token': token,
        'direction': direction,
        'score': score,
        'total_notional': total_notional,
        'time_window': time_span,
        'first_trade_ms': min(timestamps),
        'last_trade_ms': max(timestamps),
        'trade_count': len(trades),
        'alignment': alignment
    }

async def add_wallets_to_vip(wallets: List[str], reason: str):
    """
    Dynamically add wallets to VIP monitoring
    Store in database, update in-memory list
    """
    new_wallets = []
    
    with sqlite3.connect(DB_PATH) as con:
        for wallet in wallets:
            wallet_lower = wallet.lower()
            if wallet_lower not in VIP_ADDRESSES:
                con.execute(
                    "INSERT OR IGNORE INTO vip_wallets(address, reason, added_at) VALUES(?, ?, ?)",
                    (wallet_lower, reason, int(now_utc().timestamp()))
                )
                new_wallets.append(wallet_lower)
        con.commit()
    
    # Update global VIP_ADDRESSES list
    VIP_ADDRESSES.extend(new_wallets)
    WATCH_ADDRESSES.extend([w for w in new_wallets if w not in WATCH_ADDRESSES])
    
    if new_wallets:
        stats["wallets_added_to_vip"] += len(new_wallets)
        print(f"[VIP] Added {len(new_wallets)} wallets to VIP list: {reason}")

def save_cluster_to_db(cluster: Dict[str, Any]):
    """Save detected cluster to database"""
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            INSERT OR REPLACE INTO suspicious_clusters(
                cluster_id, wallets, token, total_notional, trade_count,
                time_window_minutes, suspicion_score, first_trade_ms, 
                last_trade_ms, news_event, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cluster['cluster_id'],
            json.dumps(cluster['wallets']),
            cluster['token'],
            cluster['total_notional'],
            cluster['trade_count'],
            cluster['time_window'],
            cluster['score'],
            cluster['first_trade_ms'],
            cluster['last_trade_ms'],
            cluster.get('news_event', ''),
            int(now_utc().timestamp())
        ))
        con.commit()

# -------------------------
# Alert formatting
# -------------------------
async def post_slack(blocks: list):
    payload = {"blocks": blocks}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(WEBHOOK_URL, json=payload)
        r.raise_for_status()
    stats["alerts_sent"] += 1

async def post_discord(content: str, embeds: Optional[list]=None):
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(WEBHOOK_URL, json=payload)
        r.raise_for_status()
    stats["alerts_sent"] += 1

async def send_status_message(message_type: str, details: Optional[Dict[str, Any]] = None):
    """Send status/health updates to Slack with nautical theme"""
    if not WEBHOOK_URL:
        return
    
    try:
        if WEBHOOK_TARGET == "slack":
            blocks = []
            
            if message_type == "startup":
                blocks = [
                    {"type": "header", "text": {"type": "plain_text", "text": "⚓ Captain Ahab Reporting for Duty"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": (
                        f"*The hunt begins!* 🐋\n\n"
                        f"🎯 Monitoring *{len(WATCH_ADDRESSES)}* addresses\n"
                        f"🚨 VIP watch list: *{len(VIP_ADDRESSES)}* addresses\n"
                        f"⏱️ Polling every *{POLL_SECONDS}s*\n"
                        f"💰 Short threshold: *${USD_SHORT_THRESHOLD:,.0f}*\n"
                        f"💵 Deposit threshold: *${USD_DEPOSIT_THRESHOLD:,.0f}*\n\n"
                        f"_\"From hell's heart, I stab at thee; for hate's sake, I spit my last breath at thee!\"_"
                    )}},
                    {"type": "divider"}
                ]
            
            elif message_type == "status_report":
                uptime = datetime.now(timezone.utc) - stats["start_time"] if stats["start_time"] else timedelta(0)
                uptime_hours = int(uptime.total_seconds() / 3600)
                uptime_mins = int((uptime.total_seconds() % 3600) / 60)
                
                hl_status = "⛵ Smooth sailing" if stats["hyperliquid_status"] == "healthy" else "⚠️ Choppy waters"
                cluster_status = "🎣 Active" if CLUSTER_DETECTION_ENABLED else "💤 Disabled"
                
                blocks = [
                    {"type": "header", "text": {"type": "plain_text", "text": "🌊 Status Report from the Pequod"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": (
                        f"*Captain's Log - {now_utc().strftime('%Y-%m-%d %H:%M UTC')}*\n\n"
                        f"⏰ Watch duration: *{uptime_hours}h {uptime_mins}m*\n"
                        f"🔍 Wallet scans: *{stats['scans_completed']}*\n"
                        f"🌊 Market scans: *{stats['market_scans_completed']}*\n"
                        f"📡 Hyperliquid: {hl_status}\n"
                        f"✅ API success: *{stats['api_calls_successful']}*\n"
                        f"❌ API failures: *{stats['api_calls_failed']}*\n"
                        f"🚨 Alerts sent: *{stats['alerts_sent']}*\n"
                        f"🐋 Clusters detected: *{stats['clusters_detected']}*\n"
                        f"🎯 VIP wallets added: *{stats['wallets_added_to_vip']}*\n"
                        f"🎣 Cluster detection: {cluster_status}\n\n"
                        f"_The white whale still eludes us, but we remain ever vigilant..._"
                    )}},
                    {"type": "divider"}
                ]
            
            elif message_type == "api_error":
                blocks = [
                    {"type": "header", "text": {"type": "plain_text", "text": "⚠️ Troubled Waters Ahead"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": (
                        f"*Connection issues detected!*\n\n"
                        f"🌊 Hyperliquid API: {details.get('error', 'Unknown error')}\n"
                        f"📊 Success rate: {details.get('success_rate', 'N/A')}\n\n"
                        f"_Still hunting, captain! We shall persevere!_"
                    )}},
                    {"type": "divider"}
                ]
            
            elif message_type == "recovery":
                blocks = [
                    {"type": "section", "text": {"type": "mrkdwn", "text": (
                        f"✅ *Waters have calmed* - Connection restored to Hyperliquid\n"
                        f"_Back on the hunt!_ ⚓"
                    )}}
                ]
            
            elif message_type == "suspicious_cluster":
                cluster = details.get('cluster', {})
                wallets = cluster.get('wallets', [])
                
                # Format wallet list with notional values
                wallet_lines = []
                for i, w in enumerate(wallets[:10]):  # Show max 10
                    # Try to find notional for this specific wallet
                    wallet_notional = 0
                    for trade in cluster.get('trades', []):
                        if trade.get('wallet', '') == w or trade.get('address', '') == w:
                            wallet_notional += trade.get('notional', 0)
                    wallet_lines.append(f"• `{w[:10]}...{w[-6:]}` (${wallet_notional/1e6:.1f}M)")
                
                wallet_list = "\n".join(wallet_lines) if wallet_lines else "• Multiple wallets"
                
                blocks = [
                    {"type": "header", "text": {"type": "plain_text", 
                        "text": "⚠️ SUSPICIOUS CLUSTER DETECTED ⚠️"}},
                    {"type": "section", "text": {"type": "mrkdwn", "text": (
                        f"*\"Thar she blows! A pod of whales hunting in formation!\"* 🐋\n\n"
                        f"🔴 **Suspicion Score: {cluster.get('score', 0)}/100**\n\n"
                        f"📊 Cluster Details:\n"
                        f"• Wallets: *{len(wallets)}*\n"
                        f"• Token: *{cluster.get('token', 'Multiple')}*\n"
                        f"• Total notional: *${cluster.get('total_notional', 0):,.0f}*\n"
                        f"• Time window: *{cluster.get('time_window', 0):.1f} minutes*\n"
                        f"• Direction: *{cluster.get('direction', 'UNKNOWN')}*\n"
                        f"• Alignment: *{cluster.get('alignment', 0)*100:.0f}%*\n\n"
                        f"⏰ Timeline:\n"
                        f"• First trade: `{ms_to_iso(cluster.get('first_trade_ms', 0))}`\n"
                        f"• Last trade: `{ms_to_iso(cluster.get('last_trade_ms', 0))}`\n\n"
                        f"🎯 Wallets:\n{wallet_list}\n\n"
                        f"🚨 **ACTION**: Adding all wallets to VIP watch list\n\n"
                        f"_\"All ye harpooneers stand ready with your irons!\"_"
                    )}},
                    {"type": "divider"}
                ]
            
            await post_slack(blocks)
        else:
            # Discord version
            if message_type == "startup":
                content = (
                    f"⚓ **Captain Ahab Reporting for Duty** 🐋\n\n"
                    f"🎯 Monitoring {len(WATCH_ADDRESSES)} addresses\n"
                    f"🚨 VIP watch: {len(VIP_ADDRESSES)} addresses\n"
                    f"⏱️ Poll: {POLL_SECONDS}s | Thresholds: ${USD_SHORT_THRESHOLD:,.0f} / ${USD_DEPOSIT_THRESHOLD:,.0f}"
                )
            elif message_type == "status_report":
                uptime = datetime.now(timezone.utc) - stats["start_time"] if stats["start_time"] else timedelta(0)
                content = (
                    f"🌊 **Status Report** | Uptime: {int(uptime.total_seconds()/3600)}h {int((uptime.total_seconds()%3600)/60)}m\n"
                    f"Scans: {stats['scans_completed']} | API: {stats['api_calls_successful']}✅ {stats['api_calls_failed']}❌ | Alerts: {stats['alerts_sent']}"
                )
            else:
                content = f"Status update: {message_type}"
            
            await post_discord(content)
    except Exception as e:
        print(f"[ERROR] Failed to send status message: {e}")

def to_slack_blocks(address: str, items: List[Dict[str,Any]], is_vip: bool = False) -> list:
    vip_marker = "🚨 VIP WALLET " if is_vip else ""
    blocks = [{
        "type":"header",
        "text":{"type":"plain_text","text":f"{vip_marker}Hyperliquid Alert – {address[:10]}...{address[-6:]}"}
    }]
    
    for f in items:
        t = f["kind"]
        if t == "LARGE_DEPOSIT":
            blocks += [
                {"type":"section","text":{"type":"mrkdwn",
                    "text":(
                        "*Large Deposit* :moneybag:\n"
                        f"• Token: `{f['token']}`\n"
                        f"• USD: *${f['usdamount']:,.0f}*\n"
                        f"• Time (UTC): `{ms_to_iso(f['time_ms'])}`\n"
                        f"• Tx: `{f.get('hash','')}`"
                    )}},
                {"type":"divider"}
            ]
        elif t == "LARGE_OPEN_SHORT":
            blocks += [
                {"type":"section","text":{"type":"mrkdwn",
                    "text":(
                        "*Very Large Short OPEN* :rotating_light:\n"
                        f"• {f.get('token','?')} size: `{f.get('amount','?')}` @ `${f.get('px','?')}`\n"
                        f"• Notional: *${f['notional']:,.0f}*\n"
                        f"• Time (UTC): `{ms_to_iso(f['time_ms'])}`\n"
                        f"• Tx: `{f.get('hash','')}`"
                    )}},
                {"type":"divider"}
            ]
        elif t == "VIP_ACTIVITY":
            activity_type = f.get('activity_type', 'Activity')
            blocks += [
                {"type":"section","text":{"type":"mrkdwn",
                    "text":(
                        f"*🚨 VIP {activity_type}* :warning:\n"
                        f"• Type: `{f.get('subtype','')}`\n"
                        f"• Token: `{f.get('token','')}`\n"
                        f"• Amount: `{f.get('amount','')}` @ `${f.get('px','')}`\n"
                        f"• Notional: *${f.get('notional',0):,.2f}*\n"
                        f"• Time (UTC): `{ms_to_iso(f['time_ms'])}`\n"
                        f"• Tx: `{f.get('hash','')}`"
                    )}},
                {"type":"divider"}
            ]
    return blocks

def to_discord_msg(address: str, items: List[Dict[str,Any]], is_vip: bool = False) -> (str, list):
    vip_marker = "🚨 VIP WALLET " if is_vip else ""
    lines = [f"**{vip_marker}Hyperliquid Alert – {address[:10]}...{address[-6:]}**"]
    embeds = []
    
    for f in items:
        if f["kind"] == "LARGE_DEPOSIT":
            lines.append(
                f"💰 Large Deposit | {f['token']} | ${f['usdamount']:,.0f} | {ms_to_iso(f['time_ms'])} UTC"
            )
        elif f["kind"] == "LARGE_OPEN_SHORT":
            lines.append(
                f"🚨 Large Short OPEN | {f.get('token','?')} | size {f.get('amount','?')} @ ${f.get('px','?')} | "
                f"Notional ${f['notional']:,.0f} | {ms_to_iso(f['time_ms'])} UTC"
            )
        elif f["kind"] == "VIP_ACTIVITY":
            activity_type = f.get('activity_type', 'Activity')
            lines.append(
                f"🚨 VIP {activity_type} | {f.get('token','')} | "
                f"{f.get('subtype','')} | {f.get('amount','')} @ ${f.get('px','')} | "
                f"Notional ${f.get('notional',0):,.2f} | {ms_to_iso(f['time_ms'])} UTC"
            )
    
    return "\n".join(lines), embeds

# -------------------------
# Classification rules
# -------------------------
def classify_events(address: str, perps: List[Dict[str,Any]], transfers: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    out = []
    vip = is_vip(address)

    # Deposits - for VIP addresses, alert on ANY deposit; for others, only large ones
    for r in transfers:
        tok = (r.get("token") or "").upper()
        typ = r.get("type")
        usd = float(r.get("usdamount") or 0)
        ts_ms = int(r.get("time") or 0)
        
        if vip and typ in ("Deposit", "Withdraw"):
            # VIP: alert on any deposit/withdrawal
            out.append({
                "kind": "VIP_ACTIVITY",
                "activity_type": typ.upper(),
                "subtype": typ,
                "token": tok,
                "amount": usd,
                "px": "",
                "notional": usd,
                "time_ms": ts_ms,
                "hash": r.get("hash")
            })
        elif not vip and tok in ("USDC","USDT") and typ == "Deposit" and usd >= USD_DEPOSIT_THRESHOLD:
            # Regular: only large deposits
            out.append({
                "kind":"LARGE_DEPOSIT",
                "token": tok,
                "usdamount": usd,
                "time_ms": ts_ms,
                "hash": r.get("hash")
            })

    # Perps/Trades - for VIP addresses, alert on ANY trade; for others, only large short opens
    for r in perps:
        typ = (r.get("type") or "").lower()
        side = (r.get("side") or "").lower()
        ts_ms = int(r.get("time") or 0)
        token = r.get("token", "")
        notional = float(r.get("notional") or 0)
        amount = r.get("amount", 0)
        px = r.get("px", 0)
        
        if vip:
            # VIP: alert on ANY trade activity
            out.append({
                "kind": "VIP_ACTIVITY",
                "activity_type": "TRADE",
                "subtype": f"{side.upper()} {typ.replace('_', ' ').upper()}",
                "token": token,
                "amount": amount,
                "px": px,
                "notional": notional,
                "time_ms": ts_ms,
                "hash": r.get("hash")
            })
        else:
            # Regular: only very large short opens
            is_open_short = ("open" in typ and "short" in typ) or ("short_open" in typ) or side == "sell"
            if is_open_short and notional >= USD_SHORT_THRESHOLD:
                out.append({
                    "kind":"LARGE_OPEN_SHORT",
                    "token": token,
                    "amount": amount,
                    "px": px,
                    "notional": notional,
                    "time_ms": ts_ms,
                    "hash": r.get("hash")
                })
    
    return out

# -------------------------
# Market-wide scanning
# -------------------------
def get_recent_market_trades(window_minutes: int = None) -> List[Dict[str, Any]]:
    """
    Get recent large trades from database for cluster detection
    """
    if window_minutes is None:
        window_minutes = CLUSTER_TIME_WINDOW_MINUTES
    
    cutoff_ms = int((now_utc() - timedelta(minutes=window_minutes)).timestamp() * 1000)
    
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("""
            SELECT trade_id, wallet, token, side, notional, timestamp_ms, wallet_age_days
            FROM market_trades
            WHERE timestamp_ms >= ?
            AND notional >= ?
            ORDER BY timestamp_ms DESC
        """, (cutoff_ms, MARKET_MIN_TRADE_SIZE))
        
        trades = []
        for row in cur.fetchall():
            trades.append({
                'trade_id': row[0],
                'wallet': row[1],
                'token': row[2],
                'side': row[3],
                'notional': row[4],
                'timestamp_ms': row[5],
                'wallet_age_days': row[6] or 30
            })
        
        return trades

def store_market_trade(trade: Dict[str, Any]):
    """Store a large trade in market_trades table"""
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            INSERT OR REPLACE INTO market_trades(
                trade_id, wallet, token, side, notional, timestamp_ms, wallet_age_days
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get('hash', trade.get('trade_id', sha_key(str(trade)))),
            trade.get('wallet', trade.get('address', '')),
            trade.get('token', ''),
            trade.get('side', ''),
            trade.get('notional', 0),
            trade.get('timestamp_ms', trade.get('time', 0)),
            trade.get('wallet_age_days', 30)
        ))
        con.commit()

async def scan_for_clusters():
    """
    Scan recent market trades for suspicious clusters
    """
    if not CLUSTER_DETECTION_ENABLED:
        return
    
    # Get recent trades from database
    recent_trades = get_recent_market_trades()
    
    if len(recent_trades) < 3:
        return
    
    # Group by token for more focused cluster detection
    tokens = set(t['token'] for t in recent_trades if t.get('token'))
    
    for token in tokens:
        token_trades = [t for t in recent_trades if t.get('token') == token]
        
        if len(token_trades) < 3:
            continue
        
        # Detect cluster
        cluster = detect_trading_cluster(token_trades)
        
        if cluster:
            # Check if we've already seen this cluster
            cluster_digest = cluster['cluster_id']
            
            if not is_seen(cluster_digest):
                mark_seen(cluster_digest)
                stats["clusters_detected"] += 1
                
                print(f"[CLUSTER] Detected! Score: {cluster['score']}, Wallets: {len(cluster['wallets'])}, Token: {cluster['token']}")
                
                # Save to database
                save_cluster_to_db(cluster)
                
                # Send alert
                await send_status_message("suspicious_cluster", {"cluster": cluster})
                
                # Auto-add wallets to VIP list
                reason = f"Cluster {cluster['cluster_id'][:8]} (score: {cluster['score']})"
                await add_wallets_to_vip(cluster['wallets'], reason)
    
    stats["market_scans_completed"] += 1

# -------------------------
# Poll loop
# -------------------------
async def scan_once():
    if not WATCH_ADDRESSES or not WEBHOOK_URL:
        print("[INFO] No WATCH_ADDRESSES or WEBHOOK_URL configured, skipping scan")
        return

    since_ms_default = int((now_utc() - timedelta(minutes=LOOKBACK_MINUTES)).timestamp() * 1000)

    for addr in WATCH_ADDRESSES:
        source_key = f"hyperliquid:addr:{addr}"
        since_ms = get_cursor(source_key, since_ms_default)

        try:
            perps, transfers = await asyncio.gather(
                fetch_perps(addr, since_ms),
                fetch_transfers(addr, since_ms)
            )
        except Exception as e:
            # Avoid crashing loop; log to console
            print(f"[WARN] fetch error for {addr}: {e}")
            continue

        # Store large trades for cluster detection
        for trade in perps:
            if trade.get('notional', 0) >= MARKET_MIN_TRADE_SIZE:
                trade['wallet'] = addr
                trade['address'] = addr
                store_market_trade(trade)
        
        findings = classify_events(addr, perps, transfers)

        # Deduplicate by tx/time
        deduped = []
        max_ms = since_ms
        for f in findings:
            digest = sha_key(addr, f["kind"], f.get("hash",""), str(f.get("time_ms",0)))
            if not is_seen(digest):
                mark_seen(digest)
                deduped.append(f)
                max_ms = max(max_ms, f.get("time_ms", since_ms))

        if deduped:
            vip = is_vip(addr)
            print(f"[ALERT] {len(deduped)} new event(s) for {addr} (VIP: {vip})")
            
            if WEBHOOK_TARGET == "slack":
                blocks = to_slack_blocks(addr, deduped, vip)
                await post_slack(blocks)
            else:
                content, embeds = to_discord_msg(addr, deduped, vip)
                await post_discord(content, embeds)

        # Move cursor forward to "now" to avoid re-pulling huge windows
        set_cursor(source_key, int(now_utc().timestamp()*1000))
    
    # Track completed scan
    stats["scans_completed"] += 1

@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"

def load_vip_wallets_from_db():
    """Load dynamically added VIP wallets from database on startup"""
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT address, reason FROM vip_wallets")
        loaded = 0
        for row in cur.fetchall():
            address, reason = row
            if address not in VIP_ADDRESSES:
                VIP_ADDRESSES.append(address)
                if address not in WATCH_ADDRESSES:
                    WATCH_ADDRESSES.append(address)
                loaded += 1
                print(f"[VIP] Loaded from DB: {address[:10]}... ({reason})")
        
        if loaded > 0:
            print(f"[VIP] Loaded {loaded} wallets from database")

async def poll_loop():
    ensure_db()
    
    # Load VIP wallets from database (persisted across restarts)
    load_vip_wallets_from_db()
    
    # Initialize stats
    stats["start_time"] = now_utc()
    
    print(f"[START] Monitoring {len(WATCH_ADDRESSES)} addresses (VIP: {len(VIP_ADDRESSES)})")
    print(f"[CONFIG] Poll interval: {POLL_SECONDS}s, Lookback: {LOOKBACK_MINUTES}min")
    print(f"[CONFIG] Short threshold: ${USD_SHORT_THRESHOLD:,.0f}, Deposit threshold: ${USD_DEPOSIT_THRESHOLD:,.0f}")
    print(f"[CONFIG] Cluster detection: {'ENABLED' if CLUSTER_DETECTION_ENABLED else 'DISABLED'}")
    
    # Send startup notification
    await send_status_message("startup")
    
    # Track time for periodic status reports (every 2 hours)
    next_status_report = now_utc() + timedelta(hours=2)
    
    while True:
        try:
            await scan_once()
            
            # Run cluster detection after wallet scans
            if CLUSTER_DETECTION_ENABLED:
                await scan_for_clusters()
            
            # Send periodic status report
            if now_utc() >= next_status_report:
                await send_status_message("status_report")
                next_status_report = now_utc() + timedelta(hours=2)
                
        except Exception as e:
            print("[ERROR] scan_once failed:", e)
        await asyncio.sleep(POLL_SECONDS)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(poll_loop())

