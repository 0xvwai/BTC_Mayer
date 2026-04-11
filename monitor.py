import ccxt
import pandas as pd
import requests
import os
import json
import urllib.parse

TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

def fetch_mvrv_via_proxy():
    """使用代理伺服器穿透 IP 封鎖，並具備雙重計算備援"""
    errors = []
    
    # 方案 A: 透過代理直接獲取 MVRV
    try:
        url_a = "https://api.blockchain.info/charts/mvrv-ratio?format=json&timespan=5days"
        proxy_a = f"https://api.allorigins.win/get?url={urllib.parse.quote(url_a, safe='')}"
        
        res = requests.get(proxy_a, timeout=20).json()
        if 'contents' in res:
            data = json.loads(res['contents'])
            if 'values' in data and len(data['values']) > 0:
                mvrv = float(data['values'][-1]['y'])
                return mvrv, "Blockchain MVRV (Via Proxy)"
        errors.append("方案A: 代理成功但無數據")
    except Exception as e:
        errors.append(f"方案A 失敗: {str(e)[:20]}")

    # 方案 B: 透過代理獲取平均成本 (Realized Price) 自己算
    try:
        url_b = "https://api.blockchain.info/charts/realized-price?format=json&timespan=5days"
        proxy_b = f"https://api.allorigins.win/get?url={urllib.parse.quote(url_b, safe='')}"
        
        res = requests.get(proxy_b, timeout=20).json()
        if 'contents' in res:
            data = json.loads(res['contents'])
            if 'values' in data and len(data['values']) > 0:
                r_price = float(data['values'][-1]['y'])
                ex = ccxt.coinbase()
                c_price = ex.fetch_ticker('BTC/USD')['last']
                return c_price / r_price, "Realized Price (Via Proxy)"
        errors.append("方案B: 代理成功但無數據")
    except Exception as e:
        errors.append(f"方案B 失敗: {str(e)[:20]}")

    return None, " | ".join(errors)

def run_monitor():
    try:
        # 1. 透過代理獲取 MVRV
        mvrv_value, source_info = fetch_mvrv_via_proxy()

        # 2. 抓取價格與 Mayer Multiple
        ex = ccxt.coinbase()
        curr_price = ex.fetch_ticker('BTC/USD')['last']
        
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

        # --- 報告生成 ---
        report = (
            f"📊 *BTC 雙指標審計 (代理穿透版)*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `{p_str}`\n\n"
            f"📈 *Mayer Multiple*\n"
            f"數值: `{m_str}` ({m_status})\n\n"
            f"⛓️ *MVRV Ratio*\n"
            f"數值: `{mv_str}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 *數據源*: `{source_info}`"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 系統崩潰: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
