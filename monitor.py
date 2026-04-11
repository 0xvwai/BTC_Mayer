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

def get_mvrv_from_blockchair():
    """從 Blockchair 獲取數據"""
    try:
        url = "https://api.blockchair.com/bitcoin/stats"
        res = requests.get(url, timeout=15).json()
        data = res.get('data')
        if not data: return None, None
        
        m_cap = data.get('market_cap_usd', 0)
        r_cap = data.get('realized_cap_usd', 0)
        price = data.get('market_price_usd', 0)
        
        mvrv = m_cap / r_cap if r_cap > 0 else None
        return mvrv, price
    except:
        return None, None

def run_monitor():
    print("🚀 啟動加固版監控...")
    try:
        # 1. 抓取數據
        mvrv_value, curr_price = get_mvrv_from_blockchair()
        
        # 如果 Blockchair 拿不到價格，用 Coinbase 補
        if not curr_price:
            try:
                ex = ccxt.coinbase()
                ticker = ex.fetch_ticker('BTC/USD')
                curr_price = ticker.get('last')
            except:
                curr_price = None

        # 2. 計算 Mayer Multiple
        m_value = None
        try:
            ex = ccxt.coinbase()
            bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
            df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
            ma200 = df['c'].tail(200).mean()
            if curr_price and ma200:
                m_value = curr_price / ma200
        except:
            m_value = None

        # --- 安全格式化處理 (防止 NoneType 錯誤) ---
        p_str = f"${curr_price:,.0f}" if curr_price is not None else "N/A"
        m_str = f"{m_value:.2f}" if m_value is not None else "N/A"
        mv_str = f"{mvrv_value:.2f}" if mvrv_value is not None else "N/A"

        # 狀態診斷
        m_status = "📉 低估" if m_value and m_value < 0.8 else ("📈 過熱" if m_value and m_value > 1.8 else "✅ 正常")
        mv_status = "💎 底部" if mvrv_value and mvrv_value < 1.0 else ("🚨 頂部" if mvrv_value and mvrv_value > 3.0 else "✅ 健康")

        report = (
            f"📊 *BTC 雙指標監控 (加固版)*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `{p_str}`\n\n"
            f"📈 *Mayer Multiple*\n"
            f"數值: `{m_str}` ({m_status})\n\n"
            f"⛓️ *MVRV Ratio*\n"
            f"數值: `{mv_str}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *建議*: 數據僅供參考，請維持紀律。"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 核心邏輯出錯: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
