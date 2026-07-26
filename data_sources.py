"""
data_sources.py
負責抓取「盤前全球市場觀察」所需的資料，全部使用 yfinance（Yahoo Finance）免費資料。

注意：
- Yahoo Finance 資料通常有 15-20 分鐘延遲，且部分商品（尤其是期貨）在非交易時段可能沒有即時報價。
- 台指期（TX 期貨）目前沒有可靠、免費、穩定的公開 API，此檔案用「台灣加權指數 ^TWII」作為替代參考，
  正式交易請務必搭配券商看盤軟體或台灣期貨交易所公開資訊觀測站進行比對，不要只依賴這裡的數字。
"""

import yfinance as yf

# 觀察順序建議：美股收盤 -> 美股期貨(隔夜) -> 日韓開盤 -> 台幣匯率 -> 台股/台指期開盤
GLOBAL_TICKERS = {
    "美股道瓊工業": "^DJI",
    "美股S&P500": "^GSPC",
    "美股那斯達克": "^IXIC",
    "美期貨-道瓊(YM)": "YM=F",
    "美期貨-標普(ES)": "ES=F",
    "美期貨-那斯達克(NQ)": "NQ=F",
    "日經225": "^N225",
    "南韓KOSPI": "^KS11",
    "台灣加權指數": "^TWII",
    "美元兌台幣": "TWD=X",
}


# 候選股清單：常見的高流動性、常被當沖交易的台股（可自行增減）
# 這不是官方即時當沖排行，只是拿來計算「相對量能」的候選池
CANDIDATE_UNIVERSE = {
    "台積電": "2330.TW",
    "鴻海": "2317.TW",
    "聯發科": "2454.TW",
    "長榮": "2603.TW",
    "陽明": "2609.TW",
    "萬海": "2615.TW",
    "中租-KY": "5871.TW",
    "群創": "3481.TW",
    "友達": "2409.TW",
    "力積電": "6770.TW",
    "世芯-KY": "3661.TW",
    "信驊": "5274.TW",
    "緯創": "3231.TW",
    "廣達": "2382.TW",
    "技嘉": "2376.TW",
    "華碩": "2357.TW",
    "台達電": "2308.TW",
    "南亞科": "2408.TW",
    "旺宏": "2337.TW",
    "京元電子": "2449.TW",
    "元大台灣50": "0050.TW",
    "元大高股息": "0056.TW",
    "中鋼": "2002.TW",
    "國泰金": "2882.TW",
    "富邦金": "2881.TW",
}


def get_hot_day_trading_candidates(top_n=15):
    """
    從候選股清單中，抓取近2個月日K資料，計算「今日成交量 / 過去20日均量」的相對量能倍數，
    藉此近似找出「今天量能異常放大」的熱門候選股。

    這不是台灣證交所官方的即時當沖排行榜（免費資料源沒有這個），
    只是用量能變化去猜測今天比較活躍、比較適合觀察當沖的股票，僅供輔助篩選參考。

    回傳: list[dict]，依 relative_volume 由高到低排序
    """
    tickers = list(CANDIDATE_UNIVERSE.values())
    try:
        data = yf.download(tickers, period="2mo", interval="1d", progress=False, group_by="ticker")
    except Exception:
        return []

    results = []
    for name, symbol in CANDIDATE_UNIVERSE.items():
        try:
            vol = data[symbol]["Volume"].dropna()
            close = data[symbol]["Close"].dropna()
            if len(vol) < 21:
                continue

            today_vol = float(vol.iloc[-1])
            avg_vol_20 = float(vol.iloc[-21:-1].mean())
            if avg_vol_20 <= 0:
                continue

            relative_volume = today_vol / avg_vol_20
            change_pct = None
            if len(close) >= 2:
                change_pct = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100)

            results.append({
                "name": name,
                "symbol": symbol,
                "relative_volume": relative_volume,
                "change_pct": change_pct,
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["relative_volume"], reverse=True)
    return results[:top_n]


def get_market_snapshot():
    """
    抓取上述所有商品的最新價與漲跌幅（相較前一筆收盤）。
    回傳: list[dict]，每個 dict 包含 name / symbol / last / change_pct 或 error
    """
    tickers = list(GLOBAL_TICKERS.values())
    snapshot = []

    try:
        data = yf.download(
            tickers, period="5d", interval="1d",
            progress=False, group_by="ticker", auto_adjust=False,
        )
    except Exception as e:
        return [{"name": name, "symbol": symbol, "error": str(e)}
                for name, symbol in GLOBAL_TICKERS.items()]

    for name, symbol in GLOBAL_TICKERS.items():
        try:
            if len(tickers) == 1:
                close = data["Close"].dropna()
            else:
                close = data[symbol]["Close"].dropna()

            if len(close) >= 2:
                last = float(close.iloc[-1])
                prev = float(close.iloc[-2])
                change_pct = (last - prev) / prev * 100
            elif len(close) == 1:
                last = float(close.iloc[-1])
                change_pct = None
            else:
                last, change_pct = None, None

            snapshot.append({
                "name": name, "symbol": symbol,
                "last": last, "change_pct": change_pct,
            })
        except Exception as e:
            snapshot.append({"name": name, "symbol": symbol, "error": str(e)})

    return snapshot
