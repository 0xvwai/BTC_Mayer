"""
BTC DCA Backtest — 8 Years (Apr 2017 → Apr 2025)
=================================================
Indicators tested:
  1. Mayer Multiple  — Price / 200-day MA
  2. MVRV Ratio      — Market Cap / Realized Cap        (CoinMetrics: CapMVRVCur)
  3. Puell Multiple  — Daily Issuance USD / 365d MA     (CoinMetrics: IssTotUSD)
  4. NVT Signal      — Market Cap / 90d MA(Tx Volume)   (CoinMetrics: CapMrktCurUSD, TxTfrValAdjUSD)

Strategies compared:
  • Fixed DCA          — $100 every week, no variation
  • Mayer-only         — variable sizing based on Mayer Multiple zones
  • MVRV-only          — variable sizing based on MVRV zones
  • Puell-only         — variable sizing based on Puell Multiple zones
  • NVT-only           — variable sizing based on NVT Signal zones
  • Combined (avg)     — average the 4 multipliers each week

Data source: CoinMetrics Community API (free, no key required)
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ─── Config ─────────────────────────────────────────────────────────────────

START_DATE    = "2017-04-01"
END_DATE      = "2025-04-01"
BASE_AMOUNT   = 100          # USD per week (baseline)
DCA_WEEKDAY   = 0            # 0 = Monday
COINMETRICS   = "https://community-api.coinmetrics.io/v4"

# ─── Sizing tables ──────────────────────────────────────────────────────────
# Each table: list of (upper_bound, multiplier). Last entry upper_bound = inf.
# Multiplier 0 = pause DCA, 3 = triple DCA.

MAYER_TIERS = [
    (0.80, 3.0),   # < 0.80  → extreme undervalue, triple DCA
    (1.00, 2.0),   # 0.80–1.00 → below MA200, double DCA
    (1.50, 1.0),   # 1.00–1.50 → normal zone, standard DCA
    (2.40, 0.5),   # 1.50–2.40 → elevated, halve DCA
    (float('inf'), 0.0),  # > 2.40 → overheated, pause
]

MVRV_TIERS = [
    (1.0,  3.0),   # < 1.0  → below realized cost basis, triple DCA
    (1.5,  2.0),   # 1.0–1.5 → early accumulation, double
    (2.5,  1.0),   # 1.5–2.5 → fair value zone, standard
    (3.5,  0.5),   # 2.5–3.5 → elevated, halve
    (float('inf'), 0.0),  # > 3.5 → cycle top territory, pause
]

PUELL_TIERS = [
    (0.50, 3.0),   # < 0.50 → extreme miner stress (capitulation), triple DCA
    (0.80, 2.0),   # 0.50–0.80 → miner pain, double
    (1.50, 1.0),   # 0.80–1.50 → normal mining economics
    (3.00, 0.5),   # 1.50–3.00 → miners very profitable (late bull), halve
    (float('inf'), 0.0),  # > 3.0 → extreme miner revenue, pause
]

NVT_TIERS = [
    (50,   2.0),   # < 50  → high on-chain utility vs price, double
    (100,  1.0),   # 50–100 → fair value
    (150,  0.5),   # 100–150 → overvalued vs utility, halve
    (float('inf'), 0.0),  # > 150 → very overvalued, pause
]

# ─── Helpers ────────────────────────────────────────────────────────────────

def get_multiplier(value: float, tiers: list) -> float:
    """Look up sizing multiplier from a tier table."""
    if value is None or np.isnan(value):
        return 1.0  # default to standard if data unavailable
    for upper, mult in tiers:
        if value < upper:
            return mult
    return tiers[-1][1]


def fetch_metrics(metrics: str) -> pd.DataFrame:
    """
    Fetch daily BTC metrics from CoinMetrics community API.
    Returns a DataFrame indexed by date.
    """
    print(f"  Fetching: {metrics}...")
    url = (
        f"{COINMETRICS}/timeseries/asset-metrics"
        f"?assets=btc&metrics={metrics}"
        f"&frequency=1d&start_time={START_DATE}&end_time={END_DATE}"
        f"&page_size=10000"
    )
    resp = requests.get(url, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    df = pd.DataFrame(data)
    df["date"] = pd.to_datetime(df["time"]).dt.date
    df = df.set_index("date").drop(columns=["asset", "time"], errors="ignore")
    for col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def label(value, tiers, labels):
    """Return a human-readable zone label for a value."""
    if value is None or np.isnan(value):
        return "N/A"
    for i, (upper, _) in enumerate(tiers):
        if value < upper:
            return labels[i]
    return labels[-1]

# ─── Data Fetch ─────────────────────────────────────────────────────────────

print("=" * 60)
print("BTC DCA Backtest — Fetching 8 years of on-chain data")
print("=" * 60)

df_price  = fetch_metrics("PriceUSD")
df_mvrv   = fetch_metrics("CapMVRVCur")
df_iss    = fetch_metrics("IssTotUSD")
df_ntv    = fetch_metrics("CapMrktCurUSD,TxTfrValAdjUSD")

# Merge all into one daily DataFrame
df = df_price[["PriceUSD"]].copy()
df = df.join(df_mvrv[["CapMVRVCur"]], how="left")
df = df.join(df_iss[["IssTotUSD"]], how="left")
df = df.join(df_ntv[["CapMrktCurUSD", "TxTfrValAdjUSD"]], how="left")

df = df.sort_index()
print(f"\n  Loaded {len(df)} daily rows from {df.index[0]} to {df.index[-1]}")

# ─── Compute Indicators ─────────────────────────────────────────────────────

print("\n  Computing indicators...")

# 1. Mayer Multiple: Price / 200-day MA
df["MA200"]         = df["PriceUSD"].rolling(200, min_periods=150).mean()
df["MayerMultiple"] = df["PriceUSD"] / df["MA200"]

# 2. MVRV: already fetched as CapMVRVCur

# 3. Puell Multiple: IssTotUSD / 365-day MA of IssTotUSD
df["IssTotUSD_MA365"] = df["IssTotUSD"].rolling(365, min_periods=300).mean()
df["PuellMultiple"]   = df["IssTotUSD"] / df["IssTotUSD_MA365"]

# 4. NVT Signal: Market Cap / 90-day MA of on-chain tx volume
df["TxVol_MA90"] = df["TxTfrValAdjUSD"].rolling(90, min_periods=60).mean()
df["NVTSignal"]  = df["CapMrktCurUSD"] / df["TxVol_MA90"]

# ─── Weekly DCA Simulation ───────────────────────────────────────────────────

print("  Running DCA simulations...")

# Build weekly purchase dates (every Monday within range)
all_dates = pd.to_datetime([str(d) for d in df.index])
weekly_mask = all_dates.weekday == DCA_WEEKDAY
weekly_dates = all_dates[weekly_mask]

strategies = {
    "Fixed DCA":  {"btc": 0.0, "usd": 0.0},
    "Mayer-only": {"btc": 0.0, "usd": 0.0},
    "MVRV-only":  {"btc": 0.0, "usd": 0.0},
    "Puell-only": {"btc": 0.0, "usd": 0.0},
    "NVT-only":   {"btc": 0.0, "usd": 0.0},
    "Combined":   {"btc": 0.0, "usd": 0.0},
}

for dt in weekly_dates:
    d = dt.date()
    if d not in df.index:
        continue
    row = df.loc[d]
    price = row["PriceUSD"]
    if pd.isna(price) or price <= 0:
        continue

    mayer  = row["MayerMultiple"]
    mvrv   = row["CapMVRVCur"]
    puell  = row["PuellMultiple"]
    nvt    = row["NVTSignal"]

    m_mult  = get_multiplier(mayer, MAYER_TIERS)
    mv_mult = get_multiplier(mvrv,  MVRV_TIERS)
    pu_mult = get_multiplier(puell, PUELL_TIERS)
    nt_mult = get_multiplier(nvt,   NVT_TIERS)

    # Combined: average of available signals
    available = [m for m in [m_mult, mv_mult, pu_mult, nt_mult]
                 if not np.isnan(m)]
    comb_mult = np.mean(available) if available else 1.0

    def buy(name, mult):
        spend = BASE_AMOUNT * mult
        strategies[name]["usd"] += spend
        strategies[name]["btc"] += spend / price

    buy("Fixed DCA",  1.0)
    buy("Mayer-only", m_mult)
    buy("MVRV-only",  mv_mult)
    buy("Puell-only", pu_mult)
    buy("NVT-only",   nt_mult)
    buy("Combined",   comb_mult)

# ─── Results ────────────────────────────────────────────────────────────────

last_price = df["PriceUSD"].dropna().iloc[-1]
fixed_final_value = strategies["Fixed DCA"]["btc"] * last_price

print("\n" + "=" * 60)
print(f"BACKTEST RESULTS  (BTC price at end: ${last_price:,.0f})")
print(f"Period: {START_DATE} → {END_DATE}  |  Base: ${BASE_AMOUNT}/week")
print("=" * 60)
print(f"{'Strategy':<16} {'USD Spent':>12} {'BTC Acq.':>12} {'Final Value':>14} {'vs Fixed':>10} {'Avg Cost':>12}")
print("-" * 80)

for name, s in strategies.items():
    usd   = s["usd"]
    btc   = s["btc"]
    final = btc * last_price
    vs    = ((final - fixed_final_value) / fixed_final_value) * 100
    cost  = usd / btc if btc > 0 else 0
    vs_str = f"{vs:+.1f}%" if name != "Fixed DCA" else "—"
    print(f"{name:<16} ${usd:>11,.0f} {btc:>12.4f} ${final:>13,.0f} {vs_str:>10} ${cost:>11,.0f}")

print("=" * 60)

# ─── Indicator snapshot (latest values) ─────────────────────────────────────

latest = df.dropna(subset=["PriceUSD"]).iloc[-1]

MAYER_LABELS  = ["📉 Strong DCA", "🟢 DCA Zone", "✅ Standard DCA", "🟡 Reduce", "🚨 Pause"]
MVRV_LABELS   = ["💎 Aggressive DCA", "🟢 Double DCA", "✅ Standard DCA", "🟡 Reduce", "🚨 Pause"]
PUELL_LABELS  = ["💎 Triple DCA", "🟢 Double DCA", "✅ Standard DCA", "🟡 Reduce", "🚨 Pause"]
NVT_LABELS    = ["🟢 Double DCA", "✅ Standard DCA", "🟡 Reduce", "🚨 Pause"]

print("\nCURRENT INDICATOR READINGS")
print("-" * 50)
print(f"  Price          : ${latest['PriceUSD']:>10,.0f}")
print(f"  Mayer Multiple : {latest['MayerMultiple']:>10.3f}  →  {label(latest['MayerMultiple'], MAYER_TIERS, MAYER_LABELS)}")
print(f"  MVRV Ratio     : {latest['CapMVRVCur']:>10.3f}  →  {label(latest['CapMVRVCur'], MVRV_TIERS, MVRV_LABELS)}")
print(f"  Puell Multiple : {latest['PuellMultiple']:>10.3f}  →  {label(latest['PuellMultiple'], PUELL_TIERS, PUELL_LABELS)}")
print(f"  NVT Signal     : {latest['NVTSignal']:>10.1f}  →  {label(latest['NVTSignal'], NVT_TIERS, NVT_LABELS)}")

# Combined multiplier
ms = [
    get_multiplier(latest["MayerMultiple"], MAYER_TIERS),
    get_multiplier(latest["CapMVRVCur"],    MVRV_TIERS),
    get_multiplier(latest["PuellMultiple"], PUELL_TIERS),
    get_multiplier(latest["NVTSignal"],     NVT_TIERS),
]
comb = np.mean([m for m in ms if not np.isnan(m)])
print(f"\n  Combined DCA multiplier this week: {comb:.2f}x  (${BASE_AMOUNT * comb:.0f})")
print("=" * 60)
