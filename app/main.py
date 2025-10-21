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

# VIP lookback config
VIP_LOOKBACK_HOURS = int(os.getenv("VIP_LOOKBACK_HOURS", "48"))  # Look back 48 hours for VIP wallets

# Cluster detection config
CLUSTER_DETECTION_ENABLED = os.getenv("CLUSTER_DETECTION_ENABLED", "true").lower() == "true"
CLUSTER_TIME_WINDOW_MINUTES = int(os.getenv("CLUSTER_TIME_WINDOW_MINUTES", "60"))
CLUSTER_MIN_SCORE = int(os.getenv("CLUSTER_MIN_SCORE", "70"))
CLUSTER_MIN_NOTIONAL = float(os.getenv("CLUSTER_MIN_NOTIONAL", "50000000"))
MARKET_SCAN_TOKENS = [t.strip() for t in os.getenv("MARKET_SCAN_TOKENS", "BTC,ETH").split(",") if t.strip()]
MARKET_MIN_TRADE_SIZE = float(os.getenv("MARKET_MIN_TRADE_SIZE", "5000000"))
ENABLE_MARKET_SCANNING = os.getenv("ENABLE_MARKET_SCANNING", "true").lower() == "true"
MARKET_SCAN_INTERVAL_SECONDS = int(os.getenv("MARKET_SCAN_INTERVAL_SECONDS", "300"))  # 5 minutes

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

# VIP wallet activity tracking (last hour)
vip_activity = {}

def track_vip_activity(address: str, event_type: str, notional: float = 0, side: str = "", size: float = 0, token: str = ""):
    """Track VIP wallet activity for hourly summaries"""
    if address.lower() not in vip_activity:
        vip_activity[address.lower()] = {
            "trades": 0,
            "deposits": 0,
            "withdrawals": 0,
            "total_notional": 0,
            "last_activity": None,
            "positions": {}  # Track net position by token
        }
    
    activity = vip_activity[address.lower()]
    
    if event_type in ["TRADE", "trade"]:
        activity["trades"] += 1
        # Track position changes
        if token and size:
            if token not in activity["positions"]:
                activity["positions"][token] = 0
            # Buy adds to position, sell subtracts
            if side.lower() in ["b", "buy", "bid"]:
                activity["positions"][token] += size
            elif side.lower() in ["a", "sell", "ask", "short"]:
                activity["positions"][token] -= size
    elif event_type in ["DEPOSIT", "Deposit"]:
        activity["deposits"] += 1
    elif event_type in ["WITHDRAW", "Withdraw"]:
        activity["withdrawals"] += 1
    
    activity["total_notional"] += notional
    activity["last_activity"] = now_utc()

def reset_vip_activity():
    """Reset VIP activity tracking (called every hour)"""
    vip_activity.clear()

async def get_wallet_net_position(address: str) -> Dict[str, float]:
    """
    Calculate net position for a wallet by fetching ALL trade history.
    Returns dict of {token: net_position} where positive = long, negative = short.
    """
    try:
        payload = {
            "type": "userFills",
            "user": address
        }
        
        data = await http_post_json(HYPERLIQUID_API, payload)
        
        positions = {}
        if isinstance(data, list):
            for fill in data:
                coin = fill.get("coin", "")
                side = fill.get("side", "").lower()
                size = abs(float(fill.get("sz", 0)))
                
                if coin and size:
                    if coin not in positions:
                        positions[coin] = 0
                    
                    # Buy (bid) adds to position, Sell (ask) subtracts
                    if side in ["b", "buy", "bid"]:
                        positions[coin] += size
                    elif side in ["a", "sell", "ask", "short"]:
                        positions[coin] -= size
        
        # Filter out positions that are essentially zero
        return {k: v for k, v in positions.items() if abs(v) > 0.0001}
    
    except Exception as e:
        print(f"[ERROR] Failed to get net position for {address}: {e}")
        return {}

