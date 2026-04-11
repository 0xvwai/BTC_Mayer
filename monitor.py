import ccxt
import pandas as pd
import requests
import os

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Fail: {e}")

def get_btc_realized_price():
    """從 Blockchain.info 獲取已實現價格 (全網平均持倉成本)"""
    try:
        # 抓取最近 7 天的已實現價格圖表
        url = "https://api.blockchain.info/charts/realized-price?timespan=7days&format=json"
        res = requests.get(url, timeout=15).json()
        # 取得最後一個有效數據點
        if 'values' in res and len(res['values']) > 0:
            return res['values'][-1]['y']
        return None
    except:
        return None

def run_monitor():
    print("🚀 啟動雙指標同步監控...")
    try:
        # 1. 獲取當前價格 (Coinbase)
        ex = ccxt.coinbase()
        ticker = ex.fetch_ticker('BTC/USD')
        curr_price = ticker.get('last')

        # 2. 計算 Mayer Multiple
        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200 = df['c'].tail(200).mean()
        m_value = curr_price / ma200 if (curr_price and ma200) else None

        # 3. 獲取已實現價格並計算 MVRV
        realized_price = get_btc_realized_price()
        mvrv_value = curr_price / realized_price if (curr_price and realized_price) else None

        # --- 格式化處理 ---
        p_str = f"${curr_price:,.0f}" if curr_price else "N/A"
        rp_str = f"${realized_price:,.0f}" if realized_price else "N/A"
        m_str = f"{m_value:.2f}" if m_value else "N/A"
        mv_str = f"{mvrv_value:.2f}" if mvrv_value else "N/A"

        # 狀態診斷
        m_status = "📉 低估" if m_value and m_value < 0.8 else ("📈 過熱" if m_value and m_value > 1.8 else "✅ 正常")
        mv_status = "💎 底部" if mvrv_value and mvrv_value < 1.0 else ("🚨 頂部" if mvrv_value and mvrv_value > 3.0 else "✅ 健康")

        report = (
            f"📊 *BTC 雙指標審計報告*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前市價: `{p_str}`\n"
            f"🏠 平均成本: `{rp_str}`\n\n"
            f"📈 *Mayer Multiple*\n"
            f"數值: `{m_str}` ({m_status})\n\n"
            f"⛓️ *MVRV Ratio*\n"
            f"數值: `{mv_str}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *操作建議*: \n"
            f"{'🚀 雙指標共振低估，強烈建議加碼！' if (m_value and m_value < 0.8 and mvrv_value and mvrv_value < 1.2) else '☕ 目前數據穩定，繼續執行基礎定投。'}"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 系統錯誤: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
