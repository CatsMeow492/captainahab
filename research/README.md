# Insider Trading Research

This directory contains tools and documentation for researching the October 14, 2025 insider trading incident.

## Files

- **`find_insider_cluster.py`** - Python script to query Hyperliquid historical data
- **`INSIDER_WALLETS.md`** - Documentation of identified insider addresses
- **`insider_cluster_oct14.csv`** - Output: All suspicious trades (generated after running script)
- **`cluster_analysis.json`** - Output: Cluster analysis summary (generated after running script)

## Quick Start

```bash
cd /Users/taylormohney/Documents/GitHub/captainahab

# Activate virtual environment
source venv/bin/activate

# Install dependencies if needed
pip install httpx

# Run the research
python research/find_insider_cluster.py
```

## What the Script Does

1. Queries Hyperliquid API for known suspect addresses
2. Filters for large shorts ($5M+) in 2-hour window before Trump's tweet
3. Analyzes timing, coordination, and total notional values
4. Exports results to CSV and JSON
5. Suggests VIP_ADDRESSES configuration

## Next Steps After Running

1. Review the generated CSV file
2. Add any new addresses to VIP monitoring:
   ```bash
   flyctl secrets set VIP_ADDRESSES="address1,address2,..."
   ```
3. Update `INSIDER_WALLETS.md` with findings
4. Share intelligence if needed

## Manual Research Resources

- **Hyperliquid Explorer:** https://app.hyperliquid.xyz/explorer
- **HypurrScan:** https://hypurrscan.io
- **Community:** Hyperliquid Discord, Twitter CT

## Time Window

- **Event:** Trump tariff tweet
- **Time:** October 14, 2025 at 13:07 UTC
- **Research Window:** 11:07 - 13:07 UTC (2 hours before)
- **Suspicious Pattern:** Large shorts opened just before public announcement


