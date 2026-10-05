"""
A — H4 mean reversion (z-fade) (ADR 0002, Bake-off #2).

Độ căng: z = (close − SMA(n)) / ATR(14). Vào lệnh NGƯỢC độ căng khi z vừa vượt ngưỡng:
mua ở bar ĐẦU TIÊN z ≤ −k (bar trước z > −k), bán ở bar đầu tiên z ≥ +k. Sự kiện, không
phải mức: sau khi bị SL/time stop mà giá vẫn còn căng thì KHÔNG vào lại ngay.
Lệnh market tại open bar kế. SL = close ∓ stop_atr·ATR. Không TP.
Thoát: flat ở close ĐẦU TIÊN cắt qua SMA (hồi về trung bình xong); time stop max_bars.
Không lọc xu hướng (sẽ tốn tham số thứ 5).

4 tham số tune (n, k, stop_atr, max_bars). ATR_PERIOD cố định.
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr

ATR_PERIOD = 14


class ZFade(Candidate):
    def signals(self, bars: pd.DataFrame, n: int, k: float, stop_atr: float, max_bars: int) -> pd.DataFrame:
        close = bars["close"]
        a = atr(bars, ATR_PERIOD)
        sma = close.rolling(n).mean()
        z = (close - sma) / a
        prev = z.shift(1)
        long_ = (z <= -k) & (prev > -k)           # NaN so sánh = False → chưa ấm thì không tín hiệu
        short = (z >= k) & (prev < k)

        s = empty_signals(bars.index)
        s.loc[long_, "signal"] = 1
        s.loc[short, "signal"] = -1
        s["sl"] = np.where(long_, close - stop_atr * a, np.where(short, close + stop_atr * a, np.nan))
        side = np.sign(close - sma)
        s["flat"] = (side != side.shift(1)) & sma.notna() & sma.shift(1).notna()
        s["max_bars"] = float(max_bars)
        return s


CANDIDATE = ZFade(
    name="a_zfade",
    timeframe="H4",
    param_grid={
        "n": [10, 20, 40],               # 10 bar H4 ≈ 1,7 ngày; 40 ≈ 1,3 tuần
        "k": [1.5, 2.0, 2.5],
        "stop_atr": [2.0, 3.0],
        "max_bars": [6, 12],             # 1 / 2 ngày giao dịch
    },
)
