import ccxt
import pandas as pd
import requests
import os

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# ─── CoinMetrics Community API ──────────────────────────────────────────────
# Free, no API key required. Docs: https://gitbook-docs.coinmetrics.io
COINMETRICS_BASE = "https://community-api.coinmetrics.io/v4"

# ─── Telegram ───────────────────────────────────────────────────────────────

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# ─── MVRV Retrieval ─────────────────────────────────────────────────────────

def fetch_coinmetrics(metrics: str) -> dict | None:
    """
    Fetch one or more metrics for BTC from CoinMetrics Community API.
    Returns the latest data row as a dict, or None on failure.
    Metric IDs: CapMVRVCur (MVRV), CapMrktCurUSD (Market Cap), CapRealUSD (Realized Cap)
    """
    url = (
        f"{COINMETRICS_BASE}/timeseries/asset-metrics"
        f"?assets=btc&metrics={metrics}&frequency=1d&page_size=3"
    )
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        # data is sorted oldest-first; take the last non-null entry
        for row in reversed(data):
            if all(row.get(m) is not None for m in metrics.split(",")):
                return row
        return None
    except Exception as e:
        print(f"CoinMetrics fetch error ({metrics}): {e}")
        return None


def get_mvrv() -> tuple[float | None, str]:
    """
    Attempt to retrieve Bitcoin MVRV via two strategies:
      A) Direct CapMVRVCur metric from CoinMetrics (pre-computed ratio)
      B) Compute manually: CapMrktCurUSD / CapRealUSD from CoinMetrics

    Returns (mvrv_value, source_label) or (None, error_summary).
    """
    errors = []

    # ── Strategy A: direct MVRV ratio ────────────────────────────────────────
    row = fetch_coinmetrics("CapMVRVCur")
    if row and row.get("CapMVRVCur"):
        mvrv = float(row["CapMVRVCur"])
        date = row.get("time", "")[:10]
        return mvrv, f"CoinMetrics CapMVRVCur ({date})"
    errors.append("A: CapMVRVCur unavailable")

    # ── Strategy B: manual ratio from MarketCap / RealizedCap ────────────────
    row = fetch_coinmetrics("CapMrktCurUSD,CapRealUSD")
    if row:
        mkt  = row.get("CapMrktCurUSD")
        real = row.get("CapRealUSD")
        if mkt and real and float(real) > 0:
            mvrv = float(mkt) / float(real)
            date = row.get("time", "")[:10]
            return mvrv, f"CoinMetrics MktCap/RealCap ({date})"
    errors.append("B: CapMrktCurUSD / CapRealUSD unavailable")

    return None, " | ".join(errors)

# ─── Main Monitor ───────────────────────────────────────────────────────────

def run_monitor():
    print("🚀 Starting BTC monitor...")

    try:
        # ── 1. Price & Mayer Multiple (200-day MA) via CCXT ──────────────────
        ex = ccxt.coinbase()
        ticker     = ex.fetch_ticker('BTC/USD')
        curr_price = float(ticker['last'])

        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df   = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200   = df['c'].tail(200).mean() if len(df) >= 200 else None
        m_value = curr_price / ma200 if ma200 else None

        # ── 2. MVRV via CoinMetrics ───────────────────────────────────────────
        mvrv_value, source_info = get_mvrv()

        # ── 3. DCA signal logic ───────────────────────────────────────────────
        # Mayer Multiple thresholds
        if m_value is None:
            m_status = "❓ N/A"
        elif m_value < 0.8:
            m_status = "📉 Undervalued (strong DCA zone)"
        elif m_value < 1.0:
            m_status = "🟢 Below MA200 (DCA zone)"
        elif m_value > 2.4:
            m_status = "🚨 Overheated (reduce DCA)"
        elif m_value > 1.8:
            m_status = "📈 Elevated (caution)"
        else:
            m_status = "✅ Normal"

        # MVRV thresholds (classic: <1 = bottom, 1-2 = accumulation, 2-3 = caution, >3 = top)
        if mvrv_value is None:
            mv_status = "❓ N/A"
        elif mvrv_value < 1.0:
            mv_status = "💎 Historical bottom (aggressive DCA)"
        elif mvrv_value < 2.0:
            mv_status = "🟢 Accumulation zone (DCA)"
        elif mvrv_value < 3.0:
            mv_status = "🟡 Caution (reduce DCA size)"
        else:
            mv_status = "🚨 Near cycle top (pause DCA)"

        # ── 4. Format report ──────────────────────────────────────────────────
        p_str  = f"${curr_price:,.0f}"
        m_str  = f"{m_value:.2f}"  if m_value  else "N/A"
        mv_str = f"{mvrv_value:.2f}" if mvrv_value else "N/A"

        report = (
            f"📊 *BTC DCA Monitor*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Price: `{p_str}`\n\n"
            f"📈 *Mayer Multiple* (Price / MA200)\n"
            f"Value: `{m_str}`\n"
            f"Signal: {m_status}\n\n"
            f"⛓️ *MVRV Ratio* (Market Cap / Realized Cap)\n"
            f"Value: `{mv_str}`\n"
            f"Signal: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"🔍 Source: `{source_info}`"
        )

        print(report)
        send_telegram_msg(report)

    except Exception as e:
        err_msg = f"❌ Monitor error: `{str(e)}`"
        print(err_msg)
        send_telegram_msg(err_msg)


if __name__ == "__main__":
    run_monitor()
