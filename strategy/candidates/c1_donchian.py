"""
C1 — H4 Donchian trend (ADR 0001, Bake-off).

Vào lệnh: close H4 vượt đỉnh (thủng đáy) của kênh N bar TRƯỚC đó — chỉ bar đầu tiên
vượt (sự kiện breakout), không phát lại mỗi bar khi giá còn ở ngoài kênh.
Lệnh market tại open bar kế. SL = close ∓ stop_atr·ATR. Không TP.
Thoát: trailing stop close ∓ trail_atr·ATR (engine chỉ dời SL theo hướng có lợi
→ thành mức trượt một chiều kiểu chandelier trên giá close).

3 tham số tune (N, stop_atr, trail_atr). ATR_PERIOD cố định, không tune.
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr

ATR_PERIOD = 20


class DonchianTrend(Candidate):
    def signals(self, bars: pd.DataFrame, n: int, stop_atr: float, trail_atr: float) -> pd.DataFrame:
        close = bars["close"]
        a = atr(bars, ATR_PERIOD)
        # Kênh của N bar TRƯỚC bar hiện tại (shift 1) → bar i không tự so với chính nó.
        upper = bars["high"].rolling(n).max().shift(1)
        lower = bars["low"].rolling(n).min().shift(1)
        above, below = close > upper, close < lower
        long_ = above & ~above.shift(1, fill_value=False) & a.notna()
        short = below & ~below.shift(1, fill_value=False) & a.notna()

        s = empty_signals(bars.index)
        s.loc[long_, "signal"] = 1
        s.loc[short, "signal"] = -1
        s["sl"] = np.where(long_, close - stop_atr * a, np.where(short, close + stop_atr * a, np.nan))
        s["trail_long"] = close - trail_atr * a
        s["trail_short"] = close + trail_atr * a
        return s


CANDIDATE = DonchianTrend(
    name="c1_donchian",
    timeframe="H4",
    param_grid={
        "n": [20, 40, 80, 160],          # 20 bar H4 ≈ 3,3 ngày giao dịch; 160 ≈ 1 tháng
        "stop_atr": [2.0, 3.0],
        "trail_atr": [3.0, 4.0, 6.0],
    },
    retired="Bake-off #1 2026-10-04: trượt walk-forward (ADR 0001 Outcomes)",
)
