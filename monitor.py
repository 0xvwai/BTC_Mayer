import ccxt
import pandas as pd
import requests
import subprocess
import json
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

def fetch_via_os_curl(url):
    """使用 Linux 系統底層的 curl 指令，繞過 Python 的特徵阻擋"""
    cmd = [
        'curl', '-s', 
        '-H', 'User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        '-H', 'Accept: application/json',
        '-H', 'Accept-Language: en-US,en;q=0.9',
        url
    ]
    try:
        # 呼叫系統指令執行抓取
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        # 確保回傳的是正常的 JSON 格式 (而不是防火牆的 HTML 警告)
        if result.returncode == 0 and '{' in result.stdout:
            data = json.loads(result.stdout)
            if 'values' in data and len(data['values']) > 0:
                return float(data['values'][-1]['y']), None
        return None, f"被防火牆攔截或回傳空值: {result.stdout[:40]}..."
    except Exception as e:
        return None, f"系統執行錯誤: {str(e)}"

def get_onchain_metrics(curr_price):
    errors = []
    
    # 方案 A: 直接抓 MVRV 比例
    mvrv_url = "https://api.blockchain.info/charts/mvrv-ratio?format=json&timespan=5days"
    mvrv, err = fetch_via_os_curl(mvrv_url)
    if mvrv:
        return mvrv, "Blockchain.info (OS Bypass)"
    errors.append(f"A方案: {err}")

    # 方案 B: 抓平均成本 (Realized Price) 自己算
    rp_url = "https://api.blockchain.info/charts/realized-price?format=json&timespan=5days"
    r_price, err = fetch_via_os_curl(rp_url)
    if r_price and r_price > 0:
        return curr_price / r_price, "Realized Price (OS Bypass)"
    errors.append(f"B方案: {err}")

    return None, " | ".join(errors)

def run_monitor():
    print("🚀 啟動 OS 級別穿透監控...")
    try:
        # 1. 抓取價格與 Mayer Multiple
        ex = ccxt.coinbase()
        curr_price = float(ex.fetch_ticker('BTC/USD')['last'])
        
        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200 = df['c'].tail(200).mean()
        m_value = curr_price / ma200 if ma200 else None

        # 2. 獲取鏈上 MVRV 數據
        mvrv_value, source_info = get_onchain_metrics(curr_price)

        # --- 格式化顯示 ---
        p_str = f"${curr_price:,.0f}" if curr_price else "N/A"
        m_str = f"{m_value:.2f}" if m_value else "N/A"
        mv_str = f"{mvrv_value:.2f}" if mvrv_value else "N/A"

        # 狀態診斷
        m_status = "📉 低估" if m_value and m_value < 0.8 else ("📈 過熱" if m_value and m_value > 1.8 else "✅ 正常")
        mv_status = "💎 底部" if mvrv_value and mvrv_value < 1.0 else ("🚨 頂部" if mvrv_value and mvrv_value > 3.0 else "✅ 健康")

        # --- 報告生成 ---
        report = (
            f"📊 *BTC 雙指標審計 (OS穿透版)*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `{p_str}`\n\n"
            f"📈 *Mayer Multiple*\n"
            f"數值: `{m_str}` ({m_status})\n\n"
            f"⛓️ *MVRV Ratio*\n"
            f"數值: `{mv_str}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 *系統日誌*:\n`{source_info}`"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 系統崩潰: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
