"""
data_sources.py
負責抓取「盤前全球市場觀察」所需的資料，全部使用 yfinance（Yahoo Finance）免費資料。

注意：
- Yahoo Finance 資料通常有 15-20 分鐘延遲，且部分商品（尤其是期貨）在非交易時段可能沒有即時報價。
- 台指期（TX 期貨）目前沒有可靠、免費、穩定的公開 API，此檔案用「台灣加權指數 ^TWII」作為替代參考，
  正式交易請務必搭配券商看盤軟體或台灣期貨交易所公開資訊觀測站進行比對，不要只依賴這裡的數字。
"""

import yfinance as yf

try:
    import feedparser
except ImportError:
    feedparser = None

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

# 亞洲市場慣例是「紅漲綠跌」，跟美股「綠漲紅跌」相反。
# 這個清單裡的項目在畫面上會用亞洲慣例顯示顏色，其餘（美股/美期貨/匯率）維持國際慣例。
ASIAN_CONVENTION_NAMES = {"日經225", "南韓KOSPI", "台灣加權指數"}


# 候選股清單：常見的高流動性、常被當沖交易的台股，涵蓋半導體、航運、金融、傳產等主要族群
# 這不是台灣證交所官方的即時排行清單，只是拿來計算各項排行指標的候選池，可自行增減
CANDIDATE_UNIVERSE = {
    "台積電": "2330.TW",
    "鴻海": "2317.TW",
    "聯發科": "2454.TW",
    "聯電": "2303.TW",
    "大立光": "3008.TW",
    "日月光投控": "3711.TW",
    "瑞昱": "2379.TW",
    "智原": "3035.TW",
    "創意": "3443.TW",
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
    "台塑": "1301.TW",
    "南亞": "1303.TW",
    "台泥": "1101.TW",
    "亞泥": "1102.TW",
    "中華電": "2412.TW",
    "台灣大": "3045.TW",
    "遠傳": "4904.TW",
    "統一": "1216.TW",
    "統一超": "2912.TW",
    "和泰車": "2207.TW",
    "國泰金": "2882.TW",
    "富邦金": "2881.TW",
    "兆豐金": "2886.TW",
    "玉山金": "2884.TW",
    "中信金": "2891.TW",
    "第一金": "2892.TW",
    "合庫金": "5880.TW",
    "永豐金": "2890.TW",
    "台新金": "2887.TW",
    "開發金": "2883.TW",
}


def get_candidate_metrics():
    """
    一次抓取候選股清單的近2個月日K資料，計算後續排行/評分需要的各項指標：
    - today_volume：今日成交量（股）
    - relative_volume：今日成交量 / 過去20日均量（量能倍數）
    - amplitude_pct：今日振幅 = (今日最高 - 今日最低) / 昨收 * 100
    - turnover：約略成交金額 = 今日成交量 * 今日收盤價
    - change_pct：今日漲跌幅

    回傳: list[dict]，未排序（排序交給呼叫端依需求決定用哪個欄位排序）
    """
    tickers = list(CANDIDATE_UNIVERSE.values())
    try:
        data = yf.download(tickers, period="2mo", interval="1d", progress=False, group_by="ticker")
    except Exception:
        return []

    results = []
    for name, symbol in CANDIDATE_UNIVERSE.items():
        try:
            sub = data[symbol].dropna()
            if len(sub) < 21:
                continue

            today = sub.iloc[-1]
            prev_close = float(sub["Close"].iloc[-2])
            today_volume = float(today["Volume"])
            avg_vol_20 = float(sub["Volume"].iloc[-21:-1].mean())
            if avg_vol_20 <= 0 or prev_close <= 0:
                continue

            relative_volume = today_volume / avg_vol_20
            turnover = today_volume * float(today["Close"])
            amplitude_pct = (float(today["High"]) - float(today["Low"])) / prev_close * 100
            change_pct = (float(today["Close"]) - prev_close) / prev_close * 100

            results.append({
                "name": name,
                "symbol": symbol,
                "today_volume": today_volume,
                "relative_volume": relative_volume,
                "turnover": turnover,
                "amplitude_pct": amplitude_pct,
                "change_pct": change_pct,
            })
        except Exception:
            continue

    return results


