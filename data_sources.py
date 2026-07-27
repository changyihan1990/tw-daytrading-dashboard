"""
data_sources.py
負責抓取「盤前全球市場觀察」所需的資料，全部使用 yfinance（Yahoo Finance）免費資料。

注意：
- Yahoo Finance 資料通常有 15-20 分鐘延遲，且部分商品（尤其是期貨）在非交易時段可能沒有即時報價。
- 台指期（TX 期貨）目前沒有可靠、免費、穩定的公開 API，此檔案用「台灣加權指數 ^TWII」作為替代參考，
  正式交易請務必搭配券商看盤軟體或台灣期貨交易所公開資訊觀測站進行比對，不要只依賴這裡的數字。
"""

from datetime import datetime

import re
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
    "櫃買指數": "^TWOII",
    "美元兌台幣": "TWD=X",
}

# 亞洲市場慣例是「紅漲綠跌」，跟美股「綠漲紅跌」相反。
# 這個清單裡的項目在畫面上會用亞洲慣例顯示顏色，其餘（美股/美期貨/匯率）維持國際慣例。
ASIAN_CONVENTION_NAMES = {"日經225", "南韓KOSPI", "台灣加權指數", "櫃買指數"}


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


def _taipei_today():
    """回傳台北時區的今天日期，用來判斷日K的最後一列是不是「今天」"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Taipei")).date()
    except Exception:
        return datetime.utcnow().date()


def get_candidate_metrics():
    """
    抓取候選股清單的指標，用兩組資料組合出「盡量貼近即時」的當日狀況：
    - 日K資料（近3個月）：用來算過去20日均量、昨收，當基準
    - 當日分鐘級資料（5分鐘K，period=1d）：用來算「到目前為止」的今日累積量、當日最高最低、目前價格

    這樣即使現在是盤中，排行也能反映開盤後到目前的即時量價變化，而不是卡在昨天的日K上不動。
    如果分鐘級資料抓不到（例如還沒開盤、假日），會退回用日K的今日列，都沒有的話視為量能0、價格持平。

    回傳的欄位：
    - today_volume：今日累積成交量（股）
    - relative_volume：今日成交量 / 過去20日均量（量能倍數）
    - amplitude_pct：今日振幅 = (今日最高 - 今日最低) / 昨收 * 100
    - turnover：約略成交金額 = 今日成交量 * 目前價格
    - change_pct：目前價格相較昨收的漲跌幅

    回傳: list[dict]，未排序
    """
    tickers = list(CANDIDATE_UNIVERSE.values())

    try:
        daily = yf.download(tickers, period="3mo", interval="1d", progress=False, group_by="ticker")
    except Exception:
        return []

    try:
        intraday = yf.download(tickers, period="1d", interval="5m", progress=False, group_by="ticker")
    except Exception:
        intraday = None

    today = _taipei_today()
    results = []

    for name, symbol in CANDIDATE_UNIVERSE.items():
        try:
            daily_sub = daily[symbol].dropna()
            if len(daily_sub) < 21:
                continue

            last_idx = daily_sub.index[-1]
            last_date = last_idx.date() if hasattr(last_idx, "date") else last_idx
            is_today_row = last_date == today

            # 20日均量跟昨收都要排除「今天」這一列（如果今天已經在日K裡了），避免用到還在更新中的資料
            history = daily_sub.iloc[:-1] if is_today_row else daily_sub
            if len(history) < 20:
                continue

            prev_close = float(history["Close"].iloc[-1])
            avg_vol_20 = float(history["Volume"].iloc[-20:].mean())
            if prev_close <= 0 or avg_vol_20 <= 0:
                continue

            today_volume = today_high = today_low = today_close = None

            # 優先用分鐘級資料反映「到目前為止」的即時狀況
            if intraday is not None:
                try:
                    intraday_sub = intraday[symbol].dropna()
                    if not intraday_sub.empty:
                        today_volume = float(intraday_sub["Volume"].sum())
                        today_high = float(intraday_sub["High"].max())
                        today_low = float(intraday_sub["Low"].min())
                        today_close = float(intraday_sub["Close"].iloc[-1])
                except Exception:
                    pass

            # 分鐘級資料抓不到，退回用日K的今日列
            if today_volume is None and is_today_row:
                today_row = daily_sub.iloc[-1]
                today_volume = float(today_row["Volume"])
                today_high = float(today_row["High"])
                today_low = float(today_row["Low"])
                today_close = float(today_row["Close"])

            # 兩邊都沒有（例如還沒開盤），視為量能0、價格持平在昨收
            if today_volume is None:
                today_volume, today_high, today_low, today_close = 0.0, prev_close, prev_close, prev_close

            relative_volume = today_volume / avg_vol_20
            turnover = today_volume * today_close
            amplitude_pct = (today_high - today_low) / prev_close * 100
            change_pct = (today_close - prev_close) / prev_close * 100

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


# 台股代號常見是4位數字（部分ETF是5位數字，例如00919），拿掉看起來像年份的數字，減少誤判
_STOCK_CODE_PATTERN = re.compile(r"(?<!\d)\d{4,5}(?!\d)")
_YEAR_LIKE_NUMBERS = {str(y) for y in range(2015, 2036)}

# 代號查名稱有查詢次數上限，避免一支影片裡出現太多不認識的代號時拖慢速度
_MAX_UNKNOWN_CODE_LOOKUPS = 3


def _lookup_stock_name_by_code(code):
    """用 yfinance 查詢股票代號對應的公司名稱（例如 5410 -> 國眾），查不到就回傳 None"""
    for suffix in (".TW", ".TWO"):
        try:
            info = yf.Ticker(f"{code}{suffix}").info
            name = info.get("shortName") or info.get("longName")
            if name:
                return name
        except Exception:
            continue
    return None


def extract_mentioned_stocks_from_text(text):
    """
    從一段文字（例如影片標題+說明）裡，直接找出可能提到的股票，不限於候選股清單：

    1. 先看候選股清單的公司名稱有沒有直接出現在文字裡
    2. 再用正規表達式抓出文字裡的4~5位數字（台股代號常見格式），排除看起來像年份的數字
       - 如果代號剛好對到候選股清單，直接標記名稱
       - 不在候選清單的代號，改用 yfinance 查詢公司名稱（有查詢次數上限）

    這只是文字/代號比對，不是語意分析，僅供參考，標題沒提到不代表節目沒討論，
    提到也不代表是明確的買賣建議。

    回傳: list[dict]，每個 {"name":..., "symbol":..., "source": "候選清單"|"代號比對"}
    """
    if not text:
        return []

    found = {}

    for name, symbol in CANDIDATE_UNIVERSE.items():
        if name in text:
            found[symbol] = {"name": name, "symbol": symbol, "source": "候選清單"}

    code_to_symbol = {v.split(".")[0]: v for v in CANDIDATE_UNIVERSE.values()}
    symbol_to_name = {v: k for k, v in CANDIDATE_UNIVERSE.items()}

    codes = {m for m in _STOCK_CODE_PATTERN.findall(text) if m not in _YEAR_LIKE_NUMBERS}
    unknown_lookup_count = 0

    for code in codes:
        if code in code_to_symbol:
            symbol = code_to_symbol[code]
            if symbol not in found:
                found[symbol] = {"name": symbol_to_name[symbol], "symbol": symbol, "source": "候選清單"}
            continue

        symbol_guess = f"{code}.TW"
        if symbol_guess in found:
            continue

        if unknown_lookup_count >= _MAX_UNKNOWN_CODE_LOOKUPS:
            found[symbol_guess] = {"name": f"代號 {code}（未查詢名稱）", "symbol": symbol_guess, "source": "代號比對"}
            continue

        unknown_lookup_count += 1
        name = _lookup_stock_name_by_code(code)
        if name:
            found[symbol_guess] = {"name": name, "symbol": symbol_guess, "source": "代號比對"}
        else:
            found[symbol_guess] = {"name": f"代號 {code}（查無資料）", "symbol": symbol_guess, "source": "代號比對"}

    return list(found.values())


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
        description = getattr(entry, "summary", "")
        combined_text = f"{title} {description}"
        videos.append({
            "title": title,
            "link": link,
            "published": published,
            "mentioned_stocks": extract_mentioned_stocks_from_text(combined_text),
        })
    return videos
