"""
F — D1 hướng lợi suất thực Mỹ 10 năm (DFII10, ALFRED lần công bố đầu) (ADR 0003, Bake-off #3).

Δ = DFII10 (đã biết lúc đóng bar) − DFII10 của L bar D1 trước; z = Δ / σ_L, σ_L = độ lệch
chuẩn các Δ trong 250 bar trước đó (cố định). Bar Chủ Nhật (mẩu ~2h) bị loại khỏi mọi phép
tính và không phát tín hiệu/flat, như b_tsmom. Lợi suất công bố ~20:15–21:15 UTC → sớm nhất
vào lệnh ở open 00:00 UTC kế tiếp.
Lợi suất thực TĂNG = USD mạnh → BÁN mọi symbol; GIẢM → MUA. Vào theo MỨC (z ≥ +k và > 0 →
bán; z ≤ −k và < 0 → mua). Thoát: flat khi z đổi dấu; SL = close ∓ stop_atr·ATR(20). Không TP.
Đã biết: có thể không đủ 200 lệnh OOS; swap mua vàng.

3 tham số tune (lookback, k, stop_atr).
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr, sign_flips

ATR_PERIOD = 20
SIGMA_BARS = 250


class RealYieldDirection(Candidate):
    def signals(self, bars: pd.DataFrame, aux: pd.DataFrame, lookback: int, k: float,
                stop_atr: float) -> pd.DataFrame:
        keep = bars.index.dayofweek != 6         # bỏ mẩu Chủ Nhật
        wk = bars[keep]
        close = wk["close"]
        a = atr(wk, ATR_PERIOD)
        y = aux.loc[keep, "DFII10"]
        d = y - y.shift(lookback)
        z = d / d.rolling(SIGMA_BARS).std().shift(1)
        ok = z.notna() & a.notna()
        sell = ok & (z >= k) & (z > 0)           # lợi suất thực tăng = USD mạnh
        buy = ok & (z <= -k) & (z < 0)

        s = empty_signals(bars.index)
        s["flat"] = False
        sig = np.where(sell, -1, np.where(buy, 1, 0))
        s.loc[wk.index, "signal"] = sig
        s.loc[wk.index, "sl"] = np.where(buy, close - stop_atr * a, np.where(sell, close + stop_atr * a, np.nan))
        s.loc[wk.index, "flat"] = sign_flips(z)
        return s


CANDIDATE = RealYieldDirection(
    name="f_realyield",
    timeframe="D1",
    aux=["DFII10"],
    param_grid={
        "lookback": [5, 20, 60],         # bar D1 giao dịch ≈ 1 tuần / 1 tháng / 3 tháng
        "k": [0.0, 0.5],
        "stop_atr": [3.0, 4.0],
    },
)
