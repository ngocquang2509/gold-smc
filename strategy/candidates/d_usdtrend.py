"""
D — H4 dollar trend trên rổ USD5 (ADR 0003, Bake-off #3).

Độ mạnh xu hướng USD: z = log(USD5_i / USD5_{i−L}) / (σ₁·√L), σ₁ = độ lệch chuẩn lợi
nhuận log 1 bar của USD5 trong 500 bar gần nhất (cố định, không tune).
Cả 3 symbol đều yết theo USD → USD mạnh = BÁN mọi symbol, USD yếu = MUA.
Vào lệnh theo MỨC: không có vị thế, z ≥ +k (và > 0) → bán tại open bar kế; z ≤ −k → mua.
Thoát: flat khi z đổi dấu (k chỉ chặn lúc VÀO — trễ pha); SL = close ∓ stop_atr·ATR(20)
của symbol. Không TP. Đã biết: swap mua vàng ~0.09–0.27R mỗi tuần giữ lệnh.

3 tham số tune (lookback, k, stop_atr).
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr, sign_flips

ATR_PERIOD = 20
SIGMA_BARS = 500


def usd_trend_z(usd5: pd.Series, lookback: int) -> pd.Series:
    u = np.log(usd5)
    sigma = u.diff().rolling(SIGMA_BARS).std()
    return (u - u.shift(lookback)) / (sigma * np.sqrt(lookback))


class USDTrend(Candidate):
    def signals(self, bars: pd.DataFrame, aux: pd.DataFrame, lookback: int, k: float,
                stop_atr: float) -> pd.DataFrame:
        close = bars["close"]
        a = atr(bars, ATR_PERIOD)
        z = usd_trend_z(aux["USD5"], lookback)
        ok = z.notna() & a.notna()
        sell = ok & (z >= k) & (z > 0)           # USD mạnh
        buy = ok & (z <= -k) & (z < 0)           # USD yếu

        s = empty_signals(bars.index)
        s.loc[sell, "signal"] = -1
        s.loc[buy, "signal"] = 1
        s["sl"] = np.where(buy, close - stop_atr * a, np.where(sell, close + stop_atr * a, np.nan))
        s["flat"] = sign_flips(z)
        return s


CANDIDATE = USDTrend(
    name="d_usdtrend",
    timeframe="H4",
    aux=["USD5"],
    param_grid={
        "lookback": [30, 60, 120],       # bar H4 ≈ 1 / 2 / 4 tuần
        "k": [0.0, 0.5, 1.0],
        "stop_atr": [3.0, 5.0],
    },
)
