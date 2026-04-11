import ccxt
import pandas as pd
import requests
import os

# 從 GitHub Secrets 讀取資訊
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code != 200:
            print(f"Telegram Error: {response.text}")
    except Exception as e:
        print(f"Network Error: {e}")

def get_mvrv_data(current_price):
    """從 Blockchain.info 獲取 Realized Price 並計算 MVRV"""
    try:
        # 獲取比特幣的 Realized Price (已實現價格，即全網平均持倉成本)
        url = "https://api.blockchain.info/charts/realized-price?timespan=5days&format=json"
        res = requests.get(url, timeout=10).json()
        # 獲取最後一個有效數據點的 'y' 值
        realized_price = res['values'][-1]['y']
        
        mvrv_ratio = current_price / realized_price
        return mvrv_ratio, realized_price
    except Exception as e:
        print(f"MVRV 數據抓取失敗: {e}")
        return None, None

def run_monitor():
    try:
        # 1. 獲取價格與計算 Mayer Multiple (M值)
        exchange = ccxt.coinbase()
        bars = exchange.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        curr_price = df['close'].iloc[-1]
        ma200 = df['close'].tail(200).mean()
        m_value = curr_price / ma200
        
        # 2. 獲取 MVRV 數據
        mvrv_value, realized_price = get_mvrv_data(curr_price)

        # 3. 指標診斷邏輯
        # Mayer Multiple 診斷
        if m_value < 0.8: m_status = "📉 低估 (適合加碼)"
        elif m_value > 1.8: m_status = "📈 過熱 (建議減碼)"
        else: m_status = "✅ 正常 (穩定定投)"

        # MVRV 診斷
        if mvrv_value:
            if mvrv_value < 1.0: mv_status = "💎 極度低估 (歷史底部)"
            elif mvrv_value < 1.2: mv_status = "🛒 價值區 (高性價比)"
            elif mvrv_value > 3.2: mv_status = "🚨 泡沫區 (高度風險)"
            elif mvrv_value > 2.4: mv_status = "⚠️ 過熱區 (獲利回吐風險)"
            else: mv_status = "✅ 健康 (持有/定投)"
        else:
            mv_status = "N/A"

        # 4. 構建報告
        report = (
            f"📊 *BTC 雙指標審計報告*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `${curr_price:,.0f}`\n"
            f"🏠 平均成本: `${realized_price:,.0f}`\n\n"
            f"📈 *Mayer Multiple (M值)*\n"
            f"數值: `{m_value:.2f}`\n"
            f"診斷: {m_status}\n\n"
            f"⛓️ *MVRV Ratio (鏈上盈虧)*\n"
            f"數值: `{mvrv_value:.2f if mvrv_value else 'N/A'}`\n"
            f"診斷: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *核心操作建議*: \n"
            f"{'🚀 雙指標共振低估，強烈建議加碼！' if (m_value < 0.8 and mvrv_value and mvrv_value < 1.2) else '☕ 目前數據穩定，繼續執行基礎定投。'}"
        )
        
        send_telegram_msg(report)
        print("報告發送成功")

    except Exception as e:
        print(f"運行失敗: {e}")

if __name__ == "__main__":
    run_monitor()
