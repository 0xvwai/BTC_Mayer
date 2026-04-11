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

def fetch_onchain_data():
    """多重來源與錯誤捕獲機制"""
    errors = []
    
    # 方案 A: CoinMetrics (最穩定，抓取 5 天內最新的一筆有效數據)
    try:
        url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=CapMrktCurUSD,CapRealUSD&limit=5"
        res = requests.get(url, timeout=15).json()
        if 'data' in res and len(res['data']) > 0:
            # 從最新的數據開始往前找，直到找到有資料的那天
            for item in reversed(res['data']):
                if 'CapMrktCurUSD' in item and 'CapRealUSD' in item:
                    m_cap = float(item['CapMrktCurUSD'])
                    r_cap = float(item['CapRealUSD'])
                    if r_cap > 0:
                        return m_cap / r_cap, "CoinMetrics"
            errors.append("CoinMetrics: 找到數據，但欄位空白 (數據延遲)")
        else:
            errors.append("CoinMetrics: 回傳格式異常或被擋")
    except Exception as e:
        errors.append(f"CoinMetrics Error: {str(e)[:30]}")

    # 方案 B: Blockchain.info 計算模式
    try:
        url = "https://api.blockchain.info/charts/realized-price?format=json&timespan=5days"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(url, headers=headers, timeout=15).json()
        if 'values' in res and len(res['values']) > 0:
            r_price = float(res['values'][-1]['y'])
            ex = ccxt.coinbase()
            curr_price = ex.fetch_ticker('BTC/USD')['last']
            return curr_price / r_price, "Blockchain.info"
    except Exception as e:
        errors.append(f"Blockchain Error: {str(e)[:30]}")

    # 方案 C: Blockchair (如果他們恢復了數據)
    try:
        url = "https://api.blockchair.com/bitcoin/stats"
        res = requests.get(url, timeout=15).json()
        data = res.get('data', {})
        m_cap = float(data.get('market_cap_usd', 0))
        r_cap = float(data.get('realized_cap_usd', 0))
        if m_cap > 0 and r_cap > 0:
            return m_cap / r_cap, "Blockchair"
        else:
            errors.append("Blockchair: 缺少 realized_cap 欄位")
    except Exception as e:
        errors.append(f"Blockchair Error: {str(e)[:30]}")

    return None, " | ".join(errors)

def run_monitor():
    try:
        # 1. 抓取 MVRV 數據
        mvrv_value, source_info = fetch_onchain_data()

        # 2. 抓取價格與 Mayer Multiple
        ex = ccxt.coinbase()
        curr_price = ex.fetch_ticker('BTC/USD')['last']
        
        bars = ex.fetch_ohlcv('BTC/USD', timeframe='1d', limit=250)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        ma200 = df['c'].tail(200).mean()
        m_value = curr_price / ma200 if ma200 else None

        # --- 格式化與防呆 ---
        p_str = f"${curr_price:,.0f}" if curr_price else "N/A"
        m_str = f"{m_value:.2f}" if m_value else "N/A"
        mv_str = f"{mvrv_value:.2f}" if mvrv_value else "N/A"

        # 狀態診斷
        m_status = "📉 低估" if m_value and m_value < 0.8 else ("📈 過熱" if m_value and m_value > 1.8 else "✅ 正常")
        mv_status = "💎 底部" if mvrv_value and mvrv_value < 1.0 else ("🚨 頂部" if mvrv_value and mvrv_value > 3.0 else "✅ 健康")

        # --- 報告生成 ---
        report = (
            f"📊 *BTC 雙指標審計*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 當前價格: `{p_str}`\n\n"
            f"📈 *Mayer Multiple*\n"
            f"數值: `{m_str}` ({m_status})\n\n"
            f"⛓️ *MVRV Ratio*\n"
            f"數值: `{mv_str}`\n"
            f"狀態: {mv_status}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📍 *系統狀態/錯誤日誌*:\n`{source_info}`"
        )
        
        send_telegram_msg(report)

    except Exception as e:
        send_telegram_msg(f"❌ 嚴重崩潰: `{str(e)}`")

if __name__ == "__main__":
    run_monitor()
