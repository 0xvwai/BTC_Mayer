import os
import time
import datetime
import requests
import ccxt
import numpy as np
import pandas as pd

TOKEN   = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
CM_BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
CM_HDR  = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

# ──Strategy constants─────────────────────────────────────────────────────────
BASE_BTC = 250   # neutral weekly DCA — BTC ($)
BASE_ETH = 125   # neutral weekly DCA — ETH ($ — 50% of BTC)

# BTC weights  →  MVRV 28% | AHR999 28% | Miner 22% | F&G 17% | Mayer 6%
W_BTC_MVRV   = 1.25
W_BTC_AHR999 = 1.25
W_BTC_MINER  = 1.00
W_BTC_FNG    = 0.75
W_BTC_MAYER  = 0.25
MAX_BTC = (4*W_BTC_MVRV) + (4*W_BTC_AHR999) + (4*W_BTC_MINER) + (4*W_BTC_FNG) + (4*W_BTC_MAYER)  # 18.0

# ETH weights  →  MVRV 44% | AHR999 31% | F&G 19% | Mayer 6%
W_ETH_MVRV   = 1.75
W_ETH_AHR999 = 1.25
W_ETH_FNG    = 0.75
W_ETH_MAYER  = 0.25
MAX_ETH = (4*W_ETH_MVRV) + (4*W_ETH_AHR999) + (4*W_ETH_FNG) + (4*W_ETH_MAYER)  # 16.0


# ── Telegram ───────────────────────────────────────────────────────────────────

def send_telegram(text, parse_mode="Markdown"):
    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode}
    try:
        resp   = requests.post(url, data=payload, timeout=10)
        result = resp.json()
        if not result.get("ok"):
            print(f"Telegram API error: {result.get('description', 'unknown')}")
            print(f"  Error code: {result.get('error_code')}")
        return result.get("ok", False)
    except Exception as e:
        print(f"Telegram error: {e}")
        return False



# ── Scoring reference table (module-level constant — sent as a separate message)
# ── Scoring reference table (module-level constant — sent as a separate message)
REFERENCE_TABLE = (
    "📊 *Scoring Reference*\n"
    "```\n"
    "Indicator     4pts      3pts      2pts       1pt        0pts\n"
    "--------------------------------------------------------------\n"
    "MVRV  (BTC)  <1.0    1.0-1.5   1.5-2.5   2.5-3.5    >3.5\n"
    "MVRV  (ETH)  <0.8    0.8-1.5   1.5-3.0   3.0-5.0    >5.0\n"
    "AHR999(BTC)  <0.45   0.45-1.0  1.0-1.5   1.5-2.5    >2.5\n"
    "AHR999(ETH)  <0.35   0.35-0.8  0.8-1.5   1.5-3.0    >3.0\n"
    "Miner Ratio  <0.50   0.50-0.85 0.85-1.25 1.25-1.75  >1.75\n"
    "F&G Index    <=20    21-40     41-55     56-75      76-100\n"
    "Mayer (BTC)  <0.80   0.80-1.0  1.0-1.3   1.3-1.5    >1.5\n"
    "Mayer (ETH)  <0.70   0.70-0.9  0.9-1.4   1.4-1.8    >1.8\n"
    "--------------------------------------------------------------\n"
    "DCA Mult     2.00x   1.50x     1.00x     0.50x      0.25x\n"
    f"BTC (${BASE_BTC}/wk) $500    $375      $250      $125       $63\n"
    f"ETH (${BASE_ETH}/wk) $250    $188      $125       $63       $31\n"
    "```\n"
    "_4pts=Strong Accumulate  3pts=Accumulate  2pts=Neutral_\n"
    "_1pt=Reduce  0pts=Minimise_"
)

# ── CoinMetrics generic fetcher ────────────────────────────────────────────────

