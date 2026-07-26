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
