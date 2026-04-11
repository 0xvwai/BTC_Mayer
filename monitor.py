import ccxt
import pandas as pd
import requests
import os

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# 模擬瀏覽器標頭，防止被雲端防火牆攔截
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

def get_btc_metrics():
    """多重數據源備援機制"""
    # 方案 A: CoinMetrics (目前最穩)
    try:
        url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMrktCurUSD,CapRealUSD&limit=1"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        data = res['data'][0]
        m_cap = float(data['CapMrktCurUSD'])
        r_cap = float(data['CapRealUSD'])
        return m_cap / r_cap, m_cap
    except:
        print("CoinMetrics failed, trying fallback...")

    # 方案 B: Blockchain.info
    try:
        url = "https://api.blockchain.info/charts/realized-price?timespan=1days&format=json"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        r_price = res['values'][-1]['y']
        # 需配合當前市價計算
        ex = ccxt.coinbase()
        price = ex.fetch_ticker('BTC/USD')['last']
        return price / r_price, None
    except:
        return None, None

def run_monitor():
    print("🚀 啟動終極備援監控...")
    try:
        # 1. 獲取 MVRV
        mvrv_value, m_cap = get_btc_metrics()

        # 2. 獲取價格與 200MA (Mayer Multiple)
        ex = ccxt.coinbase()
        ticker = ex.fetch_ticker('BTC/USD')
        curr_price = ticker['last']
        
        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200 = df['c'].tail(200).mean()
        m_value = curr_price / ma200 if ma200 else None

        # --- 格式化 ---
        p_str = f"${curr_price:,.0f}" if curr_price else "N/A"
        m_str = f"{m_value:.2f}" if m_value else "N/A"
        mv_str = f"{mvrv_value:.2f}" if mvrv_value else "N/A"

        # 狀態診斷
        m_status = "📉 低估" if m_value and m_value < 0.8 else ("📈 過熱" if m_value and m_value > 1.8 else "✅ 正常")
        mv_status = "💎 底部" if mvrv_value and mvrv_value < 1.0 else ("🚨 頂部" if mvrv_value and mvrv_value > 3.0 else "✅ 健康")

        report = (
            f"📊 *BTC 雙指標終極報告*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前市價: `{p_str}`\n\n"
            f"📈 *Mayer Multiple*\n"
            f"數值: `{m_str}` ({m_status})\n\n"
            f"⛓️ *MVRV Ratio*\n"
            f"數值: `{mv_str}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *核心建議*: \n"
            f"{'🚀 雙指標共振低估，這是歷史級買點！' if (m_value and m_value < 0.8 and mvrv_value and mvrv_value < 1.2) else '☕ 數據穩定，維持定投紀律。'}"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 嚴重錯誤: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
