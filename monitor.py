import os
import requests
import ccxt
import pandas as pd

TOKEN   = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
CM_BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
CM_HDR  = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}

# ── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(text):
    url     = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# ── CoinMetrics — one metric per call ────────────────────────────────────────

def cm_fetch(metric, page_size=10):
    """
    Return (value, date) for a single CoinMetrics metric, or (None, None).
    page_size=10 gives a 10-day window to handle metrics that lag 2-3 days.
    """
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

# ── Indicators ────────────────────────────────────────────────────────────────

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
    # Fallback: compute from two separate calls
    mkt,  d1 = cm_fetch("CapMrktCurUSD")
    real, _  = cm_fetch("CapRealUSD")
    if mkt and real and real > 0:
        return mkt / real, d1
    return None, None

def get_puell():
    return cm_fetch("IssTotUSD")

def get_nvt():
    """
    NVT Signal = Market Cap / on-chain transaction volume.
    Both fetched separately; TxTfrValAdjUSD can lag up to 3 days
    so we use page_size=14 to widen the search window.
    """
    mkt, d1 = cm_fetch("CapMrktCurUSD")
    txv, _  = cm_fetch("TxTfrValAdjUSD", page_size=14)
    if mkt and txv and txv > 0:
        return mkt / txv, d1
    # Log which value is missing to help diagnose future N/A issues
    print(f"  NVT unavailable — CapMrktCurUSD={mkt}, TxTfrValAdjUSD={txv}")
    return None, None

# ── Signal labels ─────────────────────────────────────────────────────────────

def mayer_signal(v):
    if v is None: return "N/A"
    if v < 0.80:  return "💎 Strong DCA  (3×)"
    if v < 1.00:  return "🟢 DCA zone    (2×)"
    if v < 1.50:  return "✅ Standard    (1×)"
    if v < 2.40:  return "🟡 Reduce      (0.5×)"
    return               "🚨 Pause DCA   (0×)"

def mvrv_signal(v):
    if v is None: return "N/A"
    if v < 1.0:   return "💎 Aggressive DCA (3×)"
    if v < 1.5:   return "🟢 Double DCA     (2×)"
    if v < 2.5:   return "✅ Standard       (1×)"
    if v < 3.5:   return "🟡 Reduce         (0.5×)"
    return               "🚨 Pause DCA      (0×)"

def puell_signal(v):
    if v is None:       return "N/A"
    if v < 10_000_000:  return "💎 Miner stress — Strong DCA (3×)"
    if v < 20_000_000:  return "🟢 Below avg    — Double DCA  (2×)"
    if v < 50_000_000:  return "✅ Normal range — Standard    (1×)"
    if v < 100_000_000: return "🟡 High revenue — Reduce      (0.5×)"
    return                     "🚨 Extreme      — Pause DCA   (0×)"

def nvt_signal(v):
    if v is None: return "N/A"
    if v < 50:    return "🟢 Double DCA  (2×)"
    if v < 100:   return "✅ Standard    (1×)"
    if v < 150:   return "🟡 Reduce      (0.5×)"
    return               "🚨 Pause DCA   (0×)"

# ── Report ────────────────────────────────────────────────────────────────────

def build_report(price, mayer, mvrv, mvrv_date, puell, puell_date, nvt, nvt_date):
    f2  = lambda v: f"{v:,.2f}" if v is not None else "N/A"
    f0  = lambda v: f"${v:,.0f}" if v is not None else "N/A"
    f1  = lambda v: f"{v:,.1f}" if v is not None else "N/A"
    dtg = lambda d: f" _({d})_" if d else ""

    return (
        f"📊 *BTC DCA Monitor*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 Price: `${price:,.0f}`\n\n"

        f"📈 *Mayer Multiple* (Price / 200d MA)\n"
        f"Value: `{f2(mayer)}`  →  {mayer_signal(mayer)}\n"
        f"```\n"
        f"< 0.80     💎 Strong DCA  3×\n"
        f"0.80-1.00  🟢 DCA zone    2×\n"
        f"1.00-1.50  ✅ Standard    1×\n"
        f"1.50-2.40  🟡 Reduce      0.5×\n"
        f"> 2.40     🚨 Pause       0×\n"
        f"```\n\n"

        f"⛓️ *MVRV Ratio*{dtg(mvrv_date)}\n"
        f"Value: `{f2(mvrv)}`  →  {mvrv_signal(mvrv)}\n"
        f"```\n"
        f"< 1.0      💎 Aggressive  3×\n"
        f"1.0-1.5    🟢 Double DCA  2×\n"
        f"1.5-2.5    ✅ Standard    1×\n"
        f"2.5-3.5    🟡 Reduce      0.5×\n"
        f"> 3.5      🚨 Pause       0×\n"
        f"```\n\n"

        f"⛏️ *Miner Daily Revenue*{dtg(puell_date)}\n"
        f"Value: `{f0(puell)}`  →  {puell_signal(puell)}\n"
        f"```\n"
        f"< $10M     💎 Strong DCA  3×\n"
        f"$10-20M    🟢 Double DCA  2×\n"
        f"$20-50M    ✅ Standard    1×\n"
        f"$50-100M   🟡 Reduce      0.5×\n"
        f"> $100M    🚨 Pause       0×\n"
        f"```\n\n"

        f"🔗 *NVT Signal* (Mkt Cap / Tx Vol){dtg(nvt_date)}\n"
        f"Value: `{f1(nvt)}`  →  {nvt_signal(nvt)}\n"
        f"```\n"
        f"< 50       🟢 Double DCA  2×\n"
        f"50-100     ✅ Standard    1×\n"
        f"100-150    🟡 Reduce      0.5×\n"
        f"> 150      🚨 Pause       0×\n"
        f"```"
    )

# ── Main ──────────────────────────────────────────────────────────────────────

def run_monitor():
    print("Starting BTC monitor...")
    try:
        price, mayer      = get_price_and_mayer()
        mvrv,  mvrv_date  = get_mvrv()
        puell, puell_date = get_puell()
        nvt,   nvt_date   = get_nvt()

        report = build_report(
            price, mayer,
            mvrv,  mvrv_date,
            puell, puell_date,
            nvt,   nvt_date,
        )
        print(report)
        send_telegram(report)

    except Exception as e:
        msg = f"❌ Monitor error: `{str(e)}`"
        print(msg)
        send_telegram(msg)

if __name__ == "__main__":
    run_monitor()
