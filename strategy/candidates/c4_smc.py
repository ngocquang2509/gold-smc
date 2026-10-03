"""
C4 — SMC rút gọn trên M15 (ADR 0001, Bake-off): sweep → CHoCH → limit retest.

Chiều mua (chiều bán đối xứng):
  1. Swing low/high = pivot strict với `swing_lookback` bar mỗi bên, chỉ BIẾT tại bar
     p+L (đủ bar bên phải). Code legacy-v1 cố định độ trễ xác nhận = 3 → nhìn tương
     lai khi lookback > 3; ở đây độ trễ luôn = L.
  2. Sweep: low < swing low gần nhất nhưng close > nó (quét râu rồi quay lại).
     Close < swing low = PHÁ đáy (không phải sweep). Cả hai đều tiêu thụ mức đó.
  3. CHoCH: trong `setup_age` bar sau sweep, close > swing high gần nhất → tín hiệu.
     Setup bị hủy nếu có close dưới râu quét.
  4. Buy LIMIT tại chính đỉnh vừa bị phá (retest), sống `setup_age` bar.
     SL = râu quét − sl_buffer_atr·ATR. TP = target_r × rủi ro.

Bỏ so với legacy: trend H4, OB/FVG, discount/premium, pool thanh khoản, ADX/ATR gate.
4 tham số tune: swing_lookback, sl_buffer_atr, setup_age, target_r. ATR_PERIOD cố định.
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr

ATR_PERIOD = 14
NAN = float("nan")


def _confirmed_pivots(x: pd.Series, n: int, high: bool) -> np.ndarray:
    """Hàng i = giá pivot tại p = i-n nếu bar i XÁC NHẬN nó (strict hơn n bar mỗi bên),
    ngược lại NaN. Chỉ dùng bar ≤ i."""
    v = x if high else -x
    centre = v.shift(n)
    ok = (centre > v.shift(n + 1).rolling(n).max()) & (centre > v.rolling(n).max())
    return np.where(ok, x.shift(n), np.nan)


class StrippedSMC(Candidate):
    def signals(self, bars: pd.DataFrame, swing_lookback: int, sl_buffer_atr: float,
                setup_age: int, target_r: float) -> pd.DataFrame:
        n = len(bars)
        h, l, c = (bars[k].to_numpy(float) for k in ("high", "low", "close"))
        a = atr(bars, ATR_PERIOD).to_numpy()
        new_sh = _confirmed_pivots(bars["high"], swing_lookback, high=True)
        new_sl = _confirmed_pivots(bars["low"], swing_lookback, high=False)

        sig = np.zeros(n, dtype=int)
        px, sl = np.full(n, np.nan), np.full(n, np.nan)
        swing_hi = swing_lo = NAN            # swing gần nhất chưa bị chạm
        bull_at, bull_ext = -1, NAN          # sweep đáy đang chờ CHoCH tăng
        bear_at, bear_ext = -1, NAN          # sweep đỉnh đang chờ CHoCH giảm
        for i in range(n):
            # 1. Swing mới xác nhận tại bar này.
            if not np.isnan(new_sh[i]):
                swing_hi = new_sh[i]
            if not np.isnan(new_sl[i]):
                swing_lo = new_sl[i]
            # 2. Setup hết hạn hoặc thất bại (đóng vượt qua râu quét).
            if bull_at >= 0 and (i - bull_at > setup_age or c[i] < bull_ext):
                bull_at = -1
            if bear_at >= 0 and (i - bear_at > setup_age or c[i] > bear_ext):
                bear_at = -1
            # 3. Bar này chạm swing: đóng vượt = phá cấu trúc, đóng lại bên trong = sweep.
            broke_up = broke_dn = NAN
            sweep_lo = sweep_hi = False
            if h[i] > swing_hi:
                if c[i] > swing_hi:
                    broke_up = swing_hi
                else:
                    sweep_hi = True
                swing_hi = NAN
            if l[i] < swing_lo:
                if c[i] < swing_lo:
                    broke_dn = swing_lo
                else:
                    sweep_lo = True
                swing_lo = NAN
            # 4. CHoCH từ setup của các bar TRƯỚC (sweep ở chính bar này chưa tính).
            go_long = bull_at >= 0 and not np.isnan(broke_up) and not np.isnan(a[i])
            go_short = bear_at >= 0 and not np.isnan(broke_dn) and not np.isnan(a[i])
            if go_long and not go_short:
                stop = bull_ext - sl_buffer_atr * a[i]
                if broke_up > stop:
                    sig[i], px[i], sl[i] = 1, broke_up, stop
                bull_at = -1
            elif go_short and not go_long:
                stop = bear_ext + sl_buffer_atr * a[i]
                if broke_dn < stop:
                    sig[i], px[i], sl[i] = -1, broke_dn, stop
                bear_at = -1
            # 5. Ghi nhận sweep mới.
            if sweep_lo:
                bull_at, bull_ext = i, l[i]
            if sweep_hi:
                bear_at, bear_ext = i, h[i]

        s = empty_signals(bars.index)
        on = sig != 0
        s["signal"] = sig
        s.loc[on, "entry_type"] = "limit"
        s.loc[on, "expiry"] = setup_age
        s["entry_price"], s["sl"] = px, sl
        s["tp"] = px + target_r * (px - sl)
        return s


CANDIDATE = StrippedSMC(
    name="c4_smc",
    timeframe="M15",
    param_grid={
        "swing_lookback": [3, 6, 12],
        "sl_buffer_atr": [0.1, 0.5],
        "setup_age": [16, 48],           # 4h / 12h trên M15
        "target_r": [1.5, 2.5, 4.0],
    },
)
