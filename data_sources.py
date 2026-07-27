"""
data_sources.py
負責抓取「盤前全球市場觀察」所需的資料，全部使用 yfinance（Yahoo Finance）免費資料。

注意：
- Yahoo Finance 資料通常有 15-20 分鐘延遲，且部分商品（尤其是期貨）在非交易時段可能沒有即時報價。
- 台指期（TX 期貨）目前沒有可靠、免費、穩定的公開 API，此檔案用「台灣加權指數 ^TWII」作為替代參考，
  正式交易請務必搭配券商看盤軟體或台灣期貨交易所公開資訊觀測站進行比對，不要只依賴這裡的數字。
- 櫃買指數（^TWOII）經實測後確認：這個代號只存在於 Yahoo「台灣站」（tw.stock.yahoo.com），
  Yahoo 的「國際版」後端（finance.yahoo.com，也就是 yfinance 套件實際抓資料的地方）查這個代號會是 404，
  代表這不是程式碼寫法的問題，是免費的 yfinance 從源頭就拿不到這筆資料，所以這裡沒有加這個指數。
  如果之後想看櫃買指數，建議直接看 Yahoo奇摩股市或券商看盤軟體。
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import re

import pandas as pd
import yfinance as yf

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None

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


def _taipei_today():
    """回傳台北時區的今天日期，用來判斷日K的最後一列是不是「今天」"""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Taipei")).date()
    except Exception:
        return datetime.utcnow().date()


def _fetch_intraday_single(symbol):
    """
    抓單一股票「當日」的5分鐘K資料，回傳今日累積量/最高/最低/目前價，抓不到回傳 None。
    刻意一次只抓一檔，不跟其他股票一起批次抓，避免 Yahoo 對大量批次分鐘資料請求限流，
    導致整批失敗（這是先前「當沖排行沒有即時反映開盤現狀」的主因）。
    """
    try:
        df = yf.download(symbol, period="1d", interval="5m", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        if df.empty:
            return None
        return {
            "volume": float(df["Volume"].sum()),
            "high": float(df["High"].max()),
            "low": float(df["Low"].min()),
            "close": float(df["Close"].iloc[-1]),
        }
    except Exception:
        return None


def _fetch_all_intraday(symbols, max_workers=8):
    """用多執行緒平行抓取每檔股票的當日分鐘資料，比逐一序列抓取快很多，且互不影響"""
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {executor.submit(_fetch_intraday_single, s): s for s in symbols}
        for future in as_completed(future_map):
            symbol = future_map[future]
            try:
                results[symbol] = future.result()
            except Exception:
                results[symbol] = None
    return results


def get_candidate_metrics():
    """
    抓取候選股清單的指標，用兩組資料組合出「盡量貼近即時」的當日狀況：
    - 日K資料（近3個月，一次批次抓）：用來算過去20日均量、昨收，當基準
    - 當日分鐘級資料（5分鐘K，逐檔個別平行抓取）：用來算「到目前為止」的今日累積量、當日最高最低、目前價格

    分鐘級資料改成逐檔個別抓取（而不是像日K一樣整批一次抓），是因為 Yahoo 對大量股票的
    分鐘級批次請求容易限流，導致整批失敗、所有股票都退回「量能0、價格持平」，
    這樣排行看起來就會像完全沒有變動。逐檔個別抓取搭配多執行緒平行處理，較不會整批一起壞掉。

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

    intraday_map = _fetch_all_intraday(tickers)

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

            intraday_data = intraday_map.get(symbol)
            if intraday_data:
                today_volume = intraday_data["volume"]
                today_high = intraday_data["high"]
                today_low = intraday_data["low"]
                today_close = intraday_data["close"]

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
    逐一個別抓取（而不是一次批次抓全部），避免其中一項資料有問題時，
    影響到其他商品的日期索引對齊（不同市場的交易日曆本來就不一樣，混在一起批次抓容易出錯）。

    回傳: list[dict]，每個 dict 包含 name / symbol / last / change_pct 或 error
    """
    snapshot = []
    for name, symbol in GLOBAL_TICKERS.items():
        try:
            hist = yf.download(symbol, period="5d", interval="1d", progress=False, auto_adjust=False)
            if isinstance(hist.columns, pd.MultiIndex):
                hist.columns = hist.columns.get_level_values(0)
            close = hist["Close"].dropna()

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


_VIDEO_ID_PATTERN = re.compile(r"v=([A-Za-z0-9_-]{11})")


def _extract_video_id(entry, link):
    """從RSS條目或影片連結中取出YouTube影片ID"""
    vid = getattr(entry, "yt_videoid", None)
    if vid:
        return vid
    match = _VIDEO_ID_PATTERN.search(link or "")
    return match.group(1) if match else None


def get_video_transcript_text(video_id):
    """
    抓取YouTube影片的字幕內容（通常是自動生成字幕），把所有片段串接成一段完整文字。
    這才是真正的「影片內容」，而不只是標題或說明欄位。

    如果影片沒有開放字幕、字幕被關閉，或套件沒安裝，回傳 None，呼叫端要自行退回用標題/說明分析。
    """
    if YouTubeTranscriptApi is None or not video_id:
        return None
    for languages in (["zh-Hant", "zh-TW"], ["zh-Hans", "zh-CN", "zh"], ["en"]):
        try:
            segments = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            text = " ".join(seg.get("text", "") for seg in segments)
            if text.strip():
                return text
        except Exception:
            continue
    return None


def get_ebc_moneyshow_videos(max_results=10, use_transcript=True):
    """
    抓取《理財達人秀 EBCmoneyshow》YouTube頻道的最新影片清單，並分析每支影片「內容」提到的股票。

    分析優先順序：
    1. 如果抓得到字幕（YouTube自動字幕），優先用字幕全文分析，這才是真正的「影片內容」
    2. 字幕抓不到（該影片沒開字幕、字幕被關閉等），退回只用標題+說明文字分析

    回傳: list[dict]，每筆包含 title / link / published / used_transcript / mentioned_stocks
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

        transcript_text = None
        if use_transcript:
            video_id = _extract_video_id(entry, link)
            transcript_text = get_video_transcript_text(video_id)

        used_transcript = bool(transcript_text)
        combined_text = " ".join(filter(None, [title, description, transcript_text]))

        videos.append({
            "title": title,
            "link": link,
            "published": published,
            "used_transcript": used_transcript,
            "mentioned_stocks": extract_mentioned_stocks_from_text(combined_text),
        })
    return videos
