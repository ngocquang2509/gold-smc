"""
C2 — Session opening-range breakout trên M15 (ADR 0001, Bake-off).

Range = high/low của X phút đầu sau giờ mở phiên (London 08:00 giờ London, hoặc
NY 08:00 giờ New York — giờ ĐỊA PHƯƠNG nên tự theo DST; dữ liệu/server là UTC).
Khi bar cuối của range đóng: đặt 2 lệnh chờ OCO — buy stop ở range high, sell stop
ở range low. SL = cạnh kia của range. TP = k × độ rộng range tính từ entry.
Lệnh chờ hết hạn và vị thế bị đóng lúc 16:00 New York (chỉ trong ngày: không swap,
không gap cuối tuần).

3 tham số tune (x, k, session). Giờ mở phiên/đóng cửa là sự kiện thị trường, không tune;
dùng chung cho cả 3 symbol (Shared Parameter Set).
"""
import numpy as np
import pandas as pd

from strategy.candidate import Candidate, empty_signals

SESSIONS = {"london": ("Europe/London", 8 * 60), "ny": ("America/New_York", 8 * 60)}
FLAT_TZ, FLAT_MIN = "America/New_York", 16 * 60
BAR = pd.Timedelta(minutes=15)


def _local(index: pd.DatetimeIndex, tz: str) -> pd.DatetimeIndex:
    """Index UTC (naive) → giờ địa phương naive (giờ treo tường, đã theo DST)."""
    return index.tz_localize("UTC").tz_convert(tz).tz_localize(None)


class OpeningRangeBreakout(Candidate):
    def signals(self, bars: pd.DataFrame, x: int, k: float, session: str) -> pd.DataFrame:
        if x % 15:
            raise ValueError(f"x={x} phải là bội của 15 phút (bar M15)")
        tz, open_min = SESSIONS[session]
        loc = _local(bars.index, tz)
        tod = loc.hour * 60 + loc.minute
        in_range = (tod >= open_min) & (tod < open_min + x)

        rb = bars[in_range]
        g = rb.groupby(loc[in_range].normalize())
        rng = pd.DataFrame({"hi": g["high"].max(), "lo": g["low"].min(), "n": g.size(),
                            "last": rb.index.to_series().groupby(loc[in_range].normalize()).max()})
        # Chỉ phát tín hiệu khi range ĐỦ bar (bar cuối có mặt → nhân quả, ngày thiếu bar bị bỏ).
        rng = rng[(rng["n"] == x // 15) & (rng["hi"] > rng["lo"])]

        # Hết hạn lệnh chờ: 16:00 New York của ngày range kết thúc.
        end_utc = pd.DatetimeIndex(rng["last"]) + BAR
        flat_at = ((_local(end_utc, FLAT_TZ).normalize() + pd.Timedelta(minutes=FLAT_MIN))
                   .tz_localize(FLAT_TZ).tz_convert("UTC").tz_localize(None))
        expiry = np.asarray((flat_at - end_utc) // BAR, dtype=int)
        ok = expiry > 0
        rng, expiry = rng[ok], expiry[ok]

        s = empty_signals(bars.index)
        for col in ("oco_price", "oco_sl", "oco_tp"):
            s[col] = np.nan
        at = pd.DatetimeIndex(rng["last"])
        width = (rng["hi"] - rng["lo"]).to_numpy()
        hi, lo = rng["hi"].to_numpy(), rng["lo"].to_numpy()
        s.loc[at, "signal"] = 1
        s.loc[at, "entry_type"] = "stop"
        s.loc[at, "expiry"] = expiry
        s.loc[at, "entry_price"], s.loc[at, "sl"], s.loc[at, "tp"] = hi, lo, hi + k * width
        s.loc[at, "oco_price"], s.loc[at, "oco_sl"], s.loc[at, "oco_tp"] = lo, hi, lo - k * width

        ny = _local(bars.index, FLAT_TZ)
        s["flat"] = (ny.hour * 60 + ny.minute) >= FLAT_MIN - 15
        return s


CANDIDATE = OpeningRangeBreakout(
    name="c2_orb",
    timeframe="M15",
    param_grid={
        "x": [15, 30, 60],
        "k": [1.0, 1.5, 2.0, 3.0],
        "session": ["london", "ny"],
    },
    retired="Bake-off #1 2026-10-04: trượt walk-forward (ADR 0001 Outcomes)",
)
