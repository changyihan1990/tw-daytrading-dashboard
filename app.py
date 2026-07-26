"""
app.py
台股當沖觀察儀表板 —— 本機 Streamlit 網頁儀表板

執行方式：
    streamlit run app.py

資料來源：Yahoo Finance（透過 yfinance 套件），延遲約 15-20 分鐘，僅供技術面觀察參考，非投資建議。
"""

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

from analysis import compute_vwap, detect_neckline, estimate_full_day_volume, volume_profile_zones
from data_sources import (
    ASIAN_CONVENTION_NAMES,
    compute_day_trading_scores,
    get_candidate_metrics,
    get_ebc_moneyshow_videos,
    get_market_snapshot,
)

MINUTE_INTERVALS = ("1m", "5m", "30m", "1h")

# 快取候選股指標計算結果 5 分鐘，避免每次頁面互動都重新抓資料
_get_candidate_metrics_cached = st.cache_data(ttl=300)(get_candidate_metrics)
# 影片清單快取久一點（30分鐘），因為節目不會每幾分鐘就更新一支新影片
_get_ebc_videos_cached = st.cache_data(ttl=1800)(get_ebc_moneyshow_videos)


def _style_change(val):
    """台股慣例：漲為紅色、跌為綠色，用在排行表格的漲跌幅欄位上"""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return "color: #c0392b; font-weight: 600;"
    if v < 0:
        return "color: #1e8449; font-weight: 600;"
    return ""


st.set_page_config(page_title="台股當沖觀察儀表板", layout="wide", page_icon="📈")

