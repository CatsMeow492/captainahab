#!/usr/bin/env python3
"""
Research Script: October 14, 2025 Trump Tariff Insider Trading Cluster

Queries Hyperliquid for all large shorts opened in the suspicious time window
before Trump's tariff announcement at 13:07 UTC.

Usage:
    python research/find_insider_cluster.py
"""
import asyncio
import csv
import json
from datetime import datetime, timezone
from typing import List, Dict, Any

import httpx

# -------------------------
# Configuration
# -------------------------
HYPERLIQUID_API = "https://api.hyperliquid.xyz/info"

# Trump tariff tweet: Oct 14, 2025 at 13:07 UTC
TWEET_TIME_MS = int(datetime(2025, 10, 14, 13, 7, 0, tzinfo=timezone.utc).timestamp() * 1000)

# Research window: 2 hours before tweet
WINDOW_START_MS = TWEET_TIME_MS - (2 * 60 * 60 * 1000)  # 11:07 UTC
WINDOW_END_MS = TWEET_TIME_MS  # 13:07 UTC

# Thresholds for suspicious activity
MIN_NOTIONAL_USD = 5_000_000  # $5M minimum to be interesting
MIN_CLUSTER_SCORE = 60  # Lower than real-time to capture more

# Known suspicious addresses (partial or complete)
KNOWN_SUSPECTS = [
    "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae",
    # Add more as we discover them
]

# Top traders to scan (you can expand this list)
# These are example addresses - would need actual high-volume traders
KNOWN_LARGE_TRADERS = [
    "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae",
    # Add more known whale addresses here
]

