"""
Market Structure: swing highs/lows, Break of Structure (BOS), Change of Character (CHoCH).
Đây là nền tảng của SMC — mọi thứ khác (OB, FVG, sweep) đều tham chiếu về cấu trúc.
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass


@dataclass
class Swing:
    index: int          # vị trí trong DataFrame
    price: float
    kind: str           # "high" | "low"
    time: pd.Timestamp


@dataclass
class StructureEvent:
    index: int
    kind: str           # "BOS" | "CHOCH"
    direction: str      # "bullish" | "bearish"
    broken_level: float
    time: pd.Timestamp


def find_swings(df: pd.DataFrame, lookback: int) -> list[Swing]:
    """
    Swing high: nến có high cao hơn `lookback` nến trái VÀ phải.
    Swing low: tương tự với low.
    """
    swings = []
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    for i in range(lookback, n - lookback):
        window_h = highs[i - lookback: i + lookback + 1]
        window_l = lows[i - lookback: i + lookback + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            swings.append(Swing(i, highs[i], "high", df.index[i]))
        elif lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            swings.append(Swing(i, lows[i], "low", df.index[i]))
    return swings


def detect_structure(df: pd.DataFrame, swings: list[Swing]) -> tuple[list[StructureEvent], str]:
    """
    Duyệt qua các swing để phát hiện BOS/CHoCH bằng giá đóng cửa vượt swing gần nhất.

    Quy tắc:
    - Trend bullish + close > swing high gần nhất  → BOS bullish (tiếp diễn)
    - Trend bullish + close < swing low gần nhất   → CHoCH bearish (đảo chiều)
    - Ngược lại cho bearish.

    Trả về: (danh sách sự kiện, xu hướng hiện tại "bullish"/"bearish"/"neutral")
    """
    events: list[StructureEvent] = []
    trend = "neutral"
    last_high: Swing | None = None
    last_low: Swing | None = None
    closes = df["close"].values

    swing_iter = iter(swings)
    next_swing = next(swing_iter, None)

    for i in range(len(df)):
        # Cập nhật swing đã "xác nhận" tính đến nến i (swing cần lookback nến phải)
        while next_swing is not None and next_swing.index + _confirm_offset(swings) <= i:
            if next_swing.kind == "high":
                last_high = next_swing
            else:
                last_low = next_swing
            next_swing = next(swing_iter, None)

        c = closes[i]
        if last_high and c > last_high.price:
            kind = "BOS" if trend in ("bullish", "neutral") else "CHOCH"
            events.append(StructureEvent(i, kind, "bullish", last_high.price, df.index[i]))
            trend = "bullish"
            last_high = None  # đã phá — chờ swing mới
        elif last_low and c < last_low.price:
            kind = "BOS" if trend in ("bearish", "neutral") else "CHOCH"
            events.append(StructureEvent(i, kind, "bearish", last_low.price, df.index[i]))
            trend = "bearish"
            last_low = None

    return events, trend


def _confirm_offset(swings: list[Swing]) -> int:
    # Swing được xác nhận sau khi đủ nến phải hình thành; suy ra từ khoảng cách phổ biến
    # Đơn giản hoá: dùng 3 (khớp swing_lookback mặc định). Ghi đè nếu cần.
    return 3


def current_trend_htf(df: pd.DataFrame, lookback: int, max_trend_age: int = 0) -> dict:
    """Phân tích HTF: trả về trend + swing levels quan trọng để làm TP/context.

    #8 — Nếu `max_trend_age` > 0 và cú BOS/CHoCH HTF gần nhất đã cách hiện tại quá
    `max_trend_age` nến (hoặc chưa từng có sự kiện nào) → coi thị trường ĐI NGANG,
    ép trend = "neutral" để đứng ngoài thay vì ôm thiên hướng cũ đã nguội."""
    swings = find_swings(df, lookback)
    events, trend = detect_structure(df, swings)
    if max_trend_age > 0:
        if not events or (len(df) - 1) - events[-1].index > max_trend_age:
            trend = "neutral"
    recent_highs = [s.price for s in swings if s.kind == "high"][-3:]
    recent_lows = [s.price for s in swings if s.kind == "low"][-3:]
    return {
        "trend": trend,
        "events": events,
        "swings": swings,
        "recent_highs": recent_highs,
        "recent_lows": recent_lows,
    }
