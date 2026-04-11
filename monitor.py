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
        print(f"發送失敗: {e}")

def run_monitor():
    try:
        exchange = ccxt.coinbase()
        bars = exchange.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        if not bars: return
        df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        current_price = df['close'].iloc[-1]
        ma200 = df['close'].tail(200).mean()
        m_value = current_price / ma200
        if m_value < 0.6: status, advice = "🔥 極度低估", "🚀 加碼 3.0x"
        elif m_value < 0.8: status, advice = "🛒 價值區", "📈 加碼 1.5x"
        elif m_value > 2.4: status, advice = "🚨 泡沫區", "🛑 停止買入/止盈"
        elif m_value > 1.8: status, advice = "⚠️ 過熱區", "📉 減碼 0.5x"
        else: status, advice = "✅ 正常", "☕ 維持 1.0x 定投"

        report = (
            f"📊 *BTC 雲端監控報告*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 價格: `${current_price:,.0f}`\n"
            f"📐 M值: `{m_value:.2f}`\n"
            f"🚦 狀態: *{status}*\n"
            f"📢 *操作*: {advice}"
        )
        send_telegram_msg(report)
    except Exception as e:
        print(f"出錯: {e}")

if __name__ == "__main__":
    run_monitor()
