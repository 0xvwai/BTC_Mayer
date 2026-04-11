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
            print(f"Telegram 發送錯誤: {response.text}")
    except Exception as e:
        print(f"網路異常: {e}")

def get_mvrv_ratio():
    """獲取比特幣 MVRV Ratio (鏈上指標)"""
    try:
        # 使用 Messari 公共 API 獲取比特幣指標
        url = "https://data.messari.io/api/v1/assets/btc/metrics"
        res = requests.get(url, timeout=10).json()
        mvrv = res['data']['marketcap']['mvrv']
        return mvrv
    except Exception as e:
        print(f"無法獲取 MVRV: {e}")
        return None

def run_monitor():
    try:
        # 1. 獲取價格與計算 Mayer Multiple (M值)
        exchange = ccxt.coinbase()
        bars = exchange.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        
        curr_price = df['close'].iloc[-1]
        ma200 = df['close'].tail(200).mean()
        m_value = curr_price / ma200
        
        # 2. 獲取 MVRV
        mvrv_value = get_mvrv_ratio()

        # 3. 指標診斷邏輯
        # Mayer Multiple 診斷
        if m_value < 0.8: m_status = "📉 低估 (加碼)"
        elif m_value > 1.8: m_status = "📈 過熱 (減碼)"
        else: m_status = "✅ 正常 (定投)"

        # MVRV 診斷
        if mvrv_value:
            if mvrv_value < 1.0: mv_status = "💎 極度低估 (底部)"
            elif mvrv_value < 1.2: mv_status = "🛒 價值區"
            elif mvrv_value > 3.2: mv_status = "🚨 泡沫區 (頂部)"
            elif mvrv_value > 2.4: mv_status = "⚠️ 過熱區"
            else: mv_status = "✅ 健康"
        else:
            mv_status = "N/A (數據獲取失敗)"

        # 4. 構建報告
        report = (
            f"📊 *BTC 雙指標審計報告*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `${curr_price:,.0f}`\n\n"
            f"📈 *Mayer Multiple (M值)*\n"
            f"數值: `{m_value:.2f}`\n"
            f"診斷: {m_status}\n\n"
            f"⛓️ *MVRV Ratio (鏈上盈虧)*\n"
            f"數值: `{mvrv_value:.2f if mvrv_value else 'N/A'}`\n"
            f"診斷: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *核心建議*: \n"
            f"{'🚀 目前處於雙指標低估區，適合加碼！' if (m_value < 0.8 or (mvrv_value and mvrv_value < 1.2)) else '☕ 市場穩定，繼續執行基礎定投。'}"
        )
        
        send_telegram_msg(report)
        print("報告發送成功")

    except Exception as e:
        print(f"運行失敗: {e}")

if __name__ == "__main__":
    run_monitor()