def get_vip_summary() -> Dict[str, Any]:
    """Get summary of VIP wallet activity"""
    total_trades = sum(v["trades"] for v in vip_activity.values())
    total_deposits = sum(v["deposits"] for v in vip_activity.values())
    total_withdrawals = sum(v["withdrawals"] for v in vip_activity.values())
    total_notional = sum(v["total_notional"] for v in vip_activity.values())
    
    return {
        "wallets_active": len(vip_activity),
        "total_trades": total_trades,
        "total_deposits": total_deposits,
        "total_withdrawals": total_withdrawals,
        "total_notional": total_notional,
        "wallet_details": vip_activity.copy()
    }

# -------------------------
# Utilities
# -------------------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat()

def sha_key(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        # Convert everything to string, handle None
        s = str(p) if p is not None else ""
        h.update(s.encode())
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
        
        # DEBUG: Log raw API response
        print(f"[DEBUG] fetch_perps for {address[:10]}...{address[-6:]}")
        print(f"[DEBUG]   Since: {ms_to_iso(since_ms)}")
        print(f"[DEBUG]   API returned: {len(data) if isinstance(data, list) else 'not a list'} fills")
        
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
                    # Hyperliquid uses: "A" = Ask (sell/short), "B" = Bid (buy/long)
                    is_short = side in ["sell", "short", "a", "ask"]
                    
                    trade = {
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
                    }
                    filtered.append(trade)
                    # DEBUG: Log each filtered trade
                    print(f"[DEBUG]   ✓ {side.upper()} {coin} {size:.4f} @ ${px:.2f} = ${notional:,.0f} "
                          f"at {ms_to_iso(fill_time_ms)}")
        
        print(f"[DEBUG]   Filtered: {len(filtered)} trades after {ms_to_iso(since_ms)}")
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
        
        # DEBUG: Log raw API response
        print(f"[DEBUG] fetch_transfers for {address[:10]}...{address[-6:]}")
        print(f"[DEBUG]   Since: {ms_to_iso(since_ms)}")
        print(f"[DEBUG]   API returned: {len(data) if isinstance(data, list) else 'not a list'} updates")
        
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
                            
                            transfer = {
                                "type": delta_type.capitalize(),
                                "token": "USDC",
                                "usdamount": usdc_amount,
                                "hash": update.get("hash", ""),
                                "time": update_time_ms
                            }
                            filtered.append(transfer)
                            # DEBUG: Log each filtered transfer
                            print(f"[DEBUG]   ✓ {delta_type.upper()} ${usdc_amount:,.0f} USDC "
                                  f"at {ms_to_iso(update_time_ms)}")
        
        print(f"[DEBUG]   Filtered: {len(filtered)} transfers after {ms_to_iso(since_ms)}")
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

async def fetch_market_activity(token: str, lookback_minutes: int = 60) -> List[Dict[str, Any]]:
    """
    Fetch recent market-wide trades for a token to discover whale activity.
    Returns large trades from ALL wallets (not just watched ones).
    """
    try:
        # Query for recent fills across the market
        # Note: Hyperliquid doesn't have a direct "all trades" endpoint
        # This is a placeholder - we'll populate from watched wallet scans
        # and gradually build a market picture
        
        # For now, return empty - market_trades table will be populated
        # from watched wallets and cluster detection will work from there
        return []
    
    except Exception as e:
        print(f"[ERROR] fetch_market_activity for {token}: {e}")
        return []

def calculate_dynamic_threshold(token: str, base_threshold: float) -> float:
    """
    Calculate dynamic threshold based on recent market activity.
    Returns adjusted threshold using percentile analysis.
    """
    try:
        # Get recent trades for this token from database
        cutoff_ms = int((now_utc() - timedelta(hours=24)).timestamp() * 1000)
        
        with sqlite3.connect(DB_PATH) as con:
            cur = con.execute("""
                SELECT notional FROM market_trades
                WHERE token = ? AND timestamp_ms >= ?
                ORDER BY notional DESC
                LIMIT 100
            """, (token, cutoff_ms))
            
            notionals = [row[0] for row in cur.fetchall()]
        
        if len(notionals) < 10:
            # Not enough data, use base threshold
            return base_threshold
        
        import statistics
        
        # Calculate 99th percentile
        notionals_sorted = sorted(notionals)
        percentile_99_idx = int(len(notionals_sorted) * 0.99)
        percentile_99 = notionals_sorted[percentile_99_idx] if percentile_99_idx < len(notionals_sorted) else notionals_sorted[-1]
        
        # Use lower of base threshold or 99th percentile
        # This catches "unusual for recent activity" even if below absolute threshold
        return min(base_threshold, percentile_99)
    
    except Exception as e:
        print(f"[DEBUG] Dynamic threshold calculation failed for {token}: {e}")
        return base_threshold

def is_unusually_large_for_wallet(wallet: str, notional: float) -> bool:
    """
    Check if this trade is unusually large compared to wallet's typical activity.
    Returns True if trade is 10x+ larger than wallet's median trade.
    """
    try:
        with sqlite3.connect(DB_PATH) as con:
            cur = con.execute("""
                SELECT notional FROM market_trades
                WHERE wallet = ?
                ORDER BY timestamp_ms DESC
                LIMIT 50
            """, (wallet,))
            
            notionals = [row[0] for row in cur.fetchall()]
        
        if len(notionals) < 5:
            # Not enough history
            return False
        
        import statistics
        median = statistics.median(notionals)
        
        # Is this trade 10x+ larger than median?
        return notional >= (median * 10)
    
    except Exception as e:
        return False

async def get_wallet_age_days(address: str) -> int:
    """
    Get wallet age in days by finding first trade timestamp.
    Caches result in database to avoid repeated API calls.
    """
    try:
        # Check cache first
        with sqlite3.connect(DB_PATH) as con:
            cur = con.execute("""
                SELECT first_trade_ms FROM trading_baselines
                WHERE address = ?
            """, (address,))
            row = cur.fetchone()
            
            if row and row[0]:
                first_trade_ms = row[0]
            else:
                # Fetch from API
                payload = {
                    "type": "userFills",
                    "user": address
                }
                data = await http_post_json(HYPERLIQUID_API, payload)
                
                if isinstance(data, list) and data:
                    # Get oldest trade
                    timestamps = [int(fill.get("time", 0)) for fill in data if fill.get("time")]
                    first_trade_ms = min(timestamps) if timestamps else int(now_utc().timestamp() * 1000)
                    
                    # Cache it
                    con.execute("""
                        INSERT OR REPLACE INTO trading_baselines(address, first_trade_ms, last_updated)
                        VALUES(?, ?, ?)
                    """, (address, first_trade_ms, int(now_utc().timestamp())))
                    con.commit()
                else:
                    # New wallet with no history
                    first_trade_ms = int(now_utc().timestamp() * 1000)
        
        # Calculate age in days
        age_ms = int(now_utc().timestamp() * 1000) - first_trade_ms
        age_days = max(0, age_ms / (1000 * 60 * 60 * 24))
        
        return int(age_days)
    
    except Exception as e:
        print(f"[DEBUG] Failed to get wallet age for {address}: {e}")
        return 30  # Default fallback

# -------------------------
# Cluster Detection
# -------------------------
def detect_size_clustering(trades: List[Dict[str, Any]]) -> float:
    """
    Detect if trade sizes cluster around similar values.
    Returns coefficient of variation (0-1, lower = more clustered).
    """
    import statistics
    
    notionals = [float(t.get('notional', 0)) for t in trades if t.get('notional', 0) > 0]
    
    if len(notionals) < 2:
        return 1.0
    
    mean = statistics.mean(notionals)
    if mean == 0:
        return 1.0
    
    stdev = statistics.stdev(notionals) if len(notionals) > 1 else 0
    coefficient_of_variation = stdev / mean
    
    return coefficient_of_variation

def detect_cross_token_coordination(trades: List[Dict[str, Any]]) -> int:
    """
    Detect if same wallets are trading multiple tokens in coordination.
    Returns count of tokens where same wallets appear.
    """
    wallet_tokens = {}
    
    for trade in trades:
        wallet = trade.get('wallet', trade.get('address', ''))
        token = trade.get('token', '')
        
        if wallet and token:
            if wallet not in wallet_tokens:
                wallet_tokens[wallet] = set()
            wallet_tokens[wallet].add(token)
    
    # Find wallets trading multiple tokens
    multi_token_wallets = {w: tokens for w, tokens in wallet_tokens.items() if len(tokens) > 1}
    
    # Count unique tokens being coordinated
    all_coordinated_tokens = set()
    for tokens in multi_token_wallets.values():
        all_coordinated_tokens.update(tokens)
    
    return len(all_coordinated_tokens)

def calculate_suspicion_score(cluster_data: Dict[str, Any]) -> int:
    """
    Score 0-100 for insider trading likelihood
    Enhanced with size clustering and cross-token detection
    """
    score = 0
    
    # Timing tightness (0-30 pts)
    time_span = cluster_data.get('time_span', 60)
    if time_span < 1:
        score += 30
    elif time_span < 5:
        score += 25
    elif time_span < 15:
        score += 15
    elif time_span < 30:
        score += 10
    elif time_span < 60:
        score += 5
        
    # Notional size (0-20 pts) - reduced from 25 to make room for new factors
    total_notional = cluster_data.get('total_notional', 0)
    notional_100m = total_notional / 100_000_000
    score += min(20, int(notional_100m * 8))
    
    # Wallet count (0-15 pts) - reduced from 20
    wallet_count = cluster_data.get('wallet_count', 0)
    score += min(15, wallet_count * 3)
    
    # Wallet age - newer is more suspicious (0-10 pts) - reduced from 15
    avg_wallet_age = cluster_data.get('avg_wallet_age', 30)
    if avg_wallet_age < 3:
        score += 10
    elif avg_wallet_age < 7:
        score += 7
    elif avg_wallet_age < 14:
        score += 4
        
    # Directional alignment (0-10 pts)
    alignment = cluster_data.get('alignment', 0.5)
    if alignment > 0.95:
        score += 10
    elif alignment > 0.9:
        score += 8
    elif alignment > 0.8:
        score += 5
    
    # NEW: Size clustering (0-15 pts) - similar trade sizes = coordinated
    size_cv = cluster_data.get('size_clustering_cv', 1.0)
    if size_cv < 0.1:  # Very tight clustering
        score += 15
    elif size_cv < 0.2:
        score += 10
    elif size_cv < 0.3:
        score += 5
    
    # NEW: Cross-token coordination (0-10 pts) - same wallets on multiple tokens
    cross_token_count = cluster_data.get('cross_token_count', 0)
    if cross_token_count >= 3:
        score += 10
    elif cross_token_count >= 2:
        score += 5
    
    # NEW: Timing precision bonus (0-10 pts) - all within 60 seconds
    if time_span < 1:
        score += 10
    
    return min(100, score)

async def detect_trading_cluster(trades: List[Dict[str, Any]], window_minutes: int = None) -> Optional[Dict[str, Any]]:
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
    sell_count = sum(1 for s in sides if s in ['sell', 'short', 'a', 'ask'])
    buy_count = sum(1 for s in sides if s in ['buy', 'long', 'b', 'bid'])
    alignment = max(sell_count, buy_count) / len(sides) if sides else 0.5
    
    if alignment < 0.8:
        return None
        
    # Determine dominant direction
    direction = "SHORT" if sell_count > buy_count else "LONG"
    
    # Get token (most common in cluster)
    tokens = [t.get('token', '') for t in trades if t.get('token')]
    token = max(set(tokens), key=tokens.count) if tokens else "Multiple"
    
    # Calculate average wallet age from all wallets in cluster
    wallet_ages = []
    for wallet in wallets:
        age = await get_wallet_age_days(wallet)
        wallet_ages.append(age)
    avg_wallet_age = sum(wallet_ages) / len(wallet_ages) if wallet_ages else 30
    
    # NEW: Detect size clustering
    size_cv = detect_size_clustering(trades)
    
    # NEW: Detect cross-token coordination  
    cross_token_count = detect_cross_token_coordination(trades)
    
    # Calculate suspicion score with enhanced factors
    cluster_data = {
        'wallet_count': len(wallets),
        'total_notional': total_notional,
        'time_span': time_span,
        'alignment': alignment,
        'avg_wallet_age': avg_wallet_age,
        'size_clustering_cv': size_cv,
        'cross_token_count': cross_token_count
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
        'alignment': alignment,
        'size_clustering_cv': size_cv,
        'cross_token_count': cross_token_count
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
            
            elif message_type == "vip_summary":
                summary = details.get('summary', {})
                wallet_count = len(VIP_ADDRESSES)
                
                # Fetch net positions for all VIP wallets
                vip_positions = {}
                for addr in VIP_ADDRESSES:
                    positions = await get_wallet_net_position(addr)
                    if positions:
                        vip_positions[addr.lower()] = positions
                
                # Format position summary
                position_lines = []
                for addr in VIP_ADDRESSES:
                    addr_lower = addr.lower()
                    positions = vip_positions.get(addr_lower, {})
                    
                    if positions:
                        for token, size in positions.items():
                            if size > 0:
                                position_lines.append(f"  • `{addr[:10]}...{addr[-6:]}`: 📈 LONG {token} ({size:.4f})")
                            elif size < 0:
                                position_lines.append(f"  • `{addr[:10]}...{addr[-6:]}`: 📉 SHORT {token} ({abs(size):.4f})")
                    else:
                        position_lines.append(f"  • `{addr[:10]}...{addr[-6:]}`: ⚖️ FLAT (no open positions)")
                
                positions_text = "\n".join(position_lines) if position_lines else "  • No position data available"
                
                if summary.get('wallets_active', 0) == 0:
                    # No activity - calm seas
                    blocks = [
                        {"type": "header", "text": {"type": "plain_text", "text": "🐋 VIP Whale Watch Summary"}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": (
                            f"*\"The sea was calm, the whales nowhere to be seen...\"*\n\n"
                            f"🔭 Watching: *{wallet_count} VIP whales*\n"
                            f"📊 Activity (last hour): **None detected**\n\n"
                            f"⚓ *Current Positions (All History):*\n{positions_text}\n\n"
                            f"_All quiet on the Hyperliquid front. The whales slumber beneath the waves..._"
                        )}},
                        {"type": "divider"}
                    ]
                else:
                    # Activity detected
                    wallet_lines = []
                    for addr, data in summary.get('wallet_details', {}).items():
                        total_events = data['trades'] + data['deposits'] + data['withdrawals']
                        if total_events > 0:
                            wallet_lines.append(
                                f"• `{addr[:10]}...{addr[-6:]}`: {data['trades']} trades, "
                                f"{data['deposits']} deposits, {data['withdrawals']} withdrawals "
                                f"(${data['total_notional']:,.0f})"
                            )
                    
                    wallet_summary = "\n".join(wallet_lines) if wallet_lines else "• No activity"
                    
                    blocks = [
                        {"type": "header", "text": {"type": "plain_text", "text": "🐋 VIP Whale Watch Summary"}},
                        {"type": "section", "text": {"type": "mrkdwn", "text": (
                            f"*\"Movement in the deep! The white whales stir!\"* 🐋\n\n"
                            f"🔭 Watching: *{wallet_count} VIP whales*\n"
                            f"📊 Active wallets: *{summary['wallets_active']}*\n"
                            f"📈 Total trades: *{summary['total_trades']}*\n"
                            f"💰 Total deposits: *{summary['total_deposits']}*\n"
                            f"💸 Total withdrawals: *{summary['total_withdrawals']}*\n"
                            f"💵 Total notional: *${summary['total_notional']:,.0f}*\n\n"
                            f"🎯 *Recent Activity:*\n{wallet_summary}\n\n"
                            f"⚓ *Current Positions (All History):*\n{positions_text}\n\n"
                            f"_\"The hunt continues through calm and storm alike...\"_ ⚓"
                        )}},
                        {"type": "divider"}
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
                
                # Format pattern indicators
                pattern_indicators = []
                size_cv = cluster.get('size_clustering_cv', 1.0)
                if size_cv < 0.3:
                    pattern_indicators.append(f"📏 Size clustering: {(1-size_cv)*100:.0f}% similar")
                
                cross_token = cluster.get('cross_token_count', 0)
                if cross_token > 0:
                    pattern_indicators.append(f"🔗 Cross-token: {cross_token} tokens")
                
                if cluster.get('time_window', 60) < 1:
                    pattern_indicators.append("⚡ Lightning fast: <60s")
                
                pattern_text = "\n• ".join(pattern_indicators) if pattern_indicators else "Standard directional cluster"
                
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
                        f"🎯 Pattern Indicators:\n• {pattern_text}\n\n"
                        f"⏰ Timeline:\n"
                        f"• First trade: `{ms_to_iso(cluster.get('first_trade_ms', 0))}`\n"
                        f"• Last trade: `{ms_to_iso(cluster.get('last_trade_ms', 0))}`\n\n"
                        f"🐋 Wallets:\n{wallet_list}\n\n"
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
    
    # DEBUG: Log classification input
    print(f"[DEBUG] classify_events for {address[:10]}...{address[-6:]}")
    print(f"[DEBUG]   VIP: {vip}")
    print(f"[DEBUG]   Processing: {len(perps)} perps, {len(transfers)} transfers")

    # Deposits - for VIP addresses, alert on ANY deposit; for others, only large ones
    for r in transfers:
        tok = (r.get("token") or "").upper()
        typ = r.get("type")
        usd = float(r.get("usdamount") or 0)
        ts_ms = int(r.get("time") or 0)
        
        if vip and typ in ("Deposit", "Withdraw"):
            # VIP: alert on any deposit/withdrawal
            track_vip_activity(address, typ, usd)
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
            track_vip_activity(address, "TRADE", notional, side=side, size=amount, token=token)
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
            is_open_short = ("open" in typ and "short" in typ) or ("short_open" in typ) or side in ["sell", "a", "ask"]
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
    
    # DEBUG: Log classification output
    print(f"[DEBUG]   Generated: {len(out)} alerts")
    for i, alert in enumerate(out, 1):
        print(f"[DEBUG]     Alert {i}: {alert['kind']} - {alert.get('activity_type', alert.get('token', ''))} "
              f"${alert.get('notional', 0):,.0f}")
    
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
    # Generate trade ID from hash or create one
    trade_hash = trade.get('hash', '')
    if not trade_hash:
        trade_id = trade.get('trade_id', '')
        if not trade_id:
            # Create ID from wallet, token, timestamp
            wallet = str(trade.get('wallet', trade.get('address', '')))
            token = str(trade.get('token', ''))
            timestamp = str(trade.get('timestamp_ms', trade.get('time', 0)))
            trade_id = sha_key(wallet, token, timestamp)
    else:
        trade_id = trade_hash
    
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
            INSERT OR REPLACE INTO market_trades(
                trade_id, wallet, token, side, notional, timestamp_ms, wallet_age_days
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
        """, (
            trade_id,
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
    
    print(f"[CLUSTER_SCAN] Checking database: {len(recent_trades)} recent trades (need 3+ to scan)")
    
    if len(recent_trades) < 3:
        print(f"[CLUSTER_SCAN] Not enough trades for cluster detection (have {len(recent_trades)}, need 3+)")
        return
    
    # Group by token for more focused cluster detection
    tokens = set(t['token'] for t in recent_trades if t.get('token'))
    
    for token in tokens:
        token_trades = [t for t in recent_trades if t.get('token') == token]
        
        if len(token_trades) < 3:
            continue
        
        # Detect cluster
        cluster = await detect_trading_cluster(token_trades)
        
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
    
    # Always increment counter when we complete a scan (even if no clusters found)
    stats["market_scans_completed"] += 1
    print(f"[CLUSTER_SCAN] Completed scan of {len(recent_trades)} trades across {len(tokens)} tokens")

# -------------------------
# Poll loop
# -------------------------
async def scan_once():
    if not WATCH_ADDRESSES or not WEBHOOK_URL:
        print("[INFO] No WATCH_ADDRESSES or WEBHOOK_URL configured, skipping scan")
        return

    for addr in WATCH_ADDRESSES:
        source_key = f"hyperliquid:addr:{addr}"
        
        # VIP wallets get longer lookback window to catch recent suspicious activity
        if is_vip(addr):
            since_ms_default = int((now_utc() - timedelta(hours=VIP_LOOKBACK_HOURS)).timestamp() * 1000)
        else:
            since_ms_default = int((now_utc() - timedelta(minutes=LOOKBACK_MINUTES)).timestamp() * 1000)
        
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
        stored_count = 0
        for trade in perps:
            if trade.get('notional', 0) >= MARKET_MIN_TRADE_SIZE:
                trade['wallet'] = addr
                trade['address'] = addr
                store_market_trade(trade)
                stored_count += 1
        
        if stored_count > 0:
            print(f"[MARKET_TRADES] Stored {stored_count} large trades (>=${MARKET_MIN_TRADE_SIZE/1e6:.0f}M) for {addr[:10]}...")
        
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
            
            # Limit alerts to prevent overwhelming Slack (max 10 per message)
            if len(deduped) > 10:
                print(f"[INFO] Too many events ({len(deduped)}), sending summary only")
                # Send summary instead of individual alerts
                summary_alert = [{
                    "kind": "VIP_ACTIVITY" if vip else "ACTIVITY_SUMMARY",
                    "activity_type": "MULTIPLE",
                    "subtype": f"{len(deduped)} events detected",
                    "token": "Various",
                    "amount": "",
                    "px": "",
                    "notional": sum(d.get('notional', 0) for d in deduped),
                    "time_ms": max(d.get('time_ms', 0) for d in deduped),
                    "hash": f"{len(deduped)} trades"
                }]
                deduped = summary_alert
            
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

@app.get("/status")
async def get_status():
    """Return current Captain Ahab status as JSON for frontend widget"""
    uptime = datetime.now(timezone.utc) - stats["start_time"] if stats["start_time"] else timedelta(0)
    
    # Get VIP positions
    vip_positions = {}
    for addr in VIP_ADDRESSES[:2]:  # Limit to avoid slow response
        positions = await get_wallet_net_position(addr)
        if positions:
            vip_positions[addr] = positions
    
    # Get VIP summary
    summary = get_vip_summary()
    
    return {
        "status": "hunting",
        "uptime_hours": uptime.total_seconds() / 3600,
        "monitoring": {
            "vip_wallets": len(VIP_ADDRESSES),
            "total_wallets": len(WATCH_ADDRESSES),
            "cluster_detection": CLUSTER_DETECTION_ENABLED
        },
        "stats": {
            "scans_completed": stats["scans_completed"],
            "alerts_sent": stats["alerts_sent"],
            "clusters_detected": stats["clusters_detected"],
            "vip_wallets_added": stats["wallets_added_to_vip"]
        },
        "vip_summary": {
            "wallets_active_last_hour": summary["wallets_active"],
            "trades_last_hour": summary["total_trades"],
            "total_notional_last_hour": summary["total_notional"]
        },
        "vip_positions": vip_positions,
        "hyperliquid_status": stats["hyperliquid_status"],
        "last_check": stats["last_hyperliquid_check"].isoformat() if stats["last_hyperliquid_check"] else None
    }

@app.post("/reset-vip-cursors", response_class=PlainTextResponse)
async def reset_vip_cursors():
    """Reset cursors for all VIP wallets to force re-scan of recent history"""
    count = 0
    with sqlite3.connect(DB_PATH) as con:
        for addr in VIP_ADDRESSES:
            source_key = f"hyperliquid:addr:{addr}"
            con.execute("DELETE FROM cursors WHERE source=?", (source_key,))
            count += 1
        con.commit()
    
    print(f"[ADMIN] Reset cursors for {count} VIP wallets - will re-scan last {VIP_LOOKBACK_HOURS} hours")
    return f"Reset {count} VIP wallet cursors. Next scan will look back {VIP_LOOKBACK_HOURS} hours."

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
    print("[POLL_LOOP] Starting poll_loop function...")
    
    try:
        ensure_db()
        print("[POLL_LOOP] Database ensured")
        
        # Load VIP wallets from database (persisted across restarts)
        load_vip_wallets_from_db()
        print("[POLL_LOOP] VIP wallets loaded")
        
        # Initialize stats
        stats["start_time"] = now_utc()
        
        print(f"[START] Monitoring {len(WATCH_ADDRESSES)} addresses (VIP: {len(VIP_ADDRESSES)})")
        print(f"[CONFIG] Poll interval: {POLL_SECONDS}s, Lookback: {LOOKBACK_MINUTES}min")
        print(f"[CONFIG] Short threshold: ${USD_SHORT_THRESHOLD:,.0f}, Deposit threshold: ${USD_DEPOSIT_THRESHOLD:,.0f}")
        print(f"[CONFIG] Cluster detection: {'ENABLED' if CLUSTER_DETECTION_ENABLED else 'DISABLED'}")
        
        # Send startup notification
        await send_status_message("startup")
        print("[POLL_LOOP] Startup message sent")
    except Exception as e:
        print(f"[POLL_LOOP ERROR] Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Track time for periodic reports
    next_status_report = now_utc() + timedelta(hours=2)
    next_vip_summary = now_utc() + timedelta(hours=1)
    
    # Send initial VIP summary at startup
    summary = get_vip_summary()
    await send_status_message("vip_summary", {"summary": summary})
    print("[VIP] Initial summary sent")
    
    while True:
        try:
            await scan_once()
            
            # Run cluster detection after wallet scans
            if CLUSTER_DETECTION_ENABLED:
                await scan_for_clusters()
            
            # Send periodic status report (every 2 hours)
            if now_utc() >= next_status_report:
                await send_status_message("status_report")
                next_status_report = now_utc() + timedelta(hours=2)
            
            # Send VIP summary (every hour)
            if now_utc() >= next_vip_summary:
                summary = get_vip_summary()
                await send_status_message("vip_summary", {"summary": summary})
                print(f"[VIP] Hourly summary sent ({summary['wallets_active']} active, {summary['total_trades']} trades)")
                reset_vip_activity()  # Reset for next hour
                next_vip_summary = now_utc() + timedelta(hours=1)
                
        except Exception as e:
            import traceback
            print(f"[ERROR] scan_once failed: {e}")
            print(f"[ERROR] Traceback: {traceback.format_exc()}")
        await asyncio.sleep(POLL_SECONDS)

@app.on_event("startup")
async def startup_event():
    try:
        print("[STARTUP] Creating background polling task...")
        asyncio.create_task(poll_loop())
        print("[STARTUP] Background task created successfully")
    except Exception as e:
        print(f"[STARTUP ERROR] Failed to create background task: {e}")
        import traceback
        traceback.print_exc()