# -------------------------
# API Functions
# -------------------------
async def fetch_user_fills(address: str) -> List[Dict[str, Any]]:
    """Fetch all trade fills for a user"""
    payload = {
        "type": "userFills",
        "user": address
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(HYPERLIQUID_API, json=payload)
            r.raise_for_status()
            return r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print(f"Error fetching fills for {address}: {e}")
        return []

def filter_suspicious_trades(fills: List[Dict], address: str) -> List[Dict]:
    """Filter for large shorts in the suspicious time window"""
    suspicious = []
    
    for fill in fills:
        fill_time_ms = int(fill.get("time", 0))
        
        # Check if within research window
        if not (WINDOW_START_MS <= fill_time_ms <= WINDOW_END_MS):
            continue
        
        # Check if it's a short (sell side)
        side = fill.get("side", "").lower()
        if side not in ["sell", "short"]:
            continue
        
        # Calculate notional
        size = abs(float(fill.get("sz", 0)))
        px = float(fill.get("px", 0))
        notional = size * px
        
        # Check if large enough
        if notional < MIN_NOTIONAL_USD:
            continue
        
        # This is a suspicious trade!
        suspicious.append({
            'wallet': address,
            'token': fill.get('coin', ''),
            'side': side,
            'size': size,
            'price': px,
            'notional': notional,
            'timestamp_ms': fill_time_ms,
            'timestamp_utc': datetime.fromtimestamp(fill_time_ms/1000, tz=timezone.utc).isoformat(),
            'minutes_before_tweet': (TWEET_TIME_MS - fill_time_ms) / (1000 * 60),
            'trade_id': fill.get('tid', ''),
            'order_id': fill.get('oid', '')
        })
    
    return suspicious

# -------------------------
# Cluster Analysis
# -------------------------
def analyze_cluster(trades: List[Dict]) -> Dict[str, Any]:
    """Analyze a set of trades for cluster characteristics"""
    if not trades:
        return {}
    
    wallets = list(set(t['wallet'] for t in trades))
    timestamps = [t['timestamp_ms'] for t in trades]
    
    time_span_ms = max(timestamps) - min(timestamps)
    time_span_minutes = time_span_ms / (1000 * 60)
    
    total_notional = sum(t['notional'] for t in trades)
    
    # Group by wallet
    wallet_breakdown = {}
    for trade in trades:
        wallet = trade['wallet']
        if wallet not in wallet_breakdown:
            wallet_breakdown[wallet] = {
                'trades': 0,
                'total_notional': 0,
                'tokens': set()
            }
        wallet_breakdown[wallet]['trades'] += 1
        wallet_breakdown[wallet]['total_notional'] += trade['notional']
        wallet_breakdown[wallet]['tokens'].add(trade['token'])
    
    return {
        'total_trades': len(trades),
        'unique_wallets': len(wallets),
        'total_notional': total_notional,
        'time_span_minutes': time_span_minutes,
        'first_trade': min(trades, key=lambda t: t['timestamp_ms']),
        'last_trade': max(trades, key=lambda t: t['timestamp_ms']),
        'tokens': list(set(t['token'] for t in trades)),
        'wallet_breakdown': {
            w: {
                'trades': data['trades'],
                'notional': data['total_notional'],
                'tokens': list(data['tokens'])
            }
            for w, data in wallet_breakdown.items()
        }
    }

# -------------------------
# Main Research Function
# -------------------------
async def main():
    print("=" * 80)
    print("🔍 INSIDER TRADING CLUSTER RESEARCH")
    print("=" * 80)
    print(f"\nEvent: Trump Tariff Tweet")
    print(f"Time: October 14, 2025 at 13:07 UTC")
    print(f"\nResearch Window:")
    print(f"  Start: {datetime.fromtimestamp(WINDOW_START_MS/1000, tz=timezone.utc).isoformat()}")
    print(f"  End:   {datetime.fromtimestamp(WINDOW_END_MS/1000, tz=timezone.utc).isoformat()}")
    print(f"  Duration: 2 hours before tweet")
    print(f"\nThreshold: ${MIN_NOTIONAL_USD:,.0f} minimum notional")
    print("=" * 80)
    
    # Scan known suspects first
    print(f"\n📊 Scanning {len(KNOWN_SUSPECTS)} known suspects...")
    all_suspicious_trades = []
    
    for address in KNOWN_SUSPECTS:
        print(f"\n  Querying {address[:10]}...{address[-6:]}")
        fills = await fetch_user_fills(address)
        suspicious = filter_suspicious_trades(fills, address)
        
        if suspicious:
            print(f"    ✅ Found {len(suspicious)} suspicious trades (${sum(t['notional'] for t in suspicious):,.0f} total)")
            all_suspicious_trades.extend(suspicious)
        else:
            print(f"    ⚪ No suspicious activity in window")
    
    # Optionally scan additional known large traders
    if KNOWN_LARGE_TRADERS:
        print(f"\n📊 Scanning {len(KNOWN_LARGE_TRADERS)} additional large traders...")
        for address in KNOWN_LARGE_TRADERS:
            if address in KNOWN_SUSPECTS:
                continue
            
            print(f"\n  Querying {address[:10]}...{address[-6:]}")
            fills = await fetch_user_fills(address)
            suspicious = filter_suspicious_trades(fills, address)
            
            if suspicious:
                print(f"    ⚠️  Found {len(suspicious)} suspicious trades! (${sum(t['notional'] for t in suspicious):,.0f})")
                all_suspicious_trades.extend(suspicious)
    
    # Analyze the cluster
    print("\n" + "=" * 80)
    print("📈 CLUSTER ANALYSIS")
    print("=" * 80)
    
    if all_suspicious_trades:
        analysis = analyze_cluster(all_suspicious_trades)
        
        print(f"\n🐋 Cluster Summary:")
        print(f"  Total trades: {analysis['total_trades']}")
        print(f"  Unique wallets: {analysis['unique_wallets']}")
        print(f"  Total notional: ${analysis['total_notional']:,.0f}")
        print(f"  Time span: {analysis['time_span_minutes']:.1f} minutes")
        print(f"  Tokens: {', '.join(analysis['tokens'])}")
        
        print(f"\n⏰ Timeline:")
        first = analysis['first_trade']
        last = analysis['last_trade']
        print(f"  First trade: {first['timestamp_utc']} ({first['minutes_before_tweet']:.1f} min before tweet)")
        print(f"  Last trade:  {last['timestamp_utc']} ({last['minutes_before_tweet']:.1f} min before tweet)")
        
        print(f"\n🎯 Wallet Breakdown:")
        for wallet, data in analysis['wallet_breakdown'].items():
            print(f"  {wallet}:")
            print(f"    Trades: {data['trades']}")
            print(f"    Notional: ${data['notional']:,.0f}")
            print(f"    Tokens: {', '.join(data['tokens'])}")
        
        # Save to CSV
        csv_filename = "research/insider_cluster_oct14.csv"
        with open(csv_filename, 'w', newline='') as csvfile:
            fieldnames = ['wallet', 'token', 'side', 'size', 'price', 'notional', 
                         'timestamp_utc', 'minutes_before_tweet', 'trade_id']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for trade in sorted(all_suspicious_trades, key=lambda t: t['timestamp_ms']):
                writer.writerow({
                    'wallet': trade['wallet'],
                    'token': trade['token'],
                    'side': trade['side'],
                    'size': trade['size'],
                    'price': trade['price'],
                    'notional': trade['notional'],
                    'timestamp_utc': trade['timestamp_utc'],
                    'minutes_before_tweet': trade['minutes_before_tweet'],
                    'trade_id': trade['trade_id']
                })
        
        print(f"\n💾 Saved results to: {csv_filename}")
        
        # Save summary JSON
        json_filename = "research/cluster_analysis.json"
        with open(json_filename, 'w') as jsonfile:
            json.dump({
                'event': 'Trump Tariff Tweet',
                'event_time_utc': datetime.fromtimestamp(TWEET_TIME_MS/1000, tz=timezone.utc).isoformat(),
                'research_window_start': datetime.fromtimestamp(WINDOW_START_MS/1000, tz=timezone.utc).isoformat(),
                'research_window_end': datetime.fromtimestamp(WINDOW_END_MS/1000, tz=timezone.utc).isoformat(),
                'cluster_analysis': analysis,
                'all_trades': all_suspicious_trades
            }, jsonfile, indent=2, default=str)
        
        print(f"💾 Saved analysis to: {json_filename}")
        
        # Print VIP address configuration
        print("\n" + "=" * 80)
        print("🚨 RECOMMENDED VIP ADDRESSES")
        print("=" * 80)
        unique_wallets = list(set(t['wallet'] for t in all_suspicious_trades))
        print(f"\nAdd these to your Fly.io secrets:")
        print(f"\nflyctl secrets set VIP_ADDRESSES=\"{','.join(unique_wallets)}\"")
        print(f"\nOr update .env:")
        print(f"VIP_ADDRESSES={','.join(unique_wallets)}")
        
    else:
        print("\n⚠️  No suspicious trades found in the time window.")
        print("This could mean:")
        print("  1. The addresses provided don't have data from Oct 14")
        print("  2. The time window needs adjustment")
        print("  3. Need to scan more addresses")
        print("\nNext steps:")
        print("  - Add more addresses to KNOWN_LARGE_TRADERS")
        print("  - Check HypurrScan manually: https://hypurrscan.io")
        print("  - Search Twitter for wallet addresses mentioned in the incident")
    
    print("\n" + "=" * 80)
    print("✅ Research complete!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())