def cm_fetch(metric, page_size=10, asset="btc"):
    """Fetch the most recent non-null value for a CoinMetrics metric."""
    url = f"{CM_BASE}?assets={asset}&metrics={metric}&frequency=1d&page_size={page_size}"
    try:
        resp = requests.get(url, headers=CM_HDR, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for row in reversed(data):
            val = row.get(metric)
            if val is not None:
                print(f"  [{asset.upper()}] {metric}: {val} ({row.get('time','')[:10]})")
                return float(val), row.get("time", "")[:10]
        print(f"  [{asset.upper()}] {metric}: all {len(data)} rows null")
    except Exception as e:
        print(f"  [{asset.upper()}] {metric}: error — {e}")
    return None, None


# ── Price + Mayer ──────────────────────────────────────────────────────────────

def get_price_and_mayer(pair="BTC/USD"):
    """Live price and Mayer Multiple (price / 200DMA) for any Coinbase pair."""
    ex    = ccxt.coinbase()
    price = float(ex.fetch_ticker(pair)["last"])
    bars  = ex.fetch_ohlcv(pair, timeframe="1d", limit=201)
    df    = pd.DataFrame(bars, columns=["t", "o", "h", "l", "c", "v"])
    ma200 = df["c"].iloc[-201:-1].mean()
    mayer = price / ma200 if ma200 else None
    return price, mayer


# ── MVRV ───────────────────────────────────────────────────────────────────────

def get_mvrv(asset="btc"):
    """
    Fetch MVRV for BTC or ETH from CoinMetrics.
    Falls back to CapMrktCurUSD / CapRealUSD if the direct metric is null.
    """
    val, date = cm_fetch("CapMVRVCur", asset=asset)
    if val:
        return val, date
    mkt,  d1 = cm_fetch("CapMrktCurUSD", asset=asset)
    real, _  = cm_fetch("CapRealUSD",     asset=asset)
    if mkt and real and real > 0:
        return mkt / real, d1
    return None, None


# ── AHR999 ─────────────────────────────────────────────────────────────────────
# Formula: (price / exp_regression_price) × (price / 730d_MA)
# Works for BTC and ETH. Primary: CoinMetrics | Fallback: CoinGecko

def _fetch_prices_coinmetrics(asset="btc", days=1500):
    url = f"{CM_BASE}?assets={asset}&metrics=PriceUSD&frequency=1d&page_size={days}"
    try:
        resp   = requests.get(url, headers=CM_HDR, timeout=20)
        resp.raise_for_status()
        prices = [
            float(r["PriceUSD"])
            for r in resp.json().get("data", [])
            if r.get("PriceUSD") is not None
        ]
        print(f"  AHR999 [{asset.upper()}] CM: {len(prices)} prices")
        return prices if len(prices) >= 730 else None
    except Exception as e:
        print(f"  AHR999 [{asset.upper()}] CM error: {e}")
        return None

def _fetch_prices_coingecko(coin_id="bitcoin", days=1500):
    url = (
        f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        f"?vs_currency=usd&days={days}&interval=daily"
    )
    try:
        resp   = requests.get(url, timeout=20)
        resp.raise_for_status()
        prices = [p[1] for p in resp.json().get("prices", []) if p[1] is not None]
        print(f"  AHR999 [{coin_id}] CG: {len(prices)} prices")
        return prices if len(prices) >= 730 else None
    except Exception as e:
        print(f"  AHR999 [{coin_id}] CG error: {e}")
        return None

def get_ahr999(price, asset="btc", coin_id="bitcoin"):
    """
    Compute AHR999 for BTC or ETH.
      Primary source : CoinMetrics PriceUSD
      Fallback source: CoinGecko market_chart
    """
    prices = _fetch_prices_coinmetrics(asset=asset)
    source = "CM"
    if prices is None:
        prices = _fetch_prices_coingecko(coin_id=coin_id)
        source = "CG"
    if prices is None:
        print(f"  AHR999 [{asset.upper()}]: all sources failed")
        return None, None

    prices    = np.array(prices, dtype=float)
    ma730     = float(np.mean(prices[-730:]))
    n         = len(prices)
    coeffs    = np.polyfit(np.arange(n, dtype=float), np.log(prices), 1)
    exp_price = float(np.exp(coeffs[0] * n + coeffs[1]))
    ahr999    = (price / exp_price) * (price / ma730)
    print(
        f"  AHR999 [{asset.upper()}]: {ahr999:.4f} "
        f"(exp={exp_price:,.0f}  ma730={ma730:,.0f}  src={source})"
    )
    return round(ahr999, 4), source


# ── BTC Miner Revenue ──────────────────────────────────────────────────────────
# Total revenue (subsidy + fees) normalised vs its own 365d MA.
# Halving-agnostic: ratio of 1.0 = average health in any subsidy era.
# Primary: Blockchain.com | Fallback: Mempool.space

def _fetch_miner_blockchain_com(days=400):
    url = (
        f"https://api.blockchain.info/charts/miners-revenue"
        f"?timespan={days}days&format=json&sampled=false"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        rows = [
            (
                float(p["y"]),
                datetime.datetime.utcfromtimestamp(p["x"]).strftime("%Y-%m-%d"),
            )
            for p in resp.json().get("values", [])
            if p.get("y") is not None
        ]
        print(f"  Miner [BC]: {len(rows)} rows")
        return rows if len(rows) >= 30 else None
    except Exception as e:
        print(f"  Miner [BC] error: {e}")
        return None

def _fetch_miner_mempool_space():
    try:
        resp = requests.get("https://mempool.space/api/v1/mining/revenue/1y", timeout=20)
        resp.raise_for_status()
        data       = resp.json()
        timestamps = data.get("timestamps", [])
        revenues   = data.get("revenue",    [])
        if not timestamps or not revenues:
            print("  Miner [MP]: empty response")
            return None
        rows = [
            (
                float(rev),
                datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
            )
            for ts, rev in zip(timestamps, revenues)
            if rev is not None
        ]
        print(f"  Miner [MP]: {len(rows)} rows")
        return rows if len(rows) >= 30 else None
    except Exception as e:
        print(f"  Miner [MP] error: {e}")
        return None

def _compute_miner_ratio(rows, src):
    values     = [v for v, _ in rows]
    today_rev  = values[-1]
    today_date = rows[-1][1]
    window     = min(365, len(values) - 1)
    ma365      = float(np.mean(values[-window - 1:-1]))
    if ma365 <= 0:
        return None, None, None, None
    ratio = today_rev / ma365
    print(
        f"  Miner [{src}]: ${today_rev:,.0f} | "
        f"MA365: ${ma365:,.0f} | Ratio: {ratio:.3f} ({today_date})"
    )
    return ratio, today_rev, ma365, today_date

def get_miner_revenue():
    rows = _fetch_miner_blockchain_com()
    src  = "BC"
    if rows is None:
        rows = _fetch_miner_mempool_space()
        src  = "MP"
    if rows is None:
        print("  Miner: all sources failed")
        return None, None, None, None, None
    ratio, today_rev, ma365, date = _compute_miner_ratio(rows, src)
    return ratio, today_rev, ma365, date, src


# ── Fear & Greed ───────────────────────────────────────────────────────────────

def get_fear_and_greed():
    """Crypto Fear & Greed Index — shared signal for BTC and ETH."""
    try:
        resp  = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        resp.raise_for_status()
        entry = resp.json()["data"][0]
        value = int(entry["value"])
        label = entry["value_classification"]
        print(f"  F&G: {value} ({label})")
        return value, label
    except Exception as e:
        print(f"  F&G error: {e}")
        return None, None


# ── Scorers ────────────────────────────────────────────────────────────────────

ICONS = {
    "DIAMOND": "💎", "GREEN": "🟢", "CHECK": "✅",
    "YELLOW":  "🟡", "RED":   "🚨", "?":     "❓",
}

# BTC — calibrated for BTC's volatility profile
def score_btc_mvrv(v):
    if v is None: return None, "N/A", "?"
    if v < 1.0:   return 4, "Extreme undervalue", "DIAMOND"
    if v < 1.5:   return 3, "Undervalue",         "GREEN"
    if v < 2.5:   return 2, "Fair value",          "CHECK"
    if v < 3.5:   return 1, "Overvalue",           "YELLOW"
    return               0, "Extreme overvalue",   "RED"

def score_btc_ahr999(v):
    if v is None: return None, "N/A", "?"
    if v < 0.45:  return 4, "Deep undervalue",   "DIAMOND"
    if v < 1.0:   return 3, "Undervalue",         "GREEN"
    if v < 1.5:   return 2, "Fair value",          "CHECK"
    if v < 2.5:   return 1, "Overvalue",           "YELLOW"
    return               0, "Extreme overvalue",   "RED"

def score_miner(v):
    """Miner revenue ratio (today / 365d MA). Bands centred on 1.0."""
    if v is None: return None, "N/A", "?"
    if v < 0.50:  return 4, "Severe distress",    "DIAMOND"
    if v < 0.85:  return 3, "Below-avg revenue",  "GREEN"
    if v < 1.25:  return 2, "Normal revenue",      "CHECK"
    if v < 1.75:  return 1, "Elevated revenue",    "YELLOW"
    return               0, "Peak revenue",        "RED"

def score_fng(v):
    """Crypto Fear & Greed — shared for BTC and ETH."""
    if v is None: return None, "N/A", "?"
    if v <= 20:   return 4, "Extreme Fear",  "DIAMOND"
    if v <= 40:   return 3, "Fear",          "GREEN"
    if v <= 55:   return 2, "Neutral",       "CHECK"
    if v <= 75:   return 1, "Greed",         "YELLOW"
    return               0, "Extreme Greed", "RED"

def score_btc_mayer(v):
    if v is None: return None, "N/A", "?"
    if v < 0.80:  return 4, "Deep below 200DMA",   "DIAMOND"
    if v < 1.00:  return 3, "Below 200DMA",         "GREEN"
    if v < 1.30:  return 2, "Near 200DMA",           "CHECK"
    if v < 1.50:  return 1, "Above 200DMA",          "YELLOW"
    return               0, "Far above 200DMA",      "RED"

# ETH — wider bands reflect ETH's higher volatility and deeper cycle swings
def score_eth_mvrv(v):
    """ETH MVRV: cycle peaks historically 5-7× vs BTC's 3-4×."""
    if v is None: return None, "N/A", "?"
    if v < 0.8:   return 4, "Extreme undervalue", "DIAMOND"
    if v < 1.5:   return 3, "Undervalue",         "GREEN"
    if v < 3.0:   return 2, "Fair value",          "CHECK"
    if v < 5.0:   return 1, "Overvalue",           "YELLOW"
    return               0, "Extreme overvalue",   "RED"

def score_eth_ahr999(v):
    """ETH AHR999: regression bottoms lower and peaks higher than BTC."""
    if v is None: return None, "N/A", "?"
    if v < 0.35:  return 4, "Deep undervalue",   "DIAMOND"
    if v < 0.8:   return 3, "Undervalue",         "GREEN"
    if v < 1.5:   return 2, "Fair value",          "CHECK"
    if v < 3.0:   return 1, "Overvalue",           "YELLOW"
    return               0, "Extreme overvalue",   "RED"

def score_eth_mayer(v):
    """ETH Mayer: wider deviation bands vs BTC due to higher volatility."""
    if v is None: return None, "N/A", "?"
    if v < 0.70:  return 4, "Deep below 200DMA",  "DIAMOND"
    if v < 0.90:  return 3, "Below 200DMA",        "GREEN"
    if v < 1.40:  return 2, "Near 200DMA",          "CHECK"
    if v < 1.80:  return 1, "Above 200DMA",         "YELLOW"
    return               0, "Far above 200DMA",     "RED"


# ── Composite score (generic) ──────────────────────────────────────────────────

def composite_score(pairs, max_score):
    """
    pairs     : list of (raw_pts_0-4, weight, name)
    max_score : theoretical maximum (all indicators at 4 pts)
    Missing indicators are excluded and the score is renormalised.
    """
    score = 0.0
    max_avail = 0.0
    for raw, weight, name in pairs:
        if raw is not None:
            score     += raw * weight
            max_avail += 4  * weight
        else:
            print(f"  WARNING: {name} unavailable — excluded from composite")
    if max_avail == 0:
        return None, None
    normalised = score / max_avail * max_score
    pct        = score / max_avail * 100
    return round(normalised, 2), round(pct, 1)


# ── DCA decision (generic) ────────────────────────────────────────────────────

def dca_decision(score, max_score, base):
    """
    Proportional tier breakpoints (same for any max_score):
      ≥ 83%  →  2.00x  Strong Accumulate
      ≥ 61%  →  1.50x  Accumulate
      ≥ 33%  →  1.00x  Neutral
      ≥ 11%  →  0.50x  Reduce
       < 11%  →  0.25x  Minimise
    """
    if score is None:
        return None, None, "Insufficient data", "N/A"
    pct = score / max_score
    if   pct >= 0.833: mult, action = 2.00, "STRONG ACCUMULATE"
    elif pct >= 0.611: mult, action = 1.50, "ACCUMULATE"
    elif pct >= 0.333: mult, action = 1.00, "NEUTRAL"
    elif pct >= 0.111: mult, action = 0.50, "REDUCE"
    else:              mult, action = 0.25, "MINIMISE"
    dollar = base * mult
    return mult, dollar, action, f"{mult:.2f}x  →  ${dollar:,.2f}/wk"


# ── Report helpers ─────────────────────────────────────────────────────────────

def score_bar(raw):
    if raw is None: return "░░░░"
    filled = int(round(raw))
    return "█" * filled + "░" * (4 - filled)

def action_icon(action):
    return {
        "STRONG ACCUMULATE": "💎", "ACCUMULATE": "🟢",
        "NEUTRAL": "✅", "REDUCE": "🟡", "MINIMISE": "🚨",
    }.get(action, "❓")

def ind_line(emoji, name, weight_str, val_str, pts, lbl, ico_key,
             src_tag="", extra=""):
    """
    Two-line indicator row: name/weight on line 1, value/score/signal on line 2.
    Trailing blank line gives breathing room between indicators.
    extra: optional context string shown on a sub-line (e.g. MA context).
    """
    bar   = score_bar(pts)
    pts_s = str(pts) if pts is not None else "?"
    icon  = ICONS.get(ico_key, "❓")
    src   = f" _[{src_tag}]_" if src_tag else ""
    ext   = f"\n    {extra}"  if extra    else ""
    return (
        f"{emoji} *{name}*{src} _{weight_str}_\n"
        f"    `{val_str}`{ext}  {bar} {pts_s}/4  {icon} _{lbl}_\n\n"
    )


# ── Combined report ────────────────────────────────────────────────────────────

def build_report(
    # BTC
    btc_price, btc_mayer,
    btc_mvrv,  btc_mvrv_date,
    btc_ahr999, btc_ahr999_src,
    btc_miner_ratio, btc_miner_rev, btc_miner_ma365, btc_miner_date, btc_miner_src,
    # ETH
    eth_price, eth_mayer,
    eth_mvrv,  eth_mvrv_date,
    eth_ahr999, eth_ahr999_src,
    # Shared
    fng, fng_label,
):
    # ── Score ──────────────────────────────────────────────────────────────────
    btc_mvrv_pts,  btc_mvrv_lbl,  btc_mvrv_ico  = score_btc_mvrv(btc_mvrv)
    btc_ahr_pts,   btc_ahr_lbl,   btc_ahr_ico   = score_btc_ahr999(btc_ahr999)
    btc_miner_pts, btc_miner_lbl, btc_miner_ico = score_miner(btc_miner_ratio)
    fng_pts,       fng_lbl2,      fng_ico       = score_fng(fng)
    btc_mayer_pts, btc_mayer_lbl, btc_mayer_ico = score_btc_mayer(btc_mayer)

    eth_mvrv_pts,  eth_mvrv_lbl,  eth_mvrv_ico  = score_eth_mvrv(eth_mvrv)
    eth_ahr_pts,   eth_ahr_lbl,   eth_ahr_ico   = score_eth_ahr999(eth_ahr999)
    eth_mayer_pts, eth_mayer_lbl, eth_mayer_ico = score_eth_mayer(eth_mayer)

    # ── Composite + DCA ────────────────────────────────────────────────────────
    btc_score, btc_pct = composite_score([
        (btc_mvrv_pts,  W_BTC_MVRV,   "BTC MVRV"),
        (btc_ahr_pts,   W_BTC_AHR999, "BTC AHR999"),
        (btc_miner_pts, W_BTC_MINER,  "BTC Miner"),
        (fng_pts,       W_BTC_FNG,    "F&G"),
        (btc_mayer_pts, W_BTC_MAYER,  "BTC Mayer"),
    ], MAX_BTC)

    eth_score, eth_pct = composite_score([
        (eth_mvrv_pts,  W_ETH_MVRV,   "ETH MVRV"),
        (eth_ahr_pts,   W_ETH_AHR999, "ETH AHR999"),
        (fng_pts,       W_ETH_FNG,    "F&G"),
        (eth_mayer_pts, W_ETH_MAYER,  "ETH Mayer"),
    ], MAX_ETH)

    _, _, btc_action, btc_mult = dca_decision(btc_score, MAX_BTC, BASE_BTC)
    _, _, eth_action, eth_mult = dca_decision(eth_score, MAX_ETH, BASE_ETH)

    # ── Formatters ─────────────────────────────────────────────────────────────
    f2s = lambda v: f"{v:,.2f}"  if v is not None else "N/A"
    f4  = lambda v: f"{v:,.4f}"  if v is not None else "N/A"
    f3  = lambda v: f"{v:,.3f}"  if v is not None else "N/A"
    f0  = lambda v: f"${v:,.0f}" if v is not None else "N/A"

    btc_score_str = f"{btc_score:.1f}/{MAX_BTC:.0f} ({btc_pct}%)" if btc_score is not None else "N/A"
    eth_score_str = f"{eth_score:.1f}/{MAX_ETH:.0f} ({eth_pct}%)" if eth_score is not None else "N/A"
    fng_str       = f"{fng} — {fng_label}" if fng is not None else "N/A"

    # Miner: compact context string
    miner_ctx = (
        f"_({f0(btc_miner_rev)} vs MA {f0(btc_miner_ma365)})_"
        if btc_miner_ratio is not None else ""
    )

    return (
        # ── Header ────────────────────────────────────────────────────────────
        f"📊 *BTC & ETH DCA Signal*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 BTC `${btc_price:,.0f}`  |  ETH `${eth_price:,.0f}`\n\n"

        # ── BTC ───────────────────────────────────────────────────────────────
        f"*── BITCOIN ─────────────────────*\n\n"
        + ind_line("⛓️", "MVRV",      "1.25×", f2s(btc_mvrv),       btc_mvrv_pts,  btc_mvrv_lbl,  btc_mvrv_ico)
        + ind_line("🔭", "AHR999",    "1.25×", f4(btc_ahr999),      btc_ahr_pts,   btc_ahr_lbl,   btc_ahr_ico,   btc_ahr999_src or "")
        + ind_line("⛏️", "Miner Rev", "1.0×",  f3(btc_miner_ratio), btc_miner_pts, btc_miner_lbl, btc_miner_ico, btc_miner_src or "", miner_ctx)
        + ind_line("😨", "F&G",       "0.75×", fng_str,             fng_pts,       fng_lbl2,      fng_ico)
        + ind_line("📈", "Mayer",     "0.25×", f2s(btc_mayer),      btc_mayer_pts, btc_mayer_lbl, btc_mayer_ico)
        + f"🧮 *Score* `{btc_score_str}`\n"
        + f"💡 *{btc_action}* {action_icon(btc_action)}  →  `{btc_mult}`\n\n"

        # ── ETH ───────────────────────────────────────────────────────────────
        f"*── ETHEREUM ────────────────────*\n\n"
        + ind_line("⛓️", "MVRV",   "1.75×", f2s(eth_mvrv),  eth_mvrv_pts,  eth_mvrv_lbl,  eth_mvrv_ico)
        + ind_line("🔭", "AHR999", "1.25×", f4(eth_ahr999), eth_ahr_pts,   eth_ahr_lbl,   eth_ahr_ico,  eth_ahr999_src or "")
        + ind_line("😨", "F&G",    "0.75×", fng_str,        fng_pts,       fng_lbl2,      fng_ico)
        + ind_line("📈", "Mayer",  "0.25×", f2s(eth_mayer), eth_mayer_pts, eth_mayer_lbl, eth_mayer_ico)
        + f"🧮 *Score* `{eth_score_str}`\n"
        + f"💡 *{eth_action}* {action_icon(eth_action)}  →  `{eth_mult}`\n\n"


        # ── Footer ────────────────────────────────────────────────────────────
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_BTC: MVRV 28% | AHR999 28% | Miner 22% | F&G 17% | Mayer 6%_\n"
        f"_ETH: MVRV 44% | AHR999 31% | F&G 19% | Mayer 6%_\n"
        f"_AHR999: exp-regression × 2yr-MA | Miner: (subsidy+fees) / 365d-MA_"
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def run_monitor():
    print("Starting BTC & ETH DCA monitor...")
    try:
        print("\n── BTC ──")
        btc_price, btc_mayer                      = get_price_and_mayer("BTC/USD")
        btc_mvrv,  btc_mvrv_date                  = get_mvrv("btc")
        btc_ahr999, btc_ahr999_src                = get_ahr999(btc_price, "btc", "bitcoin")
        btc_miner_ratio, btc_miner_rev,  \
            btc_miner_ma365, btc_miner_date,  \
            btc_miner_src                         = get_miner_revenue()

        print("\n── ETH ──")
        eth_price, eth_mayer                      = get_price_and_mayer("ETH/USD")
        eth_mvrv,  eth_mvrv_date                  = get_mvrv("eth")
        eth_ahr999, eth_ahr999_src                = get_ahr999(eth_price, "eth", "ethereum")

        print("\n── Shared ──")
        fng, fng_label                            = get_fear_and_greed()

        report = build_report(
            btc_price, btc_mayer,
            btc_mvrv,  btc_mvrv_date,
            btc_ahr999, btc_ahr999_src,
            btc_miner_ratio, btc_miner_rev, btc_miner_ma365, btc_miner_date, btc_miner_src,
            eth_price, eth_mayer,
            eth_mvrv,  eth_mvrv_date,
            eth_ahr999, eth_ahr999_src,
            fng, fng_label,
        )
        print(f"\n── Report ──\n{report}")
        send_telegram(report)
        # time.sleep(1)                  # uncomment to re-enable reference table
        # send_telegram(REFERENCE_TABLE)  # uncomment to re-enable reference table

    except Exception as e:
        msg = f"❌ Monitor error: `{str(e)}`"
        print(msg)
        send_telegram(msg)

if __name__ == "__main__":
    run_monitor()