# ---------- 自訂樣式：參考券商App的簡潔卡片風格 ----------
st.markdown(
    """
    <style>
        .block-container { padding-top: 1.4rem; }
        div[data-testid="stMetric"] {
            background-color: rgba(140,140,140,0.07);
            border: 1px solid rgba(140,140,140,0.18);
            border-radius: 10px;
            padding: 12px 14px 8px 14px;
        }
        div[data-testid="stMetricLabel"] { font-size: 13px; opacity: 0.8; }
        div[data-testid="stMetricValue"] { font-size: 21px; }
        .stTabs [data-baseweb="tab"] { font-size: 15px; font-weight: 600; padding: 8px 16px; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📈 台股當沖觀察儀表板")
st.caption(
    f"資料來源：Yahoo Finance（延遲資料，僅供參考，非投資建議）　|　"
    f"頁面產生時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)

tab1, tab2, tab3, tab4 = st.tabs(
    ["🌏 盤前總覽", "🔥 熱門/當沖評分排行", "📊 個股技術分析", "📺 理財達人秀彙整"]
)

# 候選股指標只算一次，讓「熱門排行」跟「理財達人秀彙整」分頁共用（交叉比對用得到）
with st.spinner("計算候選股指標中..."):
    _candidate_metrics = _get_candidate_metrics_cached()
_scored_candidates = compute_day_trading_scores(_candidate_metrics) if _candidate_metrics else []

# ============================================================
# Tab 1：盤前全球市場總覽
# ============================================================
with tab1:
    st.caption("建議觀察順序：美股收盤 → 美股期貨(隔夜走勢) → 日韓股市開盤 → 台幣匯率 → 台股/台指期開盤")

    with st.spinner("讀取全球市場資料中..."):
        snapshot = get_market_snapshot()

    cols = st.columns(5)
    for i, item in enumerate(snapshot):
        col = cols[i % 5]
        with col:
            if "error" in item or item.get("last") is None:
                st.metric(item["name"], "資料取得失敗")
            else:
                delta = f"{item['change_pct']:.2f}%" if item["change_pct"] is not None else None
                color_mode = "inverse" if item["name"] in ASIAN_CONVENTION_NAMES else "normal"
                st.metric(item["name"], f"{item['last']:.2f}", delta, delta_color=color_mode)

    st.info(
        "台指期（TX）目前沒有可靠的免費即時 API，「台灣加權指數」只能作為方向性參考，"
        "正式判斷開盤走勢請搭配你的券商看盤軟體或期交所公開資訊觀測站。　"
        "（台灣加權指數／日經／KOSPI 採紅漲綠跌的亞洲慣例，美股維持國際慣例綠漲紅跌）"
    )

# ============================================================
# Tab 2：熱門/當沖評分排行
# ============================================================
with tab2:
    st.caption("以下排行皆從約50檔常見高流動性台股候選清單中計算，並非全市場官方即時排行，僅供輔助篩選參考。")

    metrics = _candidate_metrics
    scored = _scored_candidates

    if not metrics:
        st.error("目前無法取得候選股資料，請稍後再試")
    else:
        # ---- 快速選擇，代入下方個股分析 ----
        NO_SELECTION_LABEL = "-- 不使用清單，自行輸入代號 --"
        top_for_dropdown = scored[:15]
        hot_options = [NO_SELECTION_LABEL] + [
            f"{item['name']}（{item['symbol']}）綜合評分 {item['score']:.0f}"
            for item in top_for_dropdown
        ]

        if "ticker_input" not in st.session_state:
            st.session_state["ticker_input"] = "2330.TW"
        if "last_hot_choice" not in st.session_state:
            st.session_state["last_hot_choice"] = NO_SELECTION_LABEL

        default_index = (
            hot_options.index(st.session_state["last_hot_choice"])
            if st.session_state["last_hot_choice"] in hot_options
            else 0
        )
        hot_choice = st.selectbox(
            "快速選擇今日當沖評分較高的股票（會自動帶入「個股技術分析」分頁）",
            hot_options,
            index=default_index,
        )

        if hot_choice != st.session_state["last_hot_choice"]:
            st.session_state["last_hot_choice"] = hot_choice
            if hot_choice != NO_SELECTION_LABEL:
                chosen = top_for_dropdown[hot_options.index(hot_choice) - 1]
                st.session_state["ticker_input"] = chosen["symbol"]
            st.rerun()

        st.divider()

        col1, col2 = st.columns(2)

        with col1:
            st.subheader("🏆 當沖適合度綜合評分")
            st.caption("綜合「相對量能40% + 今日振幅35% + 成交金額25%」計算，分數越高代表量能、波動、流動性條件越突出")
            df_score = pd.DataFrame(scored[:15])[
                ["name", "symbol", "score", "change_pct", "amplitude_pct", "relative_volume"]
            ]
            df_score.columns = ["名稱", "代號", "綜合評分", "漲跌幅(%)", "振幅(%)", "量能倍數"]
            st.dataframe(
                df_score.style.format(
                    {"綜合評分": "{:.0f}", "漲跌幅(%)": "{:.2f}", "振幅(%)": "{:.2f}", "量能倍數": "{:.1f}x"}
                ).map(_style_change, subset=["漲跌幅(%)"]),
                use_container_width=True,
                hide_index=True,
            )

        with col2:
            st.subheader("💰 當日成交量大股")
            st.caption("依候選清單中今日實際成交股數排序")
            df_vol = pd.DataFrame(sorted(metrics, key=lambda x: x["today_volume"], reverse=True)[:15])[
                ["name", "symbol", "today_volume", "change_pct", "turnover"]
            ]
            df_vol.columns = ["名稱", "代號", "今日成交量(股)", "漲跌幅(%)", "成交金額(約)"]
            st.dataframe(
                df_vol.style.format(
                    {"今日成交量(股)": "{:,.0f}", "漲跌幅(%)": "{:.2f}", "成交金額(約)": "{:,.0f}"}
                ).map(_style_change, subset=["漲跌幅(%)"]),
                use_container_width=True,
                hide_index=True,
            )

        st.subheader("⚡ 相對量能異常放大排行")
        st.caption("依「今日成交量 ÷ 過去20日均量」排序，倍數越高代表今天量能相對平常越突出")
        df_rel = pd.DataFrame(sorted(metrics, key=lambda x: x["relative_volume"], reverse=True)[:15])[
            ["name", "symbol", "relative_volume", "change_pct", "amplitude_pct"]
        ]
        df_rel.columns = ["名稱", "代號", "量能倍數", "漲跌幅(%)", "振幅(%)"]
        st.dataframe(
            df_rel.style.format(
                {"量能倍數": "{:.1f}x", "漲跌幅(%)": "{:.2f}", "振幅(%)": "{:.2f}"}
            ).map(_style_change, subset=["漲跌幅(%)"]),
            use_container_width=True,
            hide_index=True,
        )

# ============================================================
# Tab 3：個股技術分析
# ============================================================
with tab3:
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        ticker_input = st.text_input("股票代號（上市加 .TW，上櫃加 .TWO）", key="ticker_input")
    with col_b:
        period = st.selectbox("資料區間", ["1d", "5d", "1mo", "3mo", "6mo"], index=2)
    with col_c:
        interval = st.selectbox("K線週期", ["1d", "1h", "30m", "5m", "1m"], index=0)

    if interval == "1m" and period not in ("1d", "5d"):
        period = "1d"
        st.caption("已自動把資料區間改為「1d」，因為 Yahoo Finance 的 1 分鐘K線最多只提供最近幾天的資料。")

    if ticker_input:
        try:
            df = yf.download(ticker_input, period=period, interval=interval, progress=False)
        except Exception as e:
            df = pd.DataFrame()
            st.error(f"資料下載失敗：{e}")

        if df.empty:
            st.error("查無資料，請確認股票代號格式（例如台積電為 2330.TW）")
        else:
            # yfinance 新版下載單一股票有時仍會回傳多層欄位索引(MultiIndex)，
            # 例如 ('High', '2330.TW')，這裡統一把它扁平化成一般欄位，避免後面計算出錯
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna()

            st.caption(f"目前顯示：{ticker_input}｜資料區間 {period}｜K線週期 {interval}")

            # ---- K線圖 ----
            fig = go.Figure(
                data=[
                    go.Candlestick(
                        x=df.index,
                        open=df["Open"],
                        high=df["High"],
                        low=df["Low"],
                        close=df["Close"],
                        name="K線",
                        increasing_line_color="#c0392b",
                        decreasing_line_color="#1e8449",
                    )
                ]
            )

            # ---- 頸線標記 ----
            neckline = detect_neckline(df)
            if neckline["type"]:
                neck_price = neckline["points"]["price"]
                fig.add_hline(
                    y=neck_price,
                    line_dash="dash",
                    line_color="orange",
                    annotation_text=f"頸線：{neckline['type']}",
                    annotation_position="top left",
                )

            # ---- 壓力/支撐區 ----
            zones = volume_profile_zones(df)
            for z in zones:
                fig.add_hrect(y0=z["low"], y1=z["high"], fillcolor="LightSalmon", opacity=0.25, line_width=0)

            # ---- VWAP（僅在分鐘級資料時顯示較有意義）----
            if interval in MINUTE_INTERVALS:
                vwap = compute_vwap(df)
                fig.add_trace(go.Scatter(x=df.index, y=vwap, name="VWAP", line=dict(color="blue", width=1)))

            fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig, use_container_width=True)

            col_left, col_right = st.columns(2)

            with col_left:
                st.subheader("頸線型態")
                if neckline["type"]:
                    st.write(f"辨識到型態：**{neckline['type']}**")
                    st.write(f"頸線價位約：**{neckline['points']['price']:.2f}**")
                else:
                    st.write("目前資料未辨識出明顯的雙重頂/底或頭肩型態")
                    st.caption("可調整K線週期或資料區間再觀察")

                st.subheader("壓力／支撐區間")
                if zones:
                    for z in sorted(zones, key=lambda x: x["low"]):
                        st.write(f"- {z['low']:.2f} ~ {z['high']:.2f}　（成交量權重：{z['volume']:,.0f}）")
                else:
                    st.write("資料不足，暫時無法計算壓力/支撐區")

            with col_right:
                st.subheader("今日量能預估")
                if interval in MINUTE_INTERVALS:
                    current_time_str = datetime.now().strftime("%H:%M")
                    current_cum_vol = float(df["Volume"].sum())
                    est = estimate_full_day_volume(ticker_input, current_cum_vol, current_time_str)
                    if est:
                        st.write(
                            f"依過去 {est['sample_days']} 個交易日同時段的量能比例"
                            f"（約 {est['avg_ratio'] * 100:.1f}%）推算："
                        )
                        st.write(f"預估今日全日成交量約為 **{est['estimated_full_day_volume']:,.0f}** 股")
                    else:
                        st.write("歷史分鐘資料不足，暫時無法估算全日量")
                else:
                    st.write("請將「K線週期」切換為 1m / 5m / 30m / 1h，才能估算當日累積量與全日量")

# ============================================================
# Tab 4：理財達人秀彙整
# ============================================================
with tab4:
    st.caption(
        "彙整自《理財達人秀 EBCmoneyshow》YouTube頻道（東森財經台製作）的最新影片標題與連結，"
        "純粹整理呈現，不代表本系統認同其分析或個股推薦。節目本身也聲明「與分析師所推介個股無不當之財務利益關係，"
        "資料僅供參考」，投資決策請自行獨立判斷。"
    )

    with st.spinner("讀取理財達人秀最新影片中..."):
        videos = _get_ebc_videos_cached()

    if not videos:
        st.error("目前無法取得頻道資料，可能是網路問題、YouTube 暫時無法讀取，或伺服器沒有安裝 feedparser 套件")
    else:
        top15_symbols = {item["symbol"] for item in _scored_candidates[:15]}

        for v in videos:
            with st.container(border=True):
                st.markdown(f"**[{v['title']}]({v['link']})**")
                st.caption(f"發布時間：{v['published']}")

                if v["mentioned_stocks"]:
                    parts = []
                    for m in v["mentioned_stocks"]:
                        tag = "  ⭐今日當沖評分前15名" if m["symbol"] in top15_symbols else ""
                        parts.append(f"{m['name']}（{m['symbol']}）{tag}")
                    st.write("🔎 標題提及候選股：" + "、".join(parts))
                else:
                    st.write("🔎 標題中未比對到候選股清單內的個股")

        st.caption(
            "「標題提及候選股」只是簡單的文字比對（標題裡有沒有出現候選股清單中的公司名稱），"
            "不是語意分析，標題沒提到不代表節目沒討論，提到也不代表是明確的買賣建議。"
        )

st.divider()
st.caption(
    "本系統僅為技術面觀察工具，所有標記（頸線、壓力/支撐區、量能預估、當沖評分）皆為統計推論，不構成任何投資建議。"
    "當沖交易風險極高，請自行評估風險，並確認你的券商當沖資格與相關規則後再進行實際交易。"
)
