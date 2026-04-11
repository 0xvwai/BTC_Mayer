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
    except:
        pass

def get_fear_and_greed():
    """獲取市場恐懼與貪婪指數 (100% 穩定，無 IP 限制)"""
    try:
        url = "https://api.alternative.me/fng/?limit=1"
        res = requests.get(url, timeout=10).json()
        data = res['data'][0]
        value = int(data['value'])
        
        # 繁體中文狀態翻譯
        classification = data['value_classification']
        if classification == "Extreme Fear": status = "🥶 極度恐慌 (撿便宜)"
        elif classification == "Fear": status = "😨 恐慌"
        elif classification == "Neutral": status = "😐 中立"
        elif classification == "Greed": status = "😏 貪婪"
        elif classification == "Extreme Greed": status = "🤑 極度貪婪 (危險)"
        else: status = classification
        
        return value, status
    except Exception as e:
        return None, f"獲取失敗: {str(e)}"

def run_monitor():
    try:
        # 1. 抓取 Fear & Greed Index
        fng_value, fng_class = get_fear_and_greed()

        # 2. 抓取價格與 Mayer Multiple
        ex = ccxt.coinbase()
        curr_price = float(ex.fetch_ticker('BTC/USD')['last'])
        
        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200 = df['c'].tail(200).mean()
        m_value = curr_price / ma200 if ma200 else None

        # --- 格式化顯示 ---
        p_str = f"${curr_price:,.0f}" if curr_price else "N/A"
        m_str = f"{m_value:.2f}" if m_value else "N/A"
        f_str = f"{fng_value}/100" if fng_value else "N/A"

        # Mayer Multiple 狀態診斷
        m_status = "📉 低估" if m_value and m_value < 0.8 else ("📈 過熱" if m_value and m_value > 1.8 else "✅ 正常")

        # --- 報告生成 ---
        report = (
            f"📊 *BTC 雙指標審計 (F&G 情緒版)*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `{p_str}`\n\n"
            f"📈 *Mayer Multiple (價格動能)*\n"
            f"數值: `{m_str}` ({m_status})\n\n"
            f"🧭 *Fear & Greed (市場情緒)*\n"
            f"指數: `{f_str}`\n"
            f"狀態: {fng_class}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *核心操作建議*: \n"
            f"{'🚀 價格低估且市場極度恐慌，這是黃金加碼點！(3.0x)' if (m_value and m_value < 0.8 and fng_value and fng_value < 25) else '☕ 目前數據未見極端，維持基準定投 (1.0x)。'}"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 系統錯誤: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
