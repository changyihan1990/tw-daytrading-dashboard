"""
app.py
台股當沖觀察儀表板 —— 本機 Streamlit 網頁儀表板

執行方式：
    streamlit run app.py

資料來源：Yahoo Finance（透過 yfinance 套件），延遲約 15-20 分鐘，僅供技術面觀察參考，非投資建議。
"""

from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

from analysis import compute_vwap, detect_neckline, estimate_full_day_volume, volume_profile_zones
from data_sources import get_market_snapshot

st.set_page_config(page_title="台股當沖觀察儀表板", layout="wide")

st.title("台股當沖觀察儀表板")
st.caption(
    f"資料來源：Yahoo Finance（延遲資料，僅供參考，非投資建議）　|　"
    f"頁面產生時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
)

# ============================================================
# 一、盤前全球市場總覽
# ============================================================
st.header("一、盤前全球市場總覽")
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
            st.metric(item["name"], f"{item['last']:.2f}", delta)

st.info(
    "台指期（TX）目前沒有可靠的免費即時 API，上方「台灣加權指數」只能作為方向性參考，"
    "正式判斷開盤走勢請搭配你的券商看盤軟體或期交所公開資訊觀測站。"
)

st.divider()

# ============================================================
# 二、個股技術分析
# ============================================================
st.header("二、個股技術分析")

col_a, col_b, col_c = st.columns(3)
with col_a:
    ticker_input = st.text_input("股票代號（上市加 .TW，上櫃加 .TWO）", value="2330.TW")
with col_b:
    period = st.selectbox("資料區間", ["5d", "1mo", "3mo", "6mo"], index=1)
with col_c:
    interval = st.selectbox("K線週期", ["1d", "1h", "30m", "5m"], index=0)

if ticker_input:
    try:
        df = yf.download(ticker_input, period=period, interval=interval, progress=False)
    except Exception as e:
        df = pd.DataFrame()
        st.error(f"資料下載失敗：{e}")

    if df.empty:
        st.error("查無資料，請確認股票代號格式（例如台積電為 2330.TW）")
    else:
        df = df.dropna()

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
        if interval in ("5m", "30m", "1h"):
            vwap = compute_vwap(df)
            fig.add_trace(go.Scatter(x=df.index, y=vwap, name="VWAP", line=dict(color="blue", width=1)))

        fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

        # ---- 文字說明：頸線 ----
        if neckline["type"]:
            st.write(f"辨識到型態：**{neckline['type']}**，頸線價位約 **{neckline['points']['price']:.2f}**")
        else:
            st.write("目前資料未辨識出明顯的雙重頂/底或頭肩型態（可調整K線週期或資料區間再觀察）")

        # ---- 文字說明：壓力/支撐 ----
        st.subheader("壓力／支撐區間（依成交量分布推算）")
        if zones:
            for z in sorted(zones, key=lambda x: x["low"]):
                st.write(f"- {z['low']:.2f} ~ {z['high']:.2f}　（區間成交量權重：{z['volume']:,.0f}）")
        else:
            st.write("資料不足，暫時無法計算壓力/支撐區")

        # ---- 今日量能預估 ----
        st.subheader("今日量能預估")
        if interval in ("5m", "30m", "1h"):
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
            st.write("請將「K線週期」切換為 5m / 30m / 1h，才能估算當日累積量與全日量")

st.divider()
st.caption(
    "本系統僅為技術面觀察工具，所有標記（頸線、壓力/支撐區、量能預估）皆為統計推論，不構成任何投資建議。"
    "當沖交易風險極高，請自行評估風險，並確認你的券商當沖資格與相關規則後再進行實際交易。"
)
