"""
Fair Value Gap (FVG): khoảng trống giá 3 nến thể hiện imbalance.
- Bullish FVG: low của nến 3 > high của nến 1.
- Bearish FVG: high của nến 3 < low của nến 1.
Giá có xu hướng quay lại "lấp" FVG — dùng làm vùng entry.
"""
import pandas as pd
from dataclasses import dataclass


@dataclass
class FVG:
    index: int            # index của nến giữa
    direction: str        # "bullish" | "bearish"
    top: float
    bottom: float
    time: pd.Timestamp
    filled: bool = False


def find_fvgs(df: pd.DataFrame, min_size: float = 0.5,
              max_age_bars: int = 80) -> list[FVG]:
    fvgs: list[FVG] = []
    h, l, c = df["high"].values, df["low"].values, df["close"].values
    n = len(df)

    for i in range(1, n - 1):
        # Bullish FVG
        if l[i + 1] > h[i - 1] and (l[i + 1] - h[i - 1]) >= min_size:
            fvgs.append(FVG(i, "bullish", top=l[i + 1], bottom=h[i - 1], time=df.index[i]))
        # Bearish FVG
        elif h[i + 1] < l[i - 1] and (l[i - 1] - h[i + 1]) >= min_size:
            fvgs.append(FVG(i, "bearish", top=l[i - 1], bottom=h[i + 1], time=df.index[i]))

    # Đánh dấu FVG đã bị lấp hoàn toàn hoặc quá cũ
    for f in fvgs:
        for k in range(f.index + 2, n):
            if f.direction == "bullish" and c[k] < f.bottom:
                f.filled = True
                break
            if f.direction == "bearish" and c[k] > f.top:
                f.filled = True
                break
        if n - f.index > max_age_bars:
            f.filled = True

    return [f for f in fvgs if not f.filled]
