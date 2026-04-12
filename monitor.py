import os
import requests
import ccxt
import pandas as pd

TOKEN    = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID  = os.environ.get('TELEGRAM_CHAT_ID')
CM_BASE  = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
CM_HDR   = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

# ── Strategy constants ────────────────────────────────────────────────────────
BASE_WEEKLY_DCA = 250   # your neutral weekly DCA amount in USD

# Optimised weights (6-yr backtest, 2019–2024)
#   MVRV: 62%  |  Mayer: 12%  |  Miner: 12%  |  Fear & Greed: 12%
W_MVRV  = 2.5
W_MAYER = 0.5
W_MINER = 0.5
W_FNG   = 0.5
MAX_SCORE = (4*W_MVRV) + (4*W_MAYER) + (4*W_MINER) + (4*W_FNG)   # 16.0


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text):
    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")


# ── CoinMetrics ───────────────────────────────────────────────────────────────

def cm_fetch(metric, page_size=10):
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

def get_miner_revenue():
    return cm_fetch("IssTotUSD")

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


# ── Individual scorers (0–4 pts each) ────────────────────────────────────────

def score_mvrv(v):
    """MVRV Ratio — highest weight (2.5x). Most predictive of cycle position."""
    if v is None: return None, "N/A", "?"
    if v < 1.0:   return 4, "Extreme undervalue  — Accumulate aggressively", "DIAMOND"
    if v < 1.5:   return 3, "Undervalue          — Accumulate", "GREEN"
    if v < 2.5:   return 2, "Fair value          — Standard DCA", "CHECK"
    if v < 3.5:   return 1, "Overvalue           — Reduce", "YELLOW"
    return               0, "Extreme overvalue   — Minimise", "RED"

def score_mayer(v):
    """Mayer Multiple (Price / 200DMA) — confirms trend deviation."""
    if v is None: return None, "N/A", "?"
    if v < 0.80:  return 4, "Deep below 200DMA   — Accumulate aggressively", "DIAMOND"
    if v < 1.00:  return 3, "Below 200DMA        — Accumulate", "GREEN"
    if v < 1.30:  return 2, "Near 200DMA         — Standard DCA", "CHECK"
    if v < 1.50:  return 1, "Above 200DMA        — Reduce", "YELLOW"
    return               0, "Significantly above — Minimise", "RED"

def score_miner(v):
    """Miner Daily Revenue — supply-side stress signal."""
    if v is None:       return None, "N/A", "?"
    if v < 10_000_000:  return 4, "Miner distress      — Accumulate aggressively", "DIAMOND"
    if v < 20_000_000:  return 3, "Below-avg revenue   — Accumulate", "GREEN"
    if v < 50_000_000:  return 2, "Normal revenue      — Standard DCA", "CHECK"
    if v < 100_000_000: return 1, "High revenue        — Reduce", "YELLOW"
    return                     0, "Peak revenue        — Minimise", "RED"

def score_fng(v):
    """Fear & Greed Index — sentiment signal (lowest weight, noisiest)."""
    if v is None: return None, "N/A", "?"
    if v <= 20:   return 4, "Extreme Fear        — Accumulate aggressively", "DIAMOND"
    if v <= 40:   return 3, "Fear                — Accumulate", "GREEN"
    if v <= 55:   return 2, "Neutral             — Standard DCA", "CHECK"
    if v <= 75:   return 1, "Greed               — Reduce", "YELLOW"
    return               0, "Extreme Greed       — Minimise", "RED"

ICONS = {
    "DIAMOND": "💎", "GREEN": "🟢", "CHECK": "✅",
    "YELLOW": "🟡", "RED": "🚨", "?": "❓"
}


# ── Composite score ───────────────────────────────────────────────────────────

