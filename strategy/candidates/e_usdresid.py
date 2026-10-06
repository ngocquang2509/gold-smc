"""
E — H4 mean reversion trên phần dư KHÔNG giải thích bởi USD5 (ADR 0003, Bake-off #3).

Phần dư mỗi bar: ε_i = r_sym_i − β_i · r_USD5_i (lợi nhuận log), β_i = OLS cuộn trên 500 bar
TRƯỚC bar i (cố định). Độ căng: z = Σε của n bar gần nhất / (σ_ε·√n), σ_ε trong 500 bar.
Vào lệnh theo SỰ KIỆN (như a_zfade): bar ĐẦU TIÊN z ≤ −k → mua tại open bar kế; z ≥ +k → bán.
Thoát: flat khi z cắt qua 0; time stop max_bars; SL = close ∓ stop_atr·ATR(14). Không TP.
Cùng họ "fade" với a_zfade (đã đóng) nhưng chỉ fade phần riêng của symbol, không phải giá
tổng. EUR/GBP chiếm 1/5 rổ USD5 nên phần dư của EURUSD/GBPUSD bị nén nhẹ (đã ghi nhận).

4 tham số tune (n, k, stop_atr, max_bars).
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr, sign_flips

ATR_PERIOD = 14
BETA_BARS = 500


def residual_z(close: pd.Series, usd5: pd.Series, n: int) -> pd.Series:
    r_sym = np.log(close).diff()
    r_usd = np.log(usd5).diff()
    beta = (r_sym.rolling(BETA_BARS).cov(r_usd) / r_usd.rolling(BETA_BARS).var()).shift(1)
    eps = r_sym - beta * r_usd
    sig = eps.rolling(BETA_BARS).std()
    return eps.rolling(n).sum() / (sig * np.sqrt(n))


class USDResidualFade(Candidate):
    def signals(self, bars: pd.DataFrame, aux: pd.DataFrame, n: int, k: float, stop_atr: float,
                max_bars: int) -> pd.DataFrame:
        close = bars["close"]
        a = atr(bars, ATR_PERIOD)
        z = residual_z(close, aux["USD5"], n)
        prev = z.shift(1)
        long_ = (z <= -k) & (prev > -k) & a.notna()
        short = (z >= k) & (prev < k) & a.notna()

        s = empty_signals(bars.index)
        s.loc[long_, "signal"] = 1
        s.loc[short, "signal"] = -1
        s["sl"] = np.where(long_, close - stop_atr * a, np.where(short, close + stop_atr * a, np.nan))
        s["flat"] = sign_flips(z)
        s["max_bars"] = float(max_bars)
        return s


CANDIDATE = USDResidualFade(
    name="e_usdresid",
    timeframe="H4",
    aux=["USD5"],
    param_grid={
        "n": [6, 12, 24],                # 1 / 2 / 4 ngày giao dịch
        "k": [1.5, 2.0, 2.5],
        "stop_atr": [2.0, 3.0],
        "max_bars": [6, 12],
    },
)
