import ccxt
import pandas as pd
import requests
import os
import time

# --- 核心配置 ---
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

# 模擬真實瀏覽器，防止 GitHub Actions IP 被擋
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json'
}

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload, timeout=10)
    except:
        pass

def fetch_onchain_data():
    """核查並抓取鏈上數據：優先抓取 MVRV Ratio，備援為 Realized Price 計算"""
    # 方案 A: Blockchain.info MVRV 接口
    try:
        print("🔎 執行數據源 A (Blockchain.info MVRV)...")
        url = "https://api.blockchain.info/charts/mvrv-ratio?format=json&timespan=5days"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        if 'values' in res and len(res['values']) > 0:
            return float(res['values'][-1]['y']), "Blockchain.info"
    except Exception as e:
        print(f"A 失敗: {e}")

    # 方案 B: 計算模式 (Coinbase Price / Blockchain Realized Price)
    try:
        print("🔎 執行數據源 B (Realized Price Calculation)...")
        url = "https://api.blockchain.info/charts/realized-price?format=json&timespan=5days"
        res = requests.get(url, headers=HEADERS, timeout=15).json()
        if 'values' in res and len(res['values']) > 0:
            realized_price = float(res['values'][-1]['y'])
            # 獲取市價
            ex = ccxt.coinbase()
            curr_price = ex.fetch_ticker('BTC/USD')['last']
            return curr_price / realized_price, "Manual Calculation"
    except Exception as e:
        print(f"B 失敗: {e}")

    return None, "All Sources Failed"

def run_monitor():
    print("🚀 啟動雙指標審計...")
    try:
        # 1. 抓取 MVRV
        mvrv_value, source_name = fetch_onchain_data()

        # 2. 獲取價格與 200MA (Mayer Multiple)
        ex = ccxt.coinbase()
        ticker = ex.fetch_ticker('BTC/USD')
        curr_price = ticker['last']
        
        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200 = df['c'].tail(200).mean()
        m_value = curr_price / ma200 if ma200 else None

        # --- 數據安全格式化 ---
        p_str = f"${curr_price:,.0f}" if curr_price else "N/A"
        m_str = f"{m_value:.2f}" if m_value else "N/A"
        mv_str = f"{mvrv_value:.2f}" if mvrv_value else "N/A"

        # --- 策略分級診斷 ---
        # Mayer Multiple 分級
        if m_value:
            m_status = "📉 低估" if m_value < 0.8 else ("📈 過熱" if m_value > 1.8 else "✅ 正常")
        else:
            m_status = "數據異常"

        # MVRV 分級 (業界標準級別)
        if mvrv_value:
            if mvrv_value < 1.0: mv_status, advice = "💎 歷史大底", "🔥 全力加碼 (3.0x)"
            elif mvrv_value < 1.2: mv_status, advice = "🛒 價值低估", "📈 穩定加碼 (1.5x)"
            elif mvrv_value > 3.0: mv_status, advice = "🚨 泡沫區間", "🛑 停止定投/止盈"
            elif mvrv_value > 2.4: mv_status, advice = "⚠️ 局部過熱", "📉 減碼觀望 (0.5x)"
            else: mv_status, advice = "✅ 估值合理", "☕ 基準定投 (1.0x)"
        else:
            mv_status, advice = "無法診斷", "請檢查數據源"

        # --- 報告生成 ---
        report = (
            f"📊 *BTC 策略審計報告*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 市場價格: `{p_str}`\n\n"
            f"📈 *Mayer Multiple (動能)*\n"
            f"數值: `{m_str}`\n"
            f"狀態: {m_status}\n\n"
            f"⛓️ *MVRV Ratio (價值)*\n"
            f"數值: `{mv_str}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📢 *核心建議*: \n*{advice}*\n\n"
            f"📍 數據來源: {source_name}"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 嚴重審計錯誤: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