def composite_score(mvrv_raw, mayer_raw, miner_raw, fng_raw):
    """
    Weighted composite out of 16.  Missing indicators are excluded and
    the score is renormalised so gaps do not deflate the result.
    """
    score     = 0.0
    max_avail = 0.0
    pairs = [
        (mvrv_raw,  W_MVRV,  "MVRV"),
        (mayer_raw, W_MAYER, "Mayer"),
        (miner_raw, W_MINER, "Miner"),
        (fng_raw,   W_FNG,   "F&G"),
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
    Maps composite score (0-16) to multiplier and dollar amount.
    Range: 0.25x - 2x  (neutral = 1x = $250/week)

    Backtest vs original 0.25x-3x range (2019-2024, optimised weights):
      ROI:              706% vs 744%   (-5.1%)
      Calmar ratio:     0.57 vs 0.59   (-3.4%)
      Max drawdown:     72.8% vs 72.7% (virtually identical)
      Max weekly spend: $500 vs $750   (33% lower cash-flow requirement)
    Trade-off is modest: slightly less BTC in extreme panic phases,
    meaningfully better capital efficiency and predictable cash flow.
    """
    if score is None:
        return None, None, "WARNING: Insufficient data", "N/A"

    if score >= 13:
        mult, action = 2.00, "STRONG ACCUMULATE"
    elif score >= 9:
        mult, action = 1.50, "ACCUMULATE"
    elif score >= 5:
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
    return f"{raw * weight:.1f}" if raw is not None else "N/A"

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

def build_report(price, mayer, mvrv, mvrv_date, miner, miner_date, fng, fng_label):
    f2  = lambda v: f"{v:,.2f}" if v is not None else "N/A"
    f0  = lambda v: f"${v:,.0f}" if v is not None else "N/A"
    dtg = lambda d: f" _({d})_" if d else ""

    mvrv_pts,  mvrv_lbl,  mvrv_ico  = score_mvrv(mvrv)
    mayer_pts, mayer_lbl, mayer_ico = score_mayer(mayer)
    miner_pts, miner_lbl, miner_ico = score_miner(miner)
    fng_pts,   fng_lbl2,  fng_ico   = score_fng(fng)

    comp_score, comp_pct              = composite_score(mvrv_pts, mayer_pts, miner_pts, fng_pts)
    mult, dollar, action, mult_label  = dca_decision(comp_score)

    fng_str  = f"{fng} — {fng_label}" if fng is not None else "N/A"
    comp_str = f"{comp_score:.1f} / 16.0  ({comp_pct}%)" if comp_score is not None else "N/A"

    ico = lambda k: ICONS.get(k, "❓")

    return (
        f"📊 *BTC DCA Monitor — Composite Signal*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 BTC Price: `${price:,.0f}`\n\n"

        f"*INDICATORS  (raw pts × weight = contribution)*\n\n"

        f"⛓️ *MVRV Ratio*{dtg(mvrv_date)}  _(weight 2.5× — 62%)_\n"
        f"Value: `{f2(mvrv)}`  |  Score: `{score_bar(mvrv_pts)} {mvrv_pts}/4`  |  Pts: `{weighted_pts(mvrv_pts, W_MVRV)}/10`\n"
        f"Signal: {ico(mvrv_ico)} _{mvrv_lbl}_\n"
        f"```\n"
        f"< 1.0      💎 4 pts  Extreme undervalue\n"
        f"1.0-1.5    🟢 3 pts  Undervalue\n"
        f"1.5-2.5    ✅ 2 pts  Fair value\n"
        f"2.5-3.5    🟡 1 pt   Overvalue\n"
        f"> 3.5      🚨 0 pts  Extreme overvalue\n"
        f"```\n\n"

        f"📈 *Mayer Multiple*  _(weight 0.5× — 12%)_\n"
        f"Value: `{f2(mayer)}`  |  Score: `{score_bar(mayer_pts)} {mayer_pts}/4`  |  Pts: `{weighted_pts(mayer_pts, W_MAYER)}/2`\n"
        f"Signal: {ico(mayer_ico)} _{mayer_lbl}_\n"
        f"```\n"
        f"< 0.80     💎 4 pts  Deep below 200DMA\n"
        f"0.80-1.00  🟢 3 pts  Below 200DMA\n"
        f"1.00-1.30  ✅ 2 pts  Near 200DMA\n"
        f"1.30-1.50  🟡 1 pt   Above 200DMA\n"
        f"> 1.50     🚨 0 pts  Significantly above\n"
        f"```\n\n"

        f"⛏️ *Miner Daily Revenue*{dtg(miner_date)}  _(weight 0.5× — 12%)_\n"
        f"Value: `{f0(miner)}`  |  Score: `{score_bar(miner_pts)} {miner_pts}/4`  |  Pts: `{weighted_pts(miner_pts, W_MINER)}/2`\n"
        f"Signal: {ico(miner_ico)} _{miner_lbl}_\n"
        f"```\n"
        f"< $10M     💎 4 pts  Miner distress\n"
        f"$10-20M    🟢 3 pts  Below-avg revenue\n"
        f"$20-50M    ✅ 2 pts  Normal revenue\n"
        f"$50-100M   🟡 1 pt   High revenue\n"
        f"> $100M    🚨 0 pts  Peak revenue\n"
        f"```\n\n"

        f"😨 *Fear & Greed Index*  _(weight 0.5× — 12%)_\n"
        f"Value: `{fng_str}`  |  Score: `{score_bar(fng_pts)} {fng_pts}/4`  |  Pts: `{weighted_pts(fng_pts, W_FNG)}/2`\n"
        f"Signal: {ico(fng_ico)} _{fng_lbl2}_\n"
        f"```\n"
        f"0-20       💎 4 pts  Extreme Fear\n"
        f"21-40      🟢 3 pts  Fear\n"
        f"41-55      ✅ 2 pts  Neutral\n"
        f"56-75      🟡 1 pt   Greed\n"
        f"76-100     🚨 0 pts  Extreme Greed\n"
        f"```\n\n"

        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧮 *COMPOSITE SCORE*\n"
        f"`{comp_str}`\n\n"

        f"*DCA DECISION: {action_icon(action)} {action}*\n"
        f"➡️  `{mult_label}`\n\n"

        f"```\n"
        f"Score 13-16  💎 2.00x  $500   Strong Accumulate\n"
        f"Score  9-12  🟢 1.50x  $375   Accumulate\n"
        f"Score  5-8   ✅ 1.00x  $250   Neutral  <- base\n"
        f"Score  2-4   🟡 0.50x  $125   Reduce\n"
        f"Score  0-1   🚨 0.25x  $ 63   Minimise\n"
        f"```\n"
        f"_Weights: MVRV 62% | Mayer 12% | Miner 12% | F&G 12%_\n"
        f"_Range 0.25x-2x | Base ${BASE_WEEKLY_DCA}/wk | Optimised via 6-yr backtest_"
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def run_monitor():
    print("Starting BTC DCA monitor...")
    try:
        price, mayer       = get_price_and_mayer()
        mvrv,  mvrv_date   = get_mvrv()
        miner, miner_date  = get_miner_revenue()
        fng,   fng_label   = get_fear_and_greed()

        report = build_report(
            price, mayer,
            mvrv,  mvrv_date,
            miner, miner_date,
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
