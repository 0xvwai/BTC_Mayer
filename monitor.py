import os
import datetime
import requests
import ccxt
import numpy as np
import pandas as pd

TOKEN    = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID  = os.environ.get('TELEGRAM_CHAT_ID')
CM_BASE  = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
CM_HDR   = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

# ── Strategy constants ────────────────────────────────────────────────────────
BASE_WEEKLY_DCA = 250   # your neutral weekly DCA amount in USD

# Option A weights
#   MVRV: 32%  |  AHR999: 32%  |  Miner: 21%  |  F&G: 11%  |  Mayer: 5%
W_MVRV   = 1.50
W_AHR999 = 1.50
W_MINER  = 1.00
W_FNG    = 0.50
W_MAYER  = 0.25
MAX_SCORE = (4*W_MVRV) + (4*W_AHR999) + (4*W_MINER) + (4*W_FNG) + (4*W_MAYER)  # 19.0


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text):
    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


# ── CoinMetrics generic fetcher ───────────────────────────────────────────────

def cm_fetch(metric, page_size=10):
    """Fetch the most recent non-null value for a single CoinMetrics metric."""
    url = f"{CM_BASE}?assets=btc&metrics={metric}&frequency=1d&page_size={page_size}"
    try:
        resp = requests.get(url, headers=CM_HDR, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for row in reversed(data):
            val = row.get(metric)
            if val is not None:
                print(f"  {metric}: {val} ({row.get('time','')[:10]})")
                return float(val), row.get("time", "")[:10]
        print(f"  {metric}: all {len(data)} rows were null")
    except Exception as e:
        print(f"  {metric}: fetch error — {e}")
    return None, None


# ── Data fetchers ─────────────────────────────────────────────────────────────

def get_price_and_mayer():
    ex    = ccxt.coinbase()
    price = float(ex.fetch_ticker("BTC/USD")["last"])
    bars  = ex.fetch_ohlcv("BTC/USD", timeframe="1d", limit=201)
    df    = pd.DataFrame(bars, columns=["t", "o", "h", "l", "c", "v"])
    ma200 = df["c"].iloc[-201:-1].mean()
    mayer = price / ma200 if ma200 else None
    return price, mayer

def get_mvrv():
    val, date = cm_fetch("CapMVRVCur")
    if val:
        return val, date
    mkt,  d1 = cm_fetch("CapMrktCurUSD")
    real, _  = cm_fetch("CapRealUSD")
    if mkt and real and real > 0:
        return mkt / real, d1
    return None, None

def _fetch_miner_blockchain_com(days=400):
    """
    Primary miner revenue source: Blockchain.com charts API.
    Returns list of (usd_value, date_str) tuples, oldest first.
    Includes both block subsidy and transaction fees.
    """
    url = (
        f"https://api.blockchain.info/charts/miners-revenue"
        f"?timespan={days}days&format=json&sampled=false"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        pts  = resp.json().get("values", [])
        rows = []
        for p in pts:
            if p.get("y") is not None:
                date_str = datetime.datetime.utcfromtimestamp(p["x"]).strftime("%Y-%m-%d")
                rows.append((float(p["y"]), date_str))
        print(f"  Miner (Blockchain.com): {len(rows)} daily rows fetched")
        return rows if len(rows) >= 30 else None
    except Exception as e:
        print(f"  Miner (Blockchain.com) error: {e}")
        return None

def _fetch_miner_mempool_space(days=400):
    """
    Fallback miner revenue source: Mempool.space API.
    Fetches daily revenue buckets (subsidy + fees) in USD.
    Returns list of (usd_value, date_str) tuples, oldest first.
    """
    # Mempool.space returns up to 1y of daily mining revenue stats
    url = "https://mempool.space/api/v1/mining/revenue/1y"
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        # Response: {"timestamps": [...], "revenue": [...]}
        timestamps = data.get("timestamps", [])
        revenues   = data.get("revenue", [])
        if not timestamps or not revenues:
            print(f"  Miner (Mempool.space): empty response")
            return None
        rows = []
        for ts, rev in zip(timestamps, revenues):
            if rev is not None:
                date_str = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
                rows.append((float(rev), date_str))
        print(f"  Miner (Mempool.space): {len(rows)} daily rows fetched")
        return rows if len(rows) >= 30 else None
    except Exception as e:
        print(f"  Miner (Mempool.space) error: {e}")
        return None

def _compute_miner_ratio(rows, source_label):
    """
    Shared normalisation logic: given list of (usd_value, date_str) tuples,
    compute ratio = today_rev / 365d_MA (look-ahead free).
    Returns (ratio, today_rev, ma365, today_date) or (None, None, None, None).
    """
    values     = [v for v, _ in rows]
    today_rev  = values[-1]
    today_date = rows[-1][1]

    window = min(365, len(values) - 1)
    ma365  = float(np.mean(values[-window - 1:-1]))

    if ma365 <= 0:
        print(f"  Miner ({source_label}): MA365 is zero, cannot compute ratio")
        return None, None, None, None

    ratio = today_rev / ma365
    print(
        f"  Miner ({source_label}): ${today_rev:,.0f} | "
        f"MA365: ${ma365:,.0f} | "
        f"Ratio: {ratio:.3f} ({today_date})"
    )
    return ratio, today_rev, ma365, today_date

def get_miner_revenue():
    """
    Fetch total miner revenue (subsidy + fees) and compute a halving-agnostic
    ratio vs its own 365-day MA for scoring.

    Source priority:
      1. Blockchain.com charts API  (primary)
      2. Mempool.space API          (fallback)

    Returns (ratio, today_rev, ma365, date, source_label) where:
      ratio       = today_rev / 365d_MA  — the value that is scored
      today_rev   = raw USD revenue      — shown in report for context
      ma365       = trailing 365d MA     — shown in report for context
      source_label = which source was used
    """
    rows = _fetch_miner_blockchain_com()
    source = "Blockchain.com"

    if rows is None:
        print("  Miner: Blockchain.com failed — trying Mempool.space...")
        rows = _fetch_miner_mempool_space()
        source = "Mempool.space"

    if rows is None:
        print("  Miner: all sources failed")
        return None, None, None, None, None

    ratio, today_rev, ma365, today_date = _compute_miner_ratio(rows, source)
    return ratio, today_rev, ma365, today_date, source

def get_fear_and_greed():
    try:
        resp  = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        resp.raise_for_status()
        entry = resp.json()["data"][0]
        value = int(entry["value"])
        label = entry["value_classification"]
        print(f"  Fear & Greed: {value} ({label})")
        return value, label
    except Exception as e:
        print(f"  Fear & Greed: fetch error — {e}")
        return None, None


# ── AHR999 helpers ────────────────────────────────────────────────────────────
# AHR999 = (price / exp_regression_price) × (price / 730d_MA)
# Primary source: CoinMetrics PriceUSD | Fallback: CoinGecko

def _fetch_prices_coinmetrics(days=1500):
    url = (
        f"{CM_BASE}?assets=btc&metrics=PriceUSD"
        f"&frequency=1d&page_size={days}"
    )
    try:
        resp = requests.get(url, headers=CM_HDR, timeout=20)
        resp.raise_for_status()
        data   = resp.json().get("data", [])
        prices = [
            float(r["PriceUSD"])
            for r in data
            if r.get("PriceUSD") is not None
        ]
        print(f"  AHR999 (CoinMetrics): {len(prices)} daily prices fetched")
        return prices if len(prices) >= 730 else None
    except Exception as e:
        print(f"  AHR999 CoinMetrics error: {e}")
        return None

def _fetch_prices_coingecko(days=1500):
    url = (
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
        f"?vs_currency=usd&days={days}&interval=daily"
    )
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        raw    = resp.json().get("prices", [])
        prices = [p[1] for p in raw if p[1] is not None]
        print(f"  AHR999 (CoinGecko): {len(prices)} daily prices fetched")
        return prices if len(prices) >= 730 else None
    except Exception as e:
        print(f"  AHR999 CoinGecko error: {e}")
        return None

def get_ahr999(price):
    """
    AHR999 = (price / exp_regression_price) × (price / 730d_MA)
    Primary: CoinMetrics | Fallback: CoinGecko
    """
    prices = _fetch_prices_coinmetrics()
    source = "CoinMetrics"
    if prices is None:
        print("  AHR999: CoinMetrics insufficient — trying CoinGecko...")
        prices = _fetch_prices_coingecko()
        source = "CoinGecko"

    if prices is None:
        print("  AHR999: all sources failed or insufficient data")
        return None, None

    prices = np.array(prices, dtype=float)

    # 730-day MA (2-year)
    ma730 = float(np.mean(prices[-730:]))

    # Exponential regression over all available history
    n      = len(prices)
    x      = np.arange(n, dtype=float)
    log_p  = np.log(prices)
    coeffs = np.polyfit(x, log_p, 1)          # [slope, intercept]
    # Predict for index n (today's live price, one step beyond last close)
    exp_price = float(np.exp(coeffs[0] * n + coeffs[1]))

    ahr999 = (price / exp_price) * (price / ma730)
    print(
        f"  AHR999: {ahr999:.4f} "
        f"(exp_price={exp_price:,.0f}, ma730={ma730:,.0f}, src={source})"
    )
    return round(ahr999, 4), source


# ── Individual scorers (0–4 pts each) ────────────────────────────────────────

def score_mvrv(v):
    """MVRV Ratio — weight 1.5× (32%). On-chain cost basis vs market cap."""
    if v is None: return None, "N/A", "?"
    if v < 1.0:   return 4, "Extreme undervalue  — Accumulate aggressively", "DIAMOND"
    if v < 1.5:   return 3, "Undervalue          — Accumulate", "GREEN"
    if v < 2.5:   return 2, "Fair value          — Standard DCA", "CHECK"
    if v < 3.5:   return 1, "Overvalue           — Reduce", "YELLOW"
    return               0, "Extreme overvalue   — Minimise", "RED"

def score_ahr999(v):
    """AHR999 — weight 1.5× (32%). Exp. growth trend × 2yr MA composite."""
    if v is None: return None, "N/A", "?"
    if v < 0.45:  return 4, "Deep undervalue     — Accumulate aggressively", "DIAMOND"
    if v < 1.0:   return 3, "Undervalue          — Accumulate", "GREEN"
    if v < 1.5:   return 2, "Fair value          — Standard DCA", "CHECK"
    if v < 2.5:   return 1, "Overvalue           — Reduce", "YELLOW"
    return               0, "Extreme overvalue   — Minimise", "RED"

def score_miner(v):
    """
    Miner Revenue Ratio (RevUSD / 365d MA) — weight 1.0× (21%).
    Halving-agnostic: 1.0 = average health regardless of subsidy era.
    """
    if v is None: return None, "N/A", "?"
    if v < 0.50:  return 4, "Severe distress     — Accumulate aggressively", "DIAMOND"
    if v < 0.85:  return 3, "Below-avg revenue   — Accumulate", "GREEN"
    if v < 1.25:  return 2, "Normal revenue      — Standard DCA", "CHECK"
    if v < 1.75:  return 1, "Elevated revenue    — Reduce", "YELLOW"
    return               0, "Peak revenue        — Minimise", "RED"

def score_fng(v):
    """Fear & Greed Index — weight 0.5× (11%). Short-term sentiment signal."""
    if v is None: return None, "N/A", "?"
    if v <= 20:   return 4, "Extreme Fear        — Accumulate aggressively", "DIAMOND"
    if v <= 40:   return 3, "Fear                — Accumulate", "GREEN"
    if v <= 55:   return 2, "Neutral             — Standard DCA", "CHECK"
    if v <= 75:   return 1, "Greed               — Reduce", "YELLOW"
    return               0, "Extreme Greed       — Minimise", "RED"

def score_mayer(v):
    """Mayer Multiple (Price / 200DMA) — weight 0.25× (5%). Trend deviation."""
    if v is None: return None, "N/A", "?"
    if v < 0.80:  return 4, "Deep below 200DMA   — Accumulate aggressively", "DIAMOND"
    if v < 1.00:  return 3, "Below 200DMA        — Accumulate", "GREEN"
    if v < 1.30:  return 2, "Near 200DMA         — Standard DCA", "CHECK"
    if v < 1.50:  return 1, "Above 200DMA        — Reduce", "YELLOW"
    return               0, "Significantly above — Minimise", "RED"

ICONS = {
    "DIAMOND": "💎", "GREEN": "🟢", "CHECK": "✅",
    "YELLOW": "🟡", "RED": "🚨", "?": "❓"
}


# ── Composite score ───────────────────────────────────────────────────────────

def composite_score(mvrv_raw, ahr999_raw, miner_raw, fng_raw, mayer_raw):
    """
    Weighted composite out of 19.  Missing indicators are excluded and
    the score is renormalised so gaps do not deflate the result.
    """
    score     = 0.0
    max_avail = 0.0
    pairs = [
        (mvrv_raw,   W_MVRV,   "MVRV"),
        (ahr999_raw, W_AHR999, "AHR999"),
        (miner_raw,  W_MINER,  "Miner"),
        (fng_raw,    W_FNG,    "F&G"),
        (mayer_raw,  W_MAYER,  "Mayer"),
    ]
    for raw, weight, name in pairs:
        if raw is not None:
            score     += raw * weight
            max_avail += 4  * weight
        else:
            print(f"  WARNING: {name} unavailable — excluded from composite")

    if max_avail == 0:
        return None, None

    normalised = score / max_avail * MAX_SCORE
    pct        = score / max_avail * 100
    return round(normalised, 2), round(pct, 1)


# ── DCA decision (0.25x – 2x range) ─────────────────────────────────────────

def dca_decision(score, base=BASE_WEEKLY_DCA):
    """
    Maps composite score (0-19) to multiplier and dollar amount.
    Range: 0.25x - 2x  (neutral = 1x = $250/week)
    """
    if score is None:
        return None, None, "WARNING: Insufficient data", "N/A"

    if score >= 16:
        mult, action = 2.00, "STRONG ACCUMULATE"
    elif score >= 12:
        mult, action = 1.50, "ACCUMULATE"
    elif score >= 6:
        mult, action = 1.00, "NEUTRAL"
    elif score >= 2:
        mult, action = 0.50, "REDUCE"
    else:
        mult, action = 0.25, "MINIMISE"

    dollar     = base * mult
    mult_label = f"{mult:.2f}x  ->  ${dollar:,.2f} this week"
    return mult, dollar, action, mult_label


# ── Helpers ───────────────────────────────────────────────────────────────────

def score_bar(raw, max_raw=4):
    if raw is None: return "░░░░"
    filled = int(round(raw))
    return "█" * filled + "░" * (max_raw - filled)

def weighted_pts(raw, weight):
    return f"{raw * weight:.2f}" if raw is not None else "N/A"

def action_icon(action):
    mapping = {
        "STRONG ACCUMULATE": "💎",
        "ACCUMULATE":        "🟢",
        "NEUTRAL":           "✅",
        "REDUCE":            "🟡",
        "MINIMISE":          "🚨",
    }
    return mapping.get(action, "❓")


# ── Report ────────────────────────────────────────────────────────────────────

def build_report(
    price, mayer,
    mvrv, mvrv_date,
    ahr999, ahr999_src,
    miner_ratio, miner_rev, miner_ma365, miner_date, miner_src,
    fng, fng_label,
):
    f2s = lambda v: f"{v:,.2f}"  if v is not None else "N/A"
    f4  = lambda v: f"{v:,.4f}"  if v is not None else "N/A"
    f3  = lambda v: f"{v:,.3f}"  if v is not None else "N/A"
    f0  = lambda v: f"${v:,.0f}" if v is not None else "N/A"
    dtg = lambda d: f" _({d})_"  if d else ""

    mvrv_pts,   mvrv_lbl,   mvrv_ico   = score_mvrv(mvrv)
    ahr999_pts, ahr999_lbl, ahr999_ico = score_ahr999(ahr999)
    miner_pts,  miner_lbl,  miner_ico  = score_miner(miner_ratio)
    fng_pts,    fng_lbl2,   fng_ico    = score_fng(fng)
    mayer_pts,  mayer_lbl,  mayer_ico  = score_mayer(mayer)

    comp_score, comp_pct             = composite_score(mvrv_pts, ahr999_pts, miner_pts, fng_pts, mayer_pts)
    mult, dollar, action, mult_label = dca_decision(comp_score)

    fng_str   = f"{fng} — {fng_label}" if fng is not None else "N/A"
    comp_str  = f"{comp_score:.1f} / 19.0  ({comp_pct}%)" if comp_score is not None else "N/A"
    ahr_src   = f" _[{ahr999_src}]_"  if ahr999_src  else ""
    miner_src_str = f" _[{miner_src}]_" if miner_src else ""

    # Miner: show ratio + raw context on same line
    miner_val_str = (
        f"`{f3(miner_ratio)}`  _(today {f0(miner_rev)} vs MA365 {f0(miner_ma365)})_"
        if miner_ratio is not None else "`N/A`"
    )

    ico = lambda k: ICONS.get(k, "❓")

    return (
        f"📊 *BTC DCA Monitor — Composite Signal*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 BTC Price: `${price:,.0f}`\n\n"

        f"*INDICATORS  (raw pts × weight = contribution)*\n\n"

        # ── MVRV ──────────────────────────────────────────────────────────────
        f"⛓️ *MVRV Ratio*{dtg(mvrv_date)}  _(weight 1.5× — 32%)_\n"
        f"Value: `{f2s(mvrv)}`  |  Score: `{score_bar(mvrv_pts)} {mvrv_pts}/4`  |  Pts: `{weighted_pts(mvrv_pts, W_MVRV)}/6.00`\n"
        f"Signal: {ico(mvrv_ico)} _{mvrv_lbl}_\n"
        f"```\n"
        f"< 1.0      💎 4 pts  Extreme undervalue\n"
        f"1.0-1.5    🟢 3 pts  Undervalue\n"
        f"1.5-2.5    ✅ 2 pts  Fair value\n"
        f"2.5-3.5    🟡 1 pt   Overvalue\n"
        f"> 3.5      🚨 0 pts  Extreme overvalue\n"
        f"```\n\n"

        # ── AHR999 ────────────────────────────────────────────────────────────
        f"🔭 *AHR999*{ahr_src}  _(weight 1.5× — 32%)_\n"
        f"Value: `{f4(ahr999)}`  |  Score: `{score_bar(ahr999_pts)} {ahr999_pts}/4`  |  Pts: `{weighted_pts(ahr999_pts, W_AHR999)}/6.00`\n"
        f"Signal: {ico(ahr999_ico)} _{ahr999_lbl}_\n"
        f"```\n"
        f"< 0.45     💎 4 pts  Deep undervalue\n"
        f"0.45-1.00  🟢 3 pts  Undervalue\n"
        f"1.00-1.50  ✅ 2 pts  Fair value\n"
        f"1.50-2.50  🟡 1 pt   Overvalue\n"
        f"> 2.50     🚨 0 pts  Extreme overvalue\n"
        f"```\n"
        f"_Formula: (price/exp-regression) × (price/730d-MA)_\n\n"

        # ── Miner Revenue ─────────────────────────────────────────────────────
        f"⛏️ *Miner Revenue Ratio*{dtg(miner_date)}{miner_src_str}  _(weight 1.0× — 21%)_\n"
        f"Ratio: {miner_val_str}\n"
        f"Score: `{score_bar(miner_pts)} {miner_pts}/4`  |  Pts: `{weighted_pts(miner_pts, W_MINER)}/4.00`\n"
        f"Signal: {ico(miner_ico)} _{miner_lbl}_\n"
        f"```\n"
        f"< 0.50     💎 4 pts  Severe distress\n"
        f"0.50-0.85  🟢 3 pts  Below-avg revenue\n"
        f"0.85-1.25  ✅ 2 pts  Normal revenue\n"
        f"1.25-1.75  🟡 1 pt   Elevated revenue\n"
        f"> 1.75     🚨 0 pts  Peak revenue\n"
        f"```\n"
        f"_Revenue (subsidy + fees) / 365d-MA — halving-agnostic_\n\n"

        # ── Fear & Greed ──────────────────────────────────────────────────────
        f"😨 *Fear & Greed Index*  _(weight 0.5× — 11%)_\n"
        f"Value: `{fng_str}`  |  Score: `{score_bar(fng_pts)} {fng_pts}/4`  |  Pts: `{weighted_pts(fng_pts, W_FNG)}/2.00`\n"
        f"Signal: {ico(fng_ico)} _{fng_lbl2}_\n"
        f"```\n"
        f"0-20       💎 4 pts  Extreme Fear\n"
        f"21-40      🟢 3 pts  Fear\n"
        f"41-55      ✅ 2 pts  Neutral\n"
        f"56-75      🟡 1 pt   Greed\n"
        f"76-100     🚨 0 pts  Extreme Greed\n"
        f"```\n\n"

        # ── Mayer ─────────────────────────────────────────────────────────────
        f"📈 *Mayer Multiple*  _(weight 0.25× — 5%)_\n"
        f"Value: `{f2s(mayer)}`  |  Score: `{score_bar(mayer_pts)} {mayer_pts}/4`  |  Pts: `{weighted_pts(mayer_pts, W_MAYER)}/1.00`\n"
        f"Signal: {ico(mayer_ico)} _{mayer_lbl}_\n"
        f"```\n"
        f"< 0.80     💎 4 pts  Deep below 200DMA\n"
        f"0.80-1.00  🟢 3 pts  Below 200DMA\n"
        f"1.00-1.30  ✅ 2 pts  Near 200DMA\n"
        f"1.30-1.50  🟡 1 pt   Above 200DMA\n"
        f"> 1.50     🚨 0 pts  Significantly above\n"
        f"```\n\n"

        # ── Composite + DCA ───────────────────────────────────────────────────
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧮 *COMPOSITE SCORE*\n"
        f"`{comp_str}`\n\n"

        f"*DCA DECISION: {action_icon(action)} {action}*\n"
        f"➡️  `{mult_label}`\n\n"

        f"```\n"
        f"Score 16-19  💎 2.00x  $500   Strong Accumulate\n"
        f"Score 12-15  🟢 1.50x  $375   Accumulate\n"
        f"Score  6-11  ✅ 1.00x  $250   Neutral  <- base\n"
        f"Score  2-5   🟡 0.50x  $125   Reduce\n"
        f"Score  0-1   🚨 0.25x  $ 63   Minimise\n"
        f"```\n"
        f"_Weights: MVRV 32% | AHR999 32% | Miner 21% | F&G 11% | Mayer 5%_\n"
        f"_Range 0.25x-2x | Base ${BASE_WEEKLY_DCA}/wk_\n"
        f"_Miner: Blockchain.com/Mempool.space (subsidy+fees)/365d-MA | AHR999: 2yr-MA + exp regression_"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def run_monitor():
    print("Starting BTC DCA monitor...")
    try:
        price, mayer                            = get_price_and_mayer()
        mvrv,  mvrv_date                        = get_mvrv()
        ahr999, ahr999_src                      = get_ahr999(price)
        miner_ratio, miner_rev, miner_ma365, \
            miner_date, miner_src               = get_miner_revenue()
        fng,   fng_label                        = get_fear_and_greed()

        report = build_report(
            price, mayer,
            mvrv,  mvrv_date,
            ahr999, ahr999_src,
            miner_ratio, miner_rev, miner_ma365, miner_date, miner_src,
            fng,   fng_label,
        )
        print(report)
        send_telegram(report)

    except Exception as e:
        msg = f"❌ Monitor error: `{str(e)}`"
        print(msg)
        send_telegram(msg)

if __name__ == "__main__":
    run_monitor()