def _percentile_rank(values, value):
    """算出 value 在 values 這組數字裡的百分位（0~1），用來把不同單位的指標標準化成可比較的分數"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    rank = sum(1 for v in sorted_vals if v <= value)
    return rank / len(sorted_vals)


def compute_day_trading_scores(metrics):
    """
    交叉分析出「適合當沖觀察」的綜合評分（0~100分），權重邏輯：
    - 相對量能 40%：今天量能是否比平常異常放大，代表市場關注度提高
    - 今日振幅 35%：振幅越大，代表股價當天的價差空間越大，當沖才有操作空間
    - 成交金額 25%：確保流動性足夠，避免價差過大或難以成交

    這是一個簡化的統計評分，用來輔助縮小觀察範圍，不是買賣訊號，實際進出場仍要搭配
    K線圖上的頸線、壓力支撐區與當下的委買委賣狀況判斷。

    回傳: 依 score 由高到低排序的 list[dict]（在原本 metrics 的欄位基礎上多了 score）
    """
    if not metrics:
        return []

    rel_vols = [m["relative_volume"] for m in metrics]
    amplitudes = [m["amplitude_pct"] for m in metrics]
    turnovers = [m["turnover"] for m in metrics]

    scored = []
    for m in metrics:
        rel_score = _percentile_rank(rel_vols, m["relative_volume"])
        amp_score = _percentile_rank(amplitudes, m["amplitude_pct"])
        turn_score = _percentile_rank(turnovers, m["turnover"])
        composite = (rel_score * 0.40 + amp_score * 0.35 + turn_score * 0.25) * 100
        scored.append({**m, "score": composite})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


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


# 《理財達人秀 EBCmoneyshow》YouTube頻道（東森財經台製作，主持人李兆華）
# 用 YouTube 公開的 RSS feed 抓最新影片清單，不需要申請 API 金鑰
EBC_MONEYSHOW_CHANNEL_ID = "UCQvsuaih5lE0n_Ne54nNezg"
EBC_MONEYSHOW_RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={EBC_MONEYSHOW_CHANNEL_ID}"


def _extract_mentioned_stocks(text):
    """
    用簡單的字串比對，看候選股清單裡的公司名稱有沒有出現在影片標題裡，
    藉此標出「本集標題可能提到哪些候選股」。這只是文字比對，不是語意分析，僅供參考，
    標題沒提到不代表節目沒討論，標題提到也不代表是明確買賣建議。
    """
    if not text:
        return []
    return [
        {"name": name, "symbol": symbol}
        for name, symbol in CANDIDATE_UNIVERSE.items()
        if name in text
    ]


def get_ebc_moneyshow_videos(max_results=10):
    """
    抓取《理財達人秀 EBCmoneyshow》YouTube頻道的最新影片清單。

    回傳: list[dict]，每筆包含 title / link / published / mentioned_stocks
    若抓取失敗（網路問題、feedparser 未安裝等）回傳空清單，呼叫端要自行處理空清單的顯示。
    """
    if feedparser is None:
        return []

    try:
        feed = feedparser.parse(EBC_MONEYSHOW_RSS_URL)
    except Exception:
        return []

    if not getattr(feed, "entries", None):
        return []

    videos = []
    for entry in feed.entries[:max_results]:
        title = getattr(entry, "title", "")
        link = getattr(entry, "link", "")
        published = getattr(entry, "published", "")
        videos.append({
            "title": title,
            "link": link,
            "published": published,
            "mentioned_stocks": _extract_mentioned_stocks(title),
        })
    return videos
