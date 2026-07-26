"""
analysis.py
個股技術分析核心邏輯：頸線型態辨識、壓力/支撐區（成交量分布）、當日量能預估。

這裡的演算法都是「簡化版」的統計啟發式規則，目的是先讓系統能動起來、看得到標記，
之後你可以依照自己的交易邏輯調整參數（例如 order、bins、lookback）或替換成更嚴謹的演算法。
"""

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
import yfinance as yf


def find_troughs_peaks(df, order=5):
    """用局部極值找出波峰(peak)與波谷(trough)的索引位置"""
    close = df["Close"].values
    peak_idx = argrelextrema(close, np.greater, order=order)[0]
    trough_idx = argrelextrema(close, np.less, order=order)[0]
    return peak_idx, trough_idx


def detect_neckline(df, order=5, lookback=60):
    """
    偵測近期是否有雙重頂/底或頭肩頂/底型態，並回傳對應的頸線價位。

    邏輯：
    - 底部型態：找兩個相近的低點，中間夾一個反彈高點 -> 頸線 = 反彈高點價位
    - 頭部型態：找兩個相近的高點，中間夾一個回檔低點 -> 頸線 = 回檔低點價位

    回傳: {"type": str|None, "points": dict|None}
    """
    recent = df.tail(lookback).reset_index(drop=True)
    if len(recent) < order * 3:
        return {"type": None, "points": None}

    peak_idx, trough_idx = find_troughs_peaks(recent, order=order)

    # 底部型態
    if len(trough_idx) >= 2 and len(peak_idx) >= 1:
        t1, t2 = trough_idx[-2], trough_idx[-1]
        mid_peaks = [p for p in peak_idx if t1 < p < t2]
        if mid_peaks:
            neck_idx = mid_peaks[-1]
            neck_price = float(recent["High"].iloc[neck_idx])
            return {
                "type": "底部型態（雙重底/頭肩底）",
                "points": {
                    "neck_index": int(neck_idx),
                    "price": neck_price,
                    "troughs": [
                        (int(t1), float(recent["Low"].iloc[t1])),
                        (int(t2), float(recent["Low"].iloc[t2])),
                    ],
                },
            }

    # 頭部型態
    if len(peak_idx) >= 2 and len(trough_idx) >= 1:
        p1, p2 = peak_idx[-2], peak_idx[-1]
        mid_troughs = [t for t in trough_idx if p1 < t < p2]
        if mid_troughs:
            neck_idx = mid_troughs[-1]
            neck_price = float(recent["Low"].iloc[neck_idx])
            return {
                "type": "頭部型態（雙重頂/頭肩頂）",
                "points": {
                    "neck_index": int(neck_idx),
                    "price": neck_price,
                    "peaks": [
                        (int(p1), float(recent["High"].iloc[p1])),
                        (int(p2), float(recent["High"].iloc[p2])),
                    ],
                },
            }

    return {"type": None, "points": None}


def volume_profile_zones(df, bins=24, top_n=3):
    """
    用成交量分布（Volume Profile）找出壓力/支撐區間：
    把價格切成 N 個區間，統計每個區間累積成交量，取成交量最大的前 top_n 個區間視為主要壓力/支撐區。
    """
    work = df.copy()
    price_min, price_max = float(work["Low"].min()), float(work["High"].max())
    if price_max <= price_min:
        return []

    bin_edges = np.linspace(price_min, price_max, bins + 1)
    work["mid_price"] = (work["High"] + work["Low"]) / 2
    work["bin"] = pd.cut(work["mid_price"], bins=bin_edges, include_lowest=True)

    vol_by_bin = work.groupby("bin", observed=True)["Volume"].sum().sort_values(ascending=False)

    zones = []
    for interval in vol_by_bin.index[:top_n]:
        zones.append({
            "low": float(interval.left),
            "high": float(interval.right),
            "volume": float(vol_by_bin[interval]),
        })
    return zones


def estimate_full_day_volume(ticker, current_cum_volume, current_time_str, days=10):
    """
    用「時間比例法」預估全日成交量：
    抓取過去 N 個交易日的 5 分鐘K線，計算「到目前這個時間點為止的累積量」佔「當日全天總量」的平均比例，
    再用今日目前的累積量反推預估全日量。

    :param ticker: yfinance 股票代號，例如 '2330.TW'
    :param current_cum_volume: 今日截至目前的累積成交量
    :param current_time_str: 'HH:MM' 格式的目前時間，例如 '09:30'
    :param days: 用過去幾個交易日的資料來建立時間比例基準
    """
    try:
        hist = yf.download(ticker, period=f"{days}d", interval="5m", progress=False)
    except Exception:
        return None

    if hist.empty:
        return None

    # 同樣處理 yfinance 新版可能回傳的 MultiIndex 欄位
    if isinstance(hist.columns, pd.MultiIndex):
        hist.columns = hist.columns.get_level_values(0)

    if hist.index.tz is not None:
        hist = hist.tz_localize(None)

    hist = hist.copy()
    hist["date"] = hist.index.date
    hist["time"] = hist.index.strftime("%H:%M")

    ratios = []
    for _, group in hist.groupby("date"):
        full_day_vol = group["Volume"].sum()
        if full_day_vol <= 0:
            continue
        cum_at_time = group[group["time"] <= current_time_str]["Volume"].sum()
        if cum_at_time > 0:
            ratios.append(cum_at_time / full_day_vol)

    if not ratios:
        return None

    avg_ratio = float(np.mean(ratios))
    if avg_ratio <= 0:
        return None

    estimated_full_day = current_cum_volume / avg_ratio
    return {
        "avg_ratio": avg_ratio,
        "estimated_full_day_volume": estimated_full_day,
        "sample_days": len(ratios),
    }


def compute_vwap(df):
    """計算成交量加權平均價 VWAP，用來判斷股價偏離度"""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    vwap = (typical_price * df["Volume"]).cumsum() / df["Volume"].cumsum()
    return vwap
