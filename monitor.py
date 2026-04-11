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
    """從 Blockchair 獲取數據，這是目前最穩定的免費來源"""
    try:
        # Blockchair 的統計接口
        url = "https://api.blockchair.com/bitcoin/stats"
        res = requests.get(url, timeout=10).json()
        data = res['data']
        
        # MVRV = Market Cap / Realized Cap
        market_cap = data['market_cap_usd']
        realized_cap = data['realized_cap_usd']
        
        mvrv = market_cap / realized_cap
        # 同時獲取實時價格
        price = data['market_price_usd']
        
        return mvrv, price
    except Exception as e:
        print(f"MVRV 抓取出錯: {e}")
        return None, None

def run_monitor():
    print("🚀 啟動診斷監控...")
    try:
        # 1. 抓取 MVRV 與實時價格 (優先從 Blockchair 拿，因為它最穩)
        mvrv_value, curr_price = get_mvrv_from_blockchair()
        
        if not curr_price:
            # 如果 Blockchair 失敗，嘗試用 Coinbase 補救價格
            print("⚠️ Blockchair 失敗，嘗試 Coinbase...")
            ex = ccxt.coinbase()
            ticker = ex.fetch_ticker('BTC/USD')
            curr_price = ticker['last']

        # 2. 獲取 200MA 用於計算 Mayer Multiple
        ex = ccxt.coinbase()
        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200 = df['c'].tail(200).mean()
        m_value = curr_price / ma200

        # 3. 診斷與報告
        m_status = "📉 低估" if m_value < 0.8 else ("📈 過熱" if m_value > 1.8 else "✅ 正常")
        
        if mvrv_value:
            mv_status = "💎 底部" if mvrv_value < 1.0 else ("🚨 頂部" if mvrv_value > 3.0 else "✅ 健康")
        else:
            mv_status = "數據獲取失敗"

        report = (
            f"📊 *BTC 雙指標監控 (S22U 部署版)*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `${curr_price:,.0f}`\n\n"
            f"📈 *Mayer Multiple*\n"
            f"數值: `{m_value:.2f}` ({m_status})\n\n"
            f"⛓️ *MVRV Ratio*\n"
            f"數值: `{mvrv_value:.2f if mvrv_value else 'N/A'}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *建議*: 繼續執行基準定投計畫。"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        # 這是最關鍵的：如果出錯，把錯誤訊息發到你的 Telegram
        error_msg = f"❌ 系統執行失敗\n原因: `{str(e)}`"
        send_telegram_msg(error_msg)

if __name__ == "__main__":
    run_monitor()
