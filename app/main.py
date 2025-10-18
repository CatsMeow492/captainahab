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
        print(f"[ERROR] fetch_perps for {address}: {e}")
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
        print(f"[ERROR] fetch_transfers for {address}: {e}")
        return []

# -------------------------
# Alert formatting
# -------------------------
async def post_slack(blocks: list):
    payload = {"blocks": blocks}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(WEBHOOK_URL, json=payload)
        r.raise_for_status()

async def post_discord(content: str, embeds: Optional[list]=None):
    payload = {"content": content}
    if embeds:
        payload["embeds"] = embeds
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(WEBHOOK_URL, json=payload)
        r.raise_for_status()

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

@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"

async def poll_loop():
    ensure_db()
    print(f"[START] Monitoring {len(WATCH_ADDRESSES)} addresses (VIP: {len(VIP_ADDRESSES)})")
    print(f"[CONFIG] Poll interval: {POLL_SECONDS}s, Lookback: {LOOKBACK_MINUTES}min")
    print(f"[CONFIG] Short threshold: ${USD_SHORT_THRESHOLD:,.0f}, Deposit threshold: ${USD_DEPOSIT_THRESHOLD:,.0f}")
    
    while True:
        try:
            await scan_once()
        except Exception as e:
            print("[ERROR] scan_once failed:", e)
        await asyncio.sleep(POLL_SECONDS)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(poll_loop())

