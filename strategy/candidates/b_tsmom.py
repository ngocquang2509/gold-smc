"""
B — D1 time-series momentum (ADR 0002, Bake-off #2).

Tín hiệu là TRẠNG THÁI (mức): dấu của close / close[L] − 1, tính ở mọi close D1. Không có
vị thế mà dấu ≠ 0 → vào market tại open bar kế (sau SL vẫn còn dấu → vào lại).
SL = close ∓ stop_atr·ATR(20). Không TP. Thoát: flat khi dấu đổi.

Bar D1 Chủ Nhật (mẩu ~2h do resample theo 00:00 UTC) bị LOẠI khỏi L, dấu và ATR, và
không phát tín hiệu/flat — engine vẫn quản lý SL qua các bar đó (giá thật).
Đã biết: swap mua vàng 0.03–0.11R mỗi tuần giữ lệnh; có thể không đủ 200 lệnh OOS.

2 tham số tune (lookback, stop_atr). ATR_PERIOD cố định.
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr

ATR_PERIOD = 20


class TSMomentum(Candidate):
    def signals(self, bars: pd.DataFrame, lookback: int, stop_atr: float) -> pd.DataFrame:
        wk = bars[bars.index.dayofweek != 6]     # bỏ mẩu Chủ Nhật
        close = wk["close"]
        a = atr(wk, ATR_PERIOD)
        ret = close / close.shift(lookback) - 1
        ok = ret.notna() & a.notna()
        sgn = np.sign(ret).where(ok, 0).astype(int)
        flip = ok & ok.shift(1, fill_value=False) & (sgn != sgn.shift(1))

        s = empty_signals(bars.index)
        s["flat"] = False
        s.loc[wk.index, "signal"] = sgn
        s.loc[wk.index, "sl"] = np.where(sgn > 0, close - stop_atr * a,
                                         np.where(sgn < 0, close + stop_atr * a, np.nan))
        s.loc[wk.index, "flat"] = flip
        return s


CANDIDATE = TSMomentum(
    name="b_tsmom",
    timeframe="D1",
    param_grid={
        "lookback": [60, 120, 250],      # ngày giao dịch: ~3 / 6 / 12 tháng
        "stop_atr": [3.0, 4.0, 6.0],
    },
)
