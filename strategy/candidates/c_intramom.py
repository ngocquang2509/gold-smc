"""
C — H1 intraday session momentum (ADR 0002, Bake-off #2).

Biến động buổi sáng = close bar H1 kết thúc 08:00 New York − open bar H1 bắt đầu 08:00
London (giờ ĐỊA PHƯƠNG, tự theo DST; tuần lệch DST Mỹ/Anh cửa sổ 4h thay vì 5h). Giờ
neo là cấu trúc thị trường, không tune.
|biến động| ≥ k · ATR D1(14) (chỉ từ các ngày ĐÃ ĐÓNG trước đó, dựng từ chính bar H1) →
vào theo hướng biến động tại open 08:00 New York. Tối đa 1 lệnh/ngày. Thiếu 1 trong 2
bar neo (lễ, 25/12, 1/1) → bỏ ngày đó.
SL = close ∓ stop_atr·ATR H1(14). Không TP. flat lúc 16:00 New York (trong ngày: không swap).
Cùng họ "dòng tiền theo phiên" với C2 (đã đóng) nhưng trigger khác: không phá range,
hướng quyết định 1 lần tại giờ cố định.

2 tham số tune (k, stop_atr).
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals
from strategy.indicators import atr

ATR_PERIOD = 14
LONDON, NY = "Europe/London", "America/New_York"
FLAT_HOUR = 16                                   # giờ New York


def _local(index: pd.DatetimeIndex, tz: str) -> pd.DatetimeIndex:
    """Index UTC (naive) → giờ địa phương naive (giờ treo tường, đã theo DST)."""
    return index.tz_localize("UTC").tz_convert(tz).tz_localize(None)


def daily_atr_prior(bars: pd.DataFrame) -> pd.Series:
    """ATR D1(14) dựng từ H1 (ngày UTC = ngày server, bỏ mẩu Chủ Nhật), lùi 1 ngày:
    giá trị ở ngày d chỉ dùng các ngày < d (đã đóng)."""
    h = bars[bars.index.dayofweek != 6]
    d = h.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return atr(d, ATR_PERIOD).shift(1)


class IntradayMomentum(Candidate):
    def signals(self, bars: pd.DataFrame, k: float, stop_atr: float) -> pd.DataFrame:
        lon, ny = _local(bars.index, LONDON), _local(bars.index, NY)
        start = pd.Series(bars["open"].to_numpy()[lon.hour == 8], index=lon[lon.hour == 8].normalize())
        is_end = ny.hour == 7                    # bar 07:00–08:00 NY, đóng lúc 08:00 NY
        end_at = bars.index[is_end]
        end = pd.DataFrame({"close": bars["close"].to_numpy()[is_end], "at": end_at},
                           index=ny[is_end].normalize())
        day = end.join(start.rename("open"), how="inner")   # thiếu bar neo → bỏ ngày
        move = day["close"] - day["open"]

        datr = daily_atr_prior(bars)
        day_utc = pd.DatetimeIndex(day["at"]).normalize()
        thr = k * datr.reindex(day_utc).to_numpy()
        a = atr(bars, ATR_PERIOD)
        a_at = a.reindex(pd.DatetimeIndex(day["at"])).to_numpy()
        go = (move.to_numpy() != 0) & (np.abs(move.to_numpy()) >= thr) & ~np.isnan(a_at)
        at = pd.DatetimeIndex(day["at"])[go]
        side = np.sign(move.to_numpy()[go]).astype(int)
        c = day["close"].to_numpy()[go]

        s = empty_signals(bars.index)
        s.loc[at, "signal"] = side
        s.loc[at, "sl"] = c - side * stop_atr * a_at[go]
        s["flat"] = ny.hour >= FLAT_HOUR - 1     # bar 15:00 NY đóng → đóng lệnh ở open 16:00
        return s


CANDIDATE = IntradayMomentum(
    name="c_intramom",
    timeframe="H1",
    param_grid={
        "k": [0.0, 0.25, 0.5],           # ngưỡng biến động sáng, đơn vị ATR D1
        "stop_atr": [2.0, 3.0],
    },
)
