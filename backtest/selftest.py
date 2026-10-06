"""
Tự kiểm tra harness trên dữ liệu tổng hợp (không cần mạng/MT5/pytest).
Chạy sau MỌI thay đổi engine/walkforward/risk.manage_step:

    python -m backtest.selftest
"""
from dataclasses import replace

import numpy as np
import pandas as pd

from backtest.engine import simulate
from backtest.walkforward import assert_causal, equity_curve, max_drawdown_pct, profit_factor
from config.config import get_config, stressed
from strategy.candidate import Candidate, empty_signals

CFG0 = replace(get_config("gold"), spread_points=0.0, swap_long_per_lot=0.0, swap_short_per_lot=0.0)


def _bars(rows):
    idx = pd.date_range("2020-01-06", periods=len(rows), freq="1h")   # Thứ Hai, không qua đêm
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def _sig(bars, at, **kw):
    s = empty_signals(bars.index)
    for k, v in kw.items():
        s.loc[s.index[at], k] = v
    s.loc[s.index[at], "signal"] = kw.get("signal", 1)
    return s


def check(name, cond):
    print(f"  {'✅' if cond else '❌'} {name}")
    if not cond:
        raise SystemExit(1)


def test_engine():
    print("engine:")
    # Market buy ở open bar 1 = 100, SL 99, TP 102 → bar 2 chạm TP → +2R
    b = _bars([[100, 100, 100, 100], [100, 100.5, 99.5, 100], [100, 102.5, 99.8, 102], [102, 102, 102, 102]])
    tr = simulate(b, _sig(b, 0, sl=99.0, tp=102.0), CFG0)
    check("market → TP = +2R", len(tr) == 1 and abs(tr.r[0] - 2.0) < 1e-9 and tr.entry[0] == 100)

    # SL bị gap: bar 2 mở 98 dưới SL 99 → khớp 98 → -2R (không phải -1R)
    b = _bars([[100, 100, 100, 100], [100, 100.5, 99.5, 100], [98, 98.5, 97.5, 98], [98, 98, 98, 98]])
    tr = simulate(b, _sig(b, 0, sl=99.0, tp=103.0), CFG0)
    check("gap qua SL → khớp open, -2R", abs(tr.r[0] + 2.0) < 1e-9 and tr.result[0] == "SL-GAP")

    # SL trước TP khi cả hai cùng trong 1 bar → -1R
    b = _bars([[100, 100, 100, 100], [100, 103, 98, 100], [100, 100, 100, 100]])
    tr = simulate(b, _sig(b, 0, sl=99.0, tp=102.0), CFG0)
    check("SL & TP cùng bar → SL", abs(tr.r[0] + 1.0) < 1e-9)

    # Stop buy 101 khớp trong bar 1; bar khớp chạm TP nhưng KHÔNG được tính TP
    b = _bars([[100, 100, 100, 100], [100.5, 104, 100.5, 103], [103, 103.5, 102.5, 103], [103, 103, 103, 103]])
    tr = simulate(b, _sig(b, 0, entry_type="stop", entry_price=101.0, sl=100.0, tp=103.0, expiry=5), CFG0)
    check("stop khớp, bar khớp không tính TP; TP bar sau", tr.entry[0] == 101 and tr.exit_time[0] == b.index[2])

    # Stop buy bị gap: open 102 > 101 → khớp 102 (xấu hơn)
    b = _bars([[100, 100, 100, 100], [102, 102.5, 101.8, 102], [102, 102, 102, 102]])
    tr = simulate(b, _sig(b, 0, entry_type="stop", entry_price=101.0, sl=100.0, tp=110.0, expiry=5), CFG0)
    check("stop gap → khớp open 102", tr.entry[0] == 102)

    # Lệnh chờ hết hạn → không có lệnh
    b = _bars([[100, 100, 100, 100]] + [[100, 100.5, 99.5, 100]] * 5)
    tr = simulate(b, _sig(b, 0, entry_type="stop", entry_price=105.0, sl=100.0, tp=110.0, expiry=2), CFG0)
    check("lệnh chờ hết hạn → 0 lệnh", len(tr) == 0)

    # Trail: SL dời lên 101 sau bar 1 → bar 2 chạm 101 → +1R
    b = _bars([[100, 100, 100, 100], [100, 101.5, 99.5, 101.5], [101.5, 101.6, 100.8, 101], [101, 101, 101, 101]])
    s = _sig(b, 0, sl=99.0, tp=float("nan"))
    s["trail_long"] = [np.nan, 101.0, np.nan, np.nan]
    tr = simulate(b, s, CFG0)
    check("trail dời SL lên 101 → +1R", abs(tr.r[0] - 1.0) < 1e-9)

    # Flat: đóng tại open bar kế
    b = _bars([[100, 100, 100, 100], [100, 100.5, 99.5, 100.4], [100.5, 100.6, 100.4, 100.5], [100, 100, 100, 100]])
    s = _sig(b, 0, sl=99.0, tp=float("nan"))
    s["flat"] = [False, True, False, False]
    tr = simulate(b, s, CFG0)
    check("flat → đóng tại open bar kế (+0.5R)", abs(tr.r[0] - 0.5) < 1e-9 and tr.result[0] == "FLAT")

    # max_bars: market khớp bar 1, giữ đủ 2 bar (1, 2) → đóng tại open bar 3 = 100.5 → +0.5R
    b = _bars([[100, 100, 100, 100], [100, 100.5, 99.5, 100.2], [100.2, 100.6, 99.8, 100.4],
               [100.5, 100.6, 100.4, 100.5], [100, 100, 100, 100]])
    tr = simulate(b, _sig(b, 0, sl=99.0, tp=float("nan"), max_bars=2), CFG0)
    check("max_bars=2: đóng tại open bar fill+2 (+0.5R, TIME)",
          len(tr) == 1 and tr.exit_time[0] == b.index[3] and abs(tr.r[0] - 0.5) < 1e-9 and tr.result[0] == "TIME")
    # Lệnh chờ: đếm từ bar KHỚP (bar 2), không phải từ bar đặt lệnh → đóng tại open bar 4
    b = _bars([[100, 100, 100, 100], [100, 100.5, 99.8, 100.2], [100.2, 101.5, 100.1, 101.2],
               [101.2, 101.6, 100.9, 101.4], [101.5, 101.6, 101.4, 101.5], [101, 101, 101, 101]])
    tr = simulate(b, _sig(b, 0, entry_type="stop", entry_price=101.0, sl=100.0, tp=float("nan"),
                          expiry=5, max_bars=2), CFG0)
    check("max_bars đếm từ bar khớp lệnh chờ (fill bar 2 → đóng open bar 4)",
          len(tr) == 1 and tr.exit_time[0] == b.index[4] and abs(tr.r[0] - 0.5) < 1e-9 and tr.result[0] == "TIME")
    # SL trong thời hạn thắng time stop
    b = _bars([[100, 100, 100, 100], [100, 100.5, 99.5, 100.2], [100.2, 100.3, 98.5, 99],
               [99, 99, 99, 99]])
    tr = simulate(b, _sig(b, 0, sl=99.0, tp=float("nan"), max_bars=2), CFG0)
    check("SL trước hạn max_bars → SL, không TIME", len(tr) == 1 and tr.result[0] == "SL/BE")
    # max_bars 0 / NaN = tắt → giống hệt không có cột
    b = _bars([[100, 100.5, 99.5, 100]] * 8)
    base = simulate(b, _sig(b, 0, sl=99.0, tp=float("nan")), CFG0)
    off0 = simulate(b, _sig(b, 0, sl=99.0, tp=float("nan"), max_bars=0), CFG0)
    offn = simulate(b, _sig(b, 0, sl=99.0, tp=float("nan"), max_bars=float("nan")), CFG0)
    check("max_bars 0/NaN = tắt (giống không có cột)", base.equals(off0) and base.equals(offn)
          and base.result[0] == "EOD")

    # OCO: buy stop 101 / sell stop 99 (SL mỗi chân = giá chân kia, TP ±2)
    oco = dict(entry_type="stop", entry_price=101.0, sl=99.0, tp=103.0, expiry=10,
               oco_price=99.0, oco_sl=101.0, oco_tp=97.0)

    def _oco_sig(b):
        s = _sig(b, 0, **oco)
        s.loc[s.index[1:], ["oco_price", "oco_sl", "oco_tp"]] = np.nan
        return s

    b = _bars([[100, 100, 100, 100], [100, 101.5, 100.2, 101.2], [101.2, 103.2, 101, 103], [103, 103, 103, 103]])
    tr = simulate(b, _oco_sig(b), CFG0)
    check("OCO phá lên → mua 101, TP +1R", len(tr) == 1 and tr.direction[0] == "buy" and abs(tr.r[0] - 1) < 1e-9)
    b = _bars([[100, 100, 100, 100], [100, 100.4, 98.8, 98.9], [98.9, 99.0, 96.8, 97], [97, 97, 97, 97]])
    tr = simulate(b, _oco_sig(b), CFG0)
    check("OCO phá xuống → bán 99, TP +1R", len(tr) == 1 and tr.direction[0] == "sell" and abs(tr.r[0] - 1) < 1e-9)
    b = _bars([[100, 100, 100, 100], [100, 101.5, 98.5, 100], [100, 100, 100, 100], [100, 100, 100, 100]])
    tr = simulate(b, _oco_sig(b), CFG0)
    check("OCO chạm 2 chân cùng bar → 1 lệnh, -1R", len(tr) == 1 and abs(tr.r[0] + 1) < 1e-9)
    b = _bars([[100, 100, 100, 100], [100, 101.5, 100.2, 101.2], [101.2, 101.3, 98.5, 98.6],
               [98.6, 98.7, 96.5, 97], [97, 97, 97, 97]])
    tr = simulate(b, _oco_sig(b), CFG0)
    check("OCO: chân mua khớp → chân bán bị hủy (SL -1R, không lệnh bán sau đó)",
          len(tr) == 1 and tr.direction[0] == "buy" and abs(tr.r[0] + 1) < 1e-9)

    # Chi phí trong R: spread 0.28 với SL cách 1.0 → TP +2R thành 2 - 0.28 = 1.72R
    cfg = replace(CFG0, spread_points=0.28)
    b = _bars([[100, 100, 100, 100], [100, 100.5, 99.5, 100], [100, 102.5, 99.8, 102], [102, 102, 102, 102]])
    tr = simulate(b, _sig(b, 0, sl=99.0, tp=102.0), cfg)
    check("spread trừ đúng theo R", abs(tr.r[0] - 1.72) < 1e-9)

    # Stress ×1.5: swap dương giữ nguyên, âm nhân 1.5
    st = stressed(replace(CFG0, spread_points=0.2, swap_long_per_lot=-10.0, swap_short_per_lot=3.0))
    check("stress: chi phí ×1.5, swap dương giữ nguyên",
          abs(st.spread_points - 0.3) < 1e-12 and st.swap_long_per_lot == -15.0 and st.swap_short_per_lot == 3.0)


class _MA(Candidate):
    def signals(self, bars, n=5, peek=False):
        ma = bars["close"].rolling(n).mean()
        if peek:   # cố tình nhìn tương lai
            ma = ma.shift(-1)
        s = empty_signals(bars.index)
        up = (bars["close"] > ma) & (bars["close"].shift() <= ma.shift())
        s.loc[up, "signal"] = 1
        s.loc[up, "sl"] = bars["low"][up] - 1
        return s


def test_swap_nights():
    print("swap (số đêm rollover):")
    from risk.risk import _nights
    ts = pd.Timestamp   # 2020-01-06 = Thứ Hai
    check("trong ngày → 0 đêm", _nights(ts("2020-01-06 08:00"), ts("2020-01-06 21:00")) == 0)
    check("T2 → T3 = 1 đêm", _nights(ts("2020-01-06 12:00"), ts("2020-01-07 12:00")) == 1)
    check("T3 → T4 = 1 đêm (triple KHÔNG phải đêm vào thứ Tư)",
          _nights(ts("2020-01-07 12:00"), ts("2020-01-08 12:00")) == 1)
    check("T4 → T5 = 3 đêm (triple: rollover kết thúc thứ Tư, MT5 swap_rollover3days)",
          _nights(ts("2020-01-08 12:00"), ts("2020-01-09 12:00")) == 3)
    check("T6 → T2 = 1 đêm (không rollover T7/CN — cuối tuần đã trả ở thứ Tư)",
          _nights(ts("2020-01-10 12:00"), ts("2020-01-13 12:00")) == 1)
    check("T2 → T2 tuần sau = 7 đêm", _nights(ts("2020-01-06 12:00"), ts("2020-01-13 12:00")) == 7)
    check("mở CN tối → T2 = 0 đêm", _nights(ts("2020-01-12 22:00"), ts("2020-01-13 12:00")) == 0)


def test_causal_and_stats():
    print("causal & thống kê:")
    rng = np.random.default_rng(1)
    c = 100 + rng.standard_normal(2000).cumsum()
    b = pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c},
                     index=pd.date_range("2020-01-01", periods=2000, freq="1h"))
    cand = _MA("ma", "H1", {"n": [5]})
    assert_causal(cand, b, {"n": 5})
    check("Candidate nhân quả qua kiểm tra", True)
    try:
        assert_causal(cand, b, {"n": 5, "peek": True})
        caught = False
    except AssertionError:
        caught = True
    check("Candidate nhìn tương lai bị bắt", caught)

    try:
        Candidate("big", "H1", {k: [1] for k in "abcde"})
        budget = False
    except ValueError:
        budget = True
    check("> 4 tham số bị Complexity Budget chặn", budget)

    r = pd.Series([2.0, -1.0, -1.0, 3.0])
    check("PF = 5/2", abs(profit_factor(r) - 2.5) < 1e-12)
    tr = pd.DataFrame({"r": [-10.0, 5.0], "exit_time": pd.date_range("2020", periods=2)})
    check("DD 10% sau -10R ở 1%", abs(max_drawdown_pct(equity_curve(tr, 1.0)) - 10.0) < 1e-9)


def _walk(n=3000, seed=2, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 100 + (rng.standard_normal(n) + drift).cumsum()
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3,
                         "close": c}, index=pd.date_range("2020-01-01", periods=n, freq="4h"))


def test_c1():
    from strategy.candidates.c1_donchian import ATR_PERIOD, CANDIDATE as C1
    from strategy.indicators import atr
    print("C1 Donchian:")
    b = _walk()
    p = {"n": 20, "stop_atr": 3.0, "trail_atr": 4.0}
    s = C1.signals(b, **p)
    a = atr(b, ATR_PERIOD)
    up = b["high"].rolling(20).max().shift(1)
    dn = b["low"].rolling(20).min().shift(1)
    longs, shorts = s.index[s.signal == 1], s.index[s.signal == -1]
    check("có cả tín hiệu mua lẫn bán", len(longs) > 5 and len(shorts) > 5)
    check("mua = close vượt kênh N bar TRƯỚC, lần đầu (không lặp mỗi bar)",
          (b.close[longs] > up[longs]).all()
          and not (b.close.shift()[longs] > up.shift()[longs]).any())
    check("bán = close thủng kênh N bar trước", (b.close[shorts] < dn[shorts]).all())
    check("SL = close ∓ stop_atr·ATR, market, không TP",
          np.allclose(s.sl[longs], b.close[longs] - 3 * a[longs])
          and np.allclose(s.sl[shorts], b.close[shorts] + 3 * a[shorts])
          and (s.entry_type[longs] == "market").all() and s.tp.isna().all())
    check("trail = close ∓ trail_atr·ATR",
          np.allclose(s.trail_long, b.close - 4 * a, equal_nan=True)
          and np.allclose(s.trail_short, b.close + 4 * a, equal_nan=True))
    check("không tín hiệu khi ATR chưa ấm", (s.signal[a.isna()] == 0).all())
    assert_causal(C1, b, p)
    check("C1 nhân quả", True)
    check("≤ 4 tham số, không BE/partial", len(C1.param_grid) <= 4 and not C1.uses_be_partial)

    # Xu hướng tăng đều → thắng nhờ trail (lệnh mua chiếm ưu thế, R dương)
    tr = simulate(_walk(drift=0.3), C1.signals(_walk(drift=0.3), **p), CFG0)
    check("xu hướng tăng → tổng R dương", len(tr) > 0 and tr.r.sum() > 0)


def test_c2():
    from strategy.candidates.c2_orb import CANDIDATE as C2
    print("C2 opening-range breakout:")
    rng = np.random.default_rng(3)
    idx = pd.date_range("2020-01-06", periods=96, freq="15min").append(
        pd.date_range("2020-07-06", periods=96, freq="15min"))     # 1 ngày giờ mùa đông + 1 ngày mùa hè
    c = 100 + rng.standard_normal(len(idx)).cumsum() * 0.2
    o = np.concatenate([[c[0]], c[:-1]])
    b = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.1, "low": np.minimum(o, c) - 0.1, "close": c},
                     index=idx)
    ts = pd.Timestamp

    s = C2.signals(b, x=30, k=2.0, session="london")
    sig_at = list(s.index[s.signal != 0])
    # London 08:00 = 08:00 UTC mùa đông, 07:00 UTC mùa hè; range 30' → tín hiệu ở bar thứ 2
    check("London: tín hiệu tại bar cuối range, theo DST", sig_at == [ts("2020-01-06 08:15"), ts("2020-07-06 07:15")])
    hi, lo = b.high["2020-01-06 08:00":"2020-01-06 08:15"].max(), b.low["2020-01-06 08:00":"2020-01-06 08:15"].min()
    r = s.loc[ts("2020-01-06 08:15")]
    check("bracket: buy stop hi / sell stop lo, SL = cạnh kia, TP = k·range",
          r.entry_type == "stop" and r.entry_price == hi and r.oco_price == lo
          and r.sl == lo and r.oco_sl == hi
          and abs(r.tp - (hi + 2 * (hi - lo))) < 1e-12 and abs(r.oco_tp - (lo - 2 * (hi - lo))) < 1e-12)
    # range kết thúc 08:30 UTC → 16:00 NY = 21:00 UTC (mùa đông) → 12,5h = 50 bar
    check("lệnh chờ hết hạn đúng 16:00 NY", r.expiry == 50 and s.loc[ts("2020-07-06 07:15")].expiry == 50)
    # flat từ bar 15:45 NY trở đi (thiếu đúng bar 15:45 vẫn không ôm lệnh qua đêm)
    # (00:00 NY = 05:00 UTC đông / 04:00 UTC hè)
    check("flat từ 15:45 NY (20:45 UTC đông / 19:45 UTC hè), trước đó trong ngày NY thì không",
          s.flat[ts("2020-01-06 20:45")] and not s.flat["2020-01-06 05:00":"2020-01-06 20:30"].any()
          and s.flat[ts("2020-07-06 19:45")] and not s.flat["2020-07-06 04:00":"2020-07-06 19:30"].any())

    s = C2.signals(b, x=60, k=1.0, session="ny")
    check("NY 08:00, range 60' → tín hiệu 13:45 UTC đông / 12:45 UTC hè",
          list(s.index[s.signal != 0]) == [ts("2020-01-06 13:45"), ts("2020-07-06 12:45")])

    s = C2.signals(b.drop(ts("2020-01-06 08:00")), x=30, k=2.0, session="london")
    check("range thiếu bar → bỏ ngày đó", list(s.index[s.signal != 0]) == [ts("2020-07-06 07:15")])

    n = 96 * 30
    c = 100 + rng.standard_normal(n).cumsum() * 0.2
    o = np.concatenate([[c[0]], c[:-1]])
    b = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.1, "low": np.minimum(o, c) - 0.1, "close": c},
                     index=pd.date_range("2020-03-01", periods=n, freq="15min"))   # qua đổi giờ Mỹ & Anh
    for sess in ("london", "ny"):
        assert_causal(C2, b, {"x": 30, "k": 1.5, "session": sess})
    check("C2 nhân quả (cả 2 phiên, qua mùa đổi giờ)", True)
    tr = simulate(b, C2.signals(b, x=30, k=1.5, session="london"), CFG0)
    check("mô phỏng chạy, mọi lệnh đóng trong ngày",
          len(tr) > 0 and ((tr.exit_time - tr.entry_time) < pd.Timedelta(hours=16)).all())


def test_c4():
    from strategy.candidates.c4_smc import ATR_PERIOD, CANDIDATE as C4
    from strategy.indicators import atr
    print("C4 SMC rút gọn:")
    # 20 bar phẳng (không có pivot strict) rồi: swing high 12 @2, swing low 8 @4,
    # bar 7 quét đáy 8 (low 7.5, close 8.5 > 8), bar 9 close 12.3 > đỉnh 12 → CHoCH tăng.
    pre = [[10, 10.2, 9.8, 10]] * 20
    seq = [[10, 10, 9, 9.5], [9.5, 10.5, 9.5, 10], [10, 12, 11, 11.5], [11.5, 11.5, 10, 10.5],
           [10.5, 11, 8, 9], [9, 10, 9, 9.5], [9.5, 10.5, 9.2, 10], [10, 9.5, 7.5, 8.5],
           [8.5, 10, 8.6, 9.8], [9.8, 12.5, 9.8, 12.3], [12.3, 12.6, 12.1, 12.4]]
    # cột: open, high, low, close
    rows = [[r[0], r[1], r[2], r[3]] for r in pre + seq]
    idx = pd.date_range("2020-01-06", periods=len(rows), freq="15min")

    def frame(rs):
        d = pd.DataFrame(rs, columns=["open", "high", "low", "close"], index=idx[:len(rs)])
        d["high"] = d[["open", "high", "close"]].max(axis=1)
        d["low"] = d[["open", "low", "close"]].min(axis=1)
        return d

    b = frame(rows)
    p = {"swing_lookback": 2, "sl_buffer_atr": 0.5, "setup_age": 4, "target_r": 2.0}
    s = C4.signals(b, **p)
    a = atr(b, ATR_PERIOD)
    at = idx[29]
    check("sweep → CHoCH tăng → đúng 1 tín hiệu mua ở bar CHoCH", list(s.index[s.signal != 0]) == [at]
          and s.signal[at] == 1)
    sl = 7.5 - 0.5 * a[at]
    check("limit tại đỉnh bị phá 12, SL dưới râu quét − buffer·ATR, TP = 2R, hết hạn = setup_age",
          s.entry_type[at] == "limit" and s.entry_price[at] == 12 and abs(s.sl[at] - sl) < 1e-9
          and abs(s.tp[at] - (12 + 2 * (12 - sl))) < 1e-9 and s.expiry[at] == 4)

    mirror = frame([[30 - o, 30 - lo, 30 - hi, 30 - c] for o, hi, lo, c in rows])
    sm = C4.signals(mirror, **p)
    check("đối xứng: quét đỉnh → CHoCH giảm → bán limit 18",
          list(sm.index[sm.signal != 0]) == [at] and sm.signal[at] == -1 and sm.entry_price[at] == 18
          and abs(sm.sl[at] - (22.5 + 0.5 * a[at])) < 1e-9)

    check("CHoCH muộn hơn setup_age → không tín hiệu",
          (C4.signals(b, **{**p, "setup_age": 1}).signal == 0).all())
    broken = [r[:] for r in rows]
    broken[27] = [10, 9.5, 7.5, 7.8]        # đóng DƯỚI đáy 8 → phá đáy, không phải quét
    check("đóng dưới đáy = phá, không phải sweep → không tín hiệu",
          (C4.signals(frame(broken), **p).signal == 0).all())

    # Râu ngẫu nhiên (_walk có râu cố định → đỉnh 2 bar liền nhau bằng nhau → không có pivot strict)
    rng = np.random.default_rng(5)
    c = 100 + rng.standard_normal(4000).cumsum()
    o = np.concatenate([[c[0]], c[:-1]])
    rb = pd.DataFrame({"open": o, "high": np.maximum(o, c) + rng.exponential(0.4, 4000),
                       "low": np.minimum(o, c) - rng.exponential(0.4, 4000), "close": c},
                      index=pd.date_range("2020-01-01", periods=4000, freq="15min"))
    for pp in (p, {"swing_lookback": 6, "sl_buffer_atr": 0.1, "setup_age": 24, "target_r": 3.0}):
        assert_causal(C4, rb, pp)
    s = C4.signals(rb, **p)
    check("C4 nhân quả; ngẫu nhiên vẫn sinh tín hiệu 2 chiều",
          (s.signal == 1).sum() > 5 and (s.signal == -1).sum() > 5)
    check("đúng 4 tham số", len(C4.param_grid) == 4)


def _ou(n=3000, seed=4, theta=0.15, freq="4h"):
    """Chuỗi hồi quy về trung bình (Ornstein-Uhlenbeck quanh 100)."""
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = x[i - 1] * (1 - theta) + rng.standard_normal()
    c = 100 + x
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3,
                         "close": c}, index=pd.date_range("2020-01-01", periods=n, freq=freq))


def test_a_zfade():
    from strategy.candidates.a_zfade import ATR_PERIOD, CANDIDATE as A
    from strategy.indicators import atr
    print("A z-fade:")
    b = _walk()
    p = {"n": 20, "k": 2.0, "stop_atr": 2.0, "max_bars": 6}
    s = A.signals(b, **p)
    a = atr(b, ATR_PERIOD)
    sma = b.close.rolling(20).mean()
    z = (b.close - sma) / a
    longs, shorts = s.index[s.signal == 1], s.index[s.signal == -1]
    check("có cả tín hiệu mua lẫn bán", len(longs) > 5 and len(shorts) > 5)
    check("mua = bar ĐẦU TIÊN z ≤ −k (sự kiện), bán = bar đầu tiên z ≥ +k",
          (z[longs] <= -2).all() and (z.shift()[longs] > -2).all()
          and (z[shorts] >= 2).all() and (z.shift()[shorts] < 2).all())
    check("không lặp tín hiệu khi z còn ở ngoài ngưỡng",
          ((z <= -2) & (z.shift() <= -2) & (s.signal != 0)).sum() == 0)
    check("SL = close ∓ stop_atr·ATR, market, không TP",
          np.allclose(s.sl[longs], b.close[longs] - 2 * a[longs])
          and np.allclose(s.sl[shorts], b.close[shorts] + 2 * a[shorts])
          and (s.entry_type[longs] == "market").all() and s.tp.isna().all())
    side = np.sign(b.close - sma)
    check("flat đúng ở bar close cắt qua SMA", (s.flat == ((side != side.shift()) & sma.notna()
                                                          & sma.shift().notna())).all() and s.flat.sum() > 10)
    check("max_bars có trên mọi hàng", (s.max_bars == 6).all())
    check("không tín hiệu khi ATR/SMA chưa ấm", (s.signal[a.isna() | sma.isna()] == 0).all())
    assert_causal(A, b, p)
    check("A nhân quả", True)
    check("đúng 4 tham số, không BE/partial", len(A.param_grid) == 4 and not A.uses_be_partial)
    ou = _ou()
    tr = simulate(ou, A.signals(ou, **p), CFG0)
    check("chuỗi hồi quy về trung bình → tổng R dương; thoát bằng FLAT và TIME",
          len(tr) > 20 and tr.r.sum() > 0 and {"FLAT", "TIME"} <= set(tr.result))


def test_b_tsmom():
    from strategy.candidates.b_tsmom import ATR_PERIOD, CANDIDATE as B
    from strategy.indicators import atr
    print("B time-series momentum:")
    rng = np.random.default_rng(5)
    idx = pd.date_range("2016-01-01", "2020-12-31", freq="D")
    idx = idx[idx.dayofweek != 5]                          # không có Thứ Bảy, CÓ mẩu Chủ Nhật
    c = 100 + (rng.standard_normal(len(idx)) * 1.0).cumsum()
    o = np.concatenate([[c[0]], c[:-1]])
    b = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3,
                      "close": c}, index=idx)
    clean = b.copy()
    sun = b.index.dayofweek == 6
    b.loc[sun, ["high", "low", "close"]] = b.loc[sun, ["high", "low", "close"]] + [50, -50, 30]  # Chủ Nhật "điên"
    p = {"lookback": 60, "stop_atr": 4.0}
    s = B.signals(b, **p)
    wk = b[~sun]
    sw = B.signals(wk, **p)
    check("bỏ mẩu Chủ Nhật: tín hiệu/SL/flat ngày thường y hệt khi xóa hẳn Chủ Nhật",
          s.loc[wk.index, "signal"].equals(sw.signal) and s.loc[wk.index, "flat"].equals(sw.flat)
          and np.allclose(s.loc[wk.index, "sl"], sw.sl, equal_nan=True))
    check("Chủ Nhật: không tín hiệu, không flat", (s.signal[sun] == 0).all() and not s.flat[sun].any())
    ret = wk.close / wk.close.shift(60) - 1
    a = atr(wk, ATR_PERIOD)
    ok = ret.notna() & a.notna()
    check("tín hiệu = dấu lợi nhuận 60 ngày GIAO DỊCH (trạng thái, mọi bar)",
          (sw.signal[ok] == np.sign(ret[ok])).all() and (sw.signal[~ok] == 0).all()
          and (sw.signal != 0).sum() > 1000)
    lg = ok & (sw.signal > 0)
    check("SL = close ∓ stop_atr·ATR(20), market, không TP",
          np.allclose(sw.sl[lg], wk.close[lg] - 4 * a[lg]) and sw.tp.isna().all()
          and (sw.entry_type == "market").all())
    sg = sw.signal
    check("flat đúng ở bar dấu đổi", (sw.flat == (ok & ok.shift(1, fill_value=False) & (sg != sg.shift()))).all()
          and sw.flat.sum() > 5)
    assert_causal(B, b, p)
    check("B nhân quả (cả khi có Chủ Nhật)", True)
    check("2 tham số", len(B.param_grid) == 2 and not B.uses_be_partial)
    up = clean.copy()
    drift = np.arange(len(up)) * 0.3
    up[["open", "high", "low", "close"]] = up[["open", "high", "low", "close"]].add(drift, axis=0)
    tr = simulate(up, B.signals(up, **p), CFG0)
    check("xu hướng tăng → tổng R dương", len(tr) >= 1 and tr.r.sum() > 0)
    tr = simulate(clean, B.signals(clean, **p), CFG0)
    again = (tr.result.shift() == "SL/BE") & (tr.direction == tr.direction.shift())
    check("bị SL mà dấu còn giữ → vào lại cùng hướng; đổi dấu → FLAT",
          again.any() and (tr.result == "FLAT").any())


def test_c_intramom():
    from strategy.candidates.c_intramom import ATR_PERIOD, CANDIDATE as C, daily_atr_prior
    from strategy.indicators import atr
    print("C intraday momentum:")
    rng = np.random.default_rng(6)
    idx = pd.date_range("2020-01-01", "2020-04-30 23:00", freq="h")
    idx = idx[idx.dayofweek != 5]
    c = 100 + (rng.standard_normal(len(idx)) * 0.3).cumsum()
    o = np.concatenate([[c[0]], c[:-1]])
    b = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.1, "low": np.minimum(o, c) - 0.1,
                      "close": c}, index=idx)
    ts = pd.Timestamp
    s = C.signals(b, k=0.0, stop_atr=2.0)
    a = atr(b, ATR_PERIOD)

    def mv(start, end):
        return np.sign(b.close[ts(end)] - b.open[ts(start)])
    # Mùa đông: London 08:00 = 08:00 UTC, NY 07:00 = 12:00 UTC. Hè: 07:00 / 11:00 UTC.
    # Tuần lệch DST (Mỹ đã đổi 08/03, Anh chưa tới 29/03): 08:00 / 11:00 UTC → cửa sổ 4h.
    check("ATR D1 chưa ấm (tuần đầu) → không lệnh dù k = 0", (s.signal[:"2020-01-15"] == 0).all())
    check("mùa đông: tín hiệu ở bar 12:00 UTC, hướng = close(12:00) − open(08:00)",
          s.signal[ts("2020-02-12 12:00")] == mv("2020-02-12 08:00", "2020-02-12 12:00")
          and (s.signal["2020-02-12"] != 0).sum() == 1)
    check("tuần lệch DST: cửa sổ 08:00 → 11:00 UTC (4h)",
          s.signal[ts("2020-03-16 11:00")] == mv("2020-03-16 08:00", "2020-03-16 11:00")
          and (s.signal["2020-03-16"] != 0).sum() == 1)
    check("mùa hè: cửa sổ 07:00 → 11:00 UTC",
          s.signal[ts("2020-04-15 11:00")] == mv("2020-04-15 07:00", "2020-04-15 11:00")
          and (s.signal["2020-04-15"] != 0).sum() == 1)
    at = s.index[s.signal != 0]
    check("SL = close ∓ stop_atr·ATR H1, market, không TP",
          np.allclose(s.sl[at], b.close[at] - s.signal[at] * 2 * a[at]) and s.tp.isna().all()
          and (s.entry_type[at] == "market").all())
    check("flat từ bar 15:00 NY (20:00 UTC đông / 19:00 UTC hè), không trước đó trong phiên",
          s.flat[ts("2020-02-12 20:00")] and not s.flat["2020-02-12 05:00":"2020-02-12 19:00"].any()
          and s.flat[ts("2020-04-15 19:00")] and not s.flat["2020-04-15 04:00":"2020-04-15 18:00"].any())
    s2 = C.signals(b.drop(ts("2020-02-12 08:00")), k=0.0, stop_atr=2.0)
    check("thiếu bar neo → bỏ ngày đó", (s2.signal["2020-02-12"] == 0).all()
          and s2.signal[ts("2020-02-13 12:00")] != 0)
    d = b[b.index.dayofweek != 6].resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    datr = daily_atr_prior(b)
    check("ATR D1 ngày d chỉ dùng các ngày < d",
          abs(datr[ts("2020-02-20")] - atr(d[:"2020-02-19"], ATR_PERIOD).iloc[-1]) < 1e-12)
    s5 = C.signals(b, k=0.5, stop_atr=2.0)
    ends = s.index[s.signal != 0]
    thr = 0.5 * datr.reindex(ends.normalize()).to_numpy()
    def london8(t):   # 08:00 London của cùng ngày → UTC
        return (t.normalize() + pd.Timedelta(hours=8)).tz_localize("Europe/London").tz_convert("UTC").tz_localize(None)
    move = np.array([b.close[t] - b.open[london8(t)] for t in ends])
    check("k = 0.5: có lệnh ⇔ |biến động| ≥ 0.5·ATR D1 ngày trước",
          ((s5.signal[ends] != 0).to_numpy() == (np.abs(move) >= thr)).all()
          and 0 < (s5.signal != 0).sum() < (s.signal != 0).sum())
    for k in (0.0, 0.5):
        assert_causal(C, b, {"k": k, "stop_atr": 2.0})
    check("C nhân quả (qua đổi giờ Mỹ & Anh)", True)
    check("2 tham số", len(C.param_grid) == 2 and not C.uses_be_partial)
    tr = simulate(b, s, CFG0)
    check("mọi lệnh đóng trong ngày (FLAT/SL), không qua đêm",
          len(tr) > 50 and set(tr.result) <= {"FLAT", "SL/BE", "SL-GAP"}
          and (tr.exit_time.dt.normalize() == tr.entry_time.dt.normalize()).all())


def test_auxdata():
    print("dữ liệu phụ (ADR 0003):")
    from datafeed import auxdata as ax
    from datafeed.bars import HOLDOUT_START
    ts = pd.Timestamp
    # Giờ công bố H.15: ngày làm việc Mỹ kế tiếp, 16:15 ET → UTC theo DST.
    check("H.15 mùa đông: T2 06/01/2020 → T3 16:15 ET = 21:15 UTC",
          ax.h15_available_at(ts("2020-01-06")) == ts("2020-01-07 21:15"))
    check("H.15 mùa hè: → 20:15 UTC", ax.h15_available_at(ts("2020-07-07")) == ts("2020-07-08 20:15"))
    check("H.15 thứ Sáu → thứ Hai", ax.h15_available_at(ts("2020-01-10")) == ts("2020-01-13 21:15"))
    check("H.15 bỏ ngày lễ liên bang (03/07/2020 nghỉ bù) → 06/07",
          ax.h15_available_at(ts("2020-07-02")) == ts("2020-07-06 20:15"))
    check("H.15 qua đổi giờ Mỹ (T6 06/03/2020 → T2 09/03 đã là EDT) → 20:15 UTC",
          ax.h15_available_at(ts("2020-03-06")) == ts("2020-03-09 20:15"))

    # FRED/ALFRED lần công bố đầu: "." = không công bố; available_at = max(quy tắc H.15, ngày vintage 16:15 ET).
    payload = {"observations": [
        {"date": "2020-01-06", "value": "1.81", "realtime_start": "2020-01-07", "realtime_end": "2020-01-07"},
        {"date": "2020-01-07", "value": "1.83", "realtime_start": "2020-01-09", "realtime_end": "2020-01-09"},
        {"date": "2020-01-08", "value": ".", "realtime_start": "2020-01-09", "realtime_end": "2020-01-09"},
    ]}
    rows = ax.parse_fred(payload, "DGS10")
    check("FRED: '.' → NaN, giá trị số giữ nguyên",
          np.isnan(rows.value.iloc[2]) and rows.value.iloc[0] == 1.81 and rows.source.iloc[0] == "alfred:DGS10")
    check("FRED: vintage muộn hơn quy tắc → dùng vintage (07/01 công bố 09/01 16:15 ET)",
          rows.available_at.iloc[1] == ts("2020-01-09 21:15") and rows.available_at.iloc[0] == ts("2020-01-07 21:15"))

    # Căn theo giờ ĐÓNG bar: hàng i chỉ thấy giá trị có available_at ≤ close(i).
    idx = pd.date_range("2020-01-07 20:00", periods=4, freq="h")       # close 21:00, 22:00, 23:00, 00:00
    a = ax.align(rows, idx, "H1")
    check("1 giây trước available_at → chưa thấy; đúng giờ → thấy",
          np.isnan(a.iloc[0]) and a.iloc[1] == 1.81
          and np.isnan(ax.align(rows, pd.DatetimeIndex([ts("2020-01-07 20:14:59")]), "H1").iloc[0])
          and ax.align(rows, pd.DatetimeIndex([ts("2020-01-07 20:15")]), "H1").iloc[0] == 1.81)
    late = ax.align(rows, pd.DatetimeIndex([ts("2020-01-09 21:00"), ts("2020-01-10 12:00")]), "H1")
    check("công bố thiếu ('.') không tạo giá trị; giữ giá trị THẬT gần nhất",
          list(late) == [1.83, 1.83])
    bad = rows.copy()
    bad.loc[1, "available_at"] = ts("2020-01-01")
    check("available_at đi lùi theo observation → báo lỗi (store hỏng)", _refused_any(ax.align, bad, idx, "H1"))

    # Holdout: quan sát từ HOLDOUT_START bị ẩn trừ khi include_holdout.
    hr = pd.DataFrame({"observation": [HOLDOUT_START - pd.Timedelta(days=1), HOLDOUT_START],
                       "value": [1.0, 2.0], "source": "x",
                       "available_at": [HOLDOUT_START, HOLDOUT_START + pd.Timedelta(days=1)]})
    check("holdout: quan sát ≥ 2025-10-01 bị ẩn mặc định",
          list(ax.hide_holdout(hr, False).value) == [1.0] and len(ax.hide_holdout(hr, True)) == 2)

    # USD5: rổ hình học đều 5 đồng, TĂNG khi USD mạnh.
    one = pd.Series([1.0, 1.0])
    check("USD5: USDJPY ×32 (USD mạnh lên so với JPY) → USD5 ×2 (= 32^(1/5))",
          np.allclose(ax.usd5_index(one, one, pd.Series([1.0, 32.0]), one, one), [1.0, 2.0]))
    check("USD5: EURUSD giảm (USD mạnh) → tăng; USDCHF giảm (USD yếu) → giảm",
          ax.usd5_index(pd.Series([1.0, 0.5]), one, one, one, one).is_monotonic_increasing
          and ax.usd5_index(one, one, one, one, pd.Series([1.0, 0.5])).is_monotonic_decreasing)

    # Hợp đồng: Candidate khai báo aux nhận signals(bars, aux); nhìn trộm aux tương lai bị bắt.
    class _Aux(Candidate):
        def signals(self, bars, aux, peek=False):
            s = empty_signals(bars.index)
            x = aux["X"].shift(-1) if peek else aux["X"]
            go = (x > 0).to_numpy()
            s.loc[go, "signal"] = 1
            s["sl"] = bars["close"] - 1.0
            return s
    b = _walk(200)
    aux = pd.DataFrame({"X": np.sin(np.arange(200) / 5)}, index=b.index)
    cand = _Aux("aux_test", "H4", {"peek": [False]}, aux=["X"])
    check("Candidate có aux: compute() truyền aux; thiếu aux → lỗi",
          (cand.compute(b, {}, aux).signal > 0).sum() > 10 and _refused_any(cand.compute, b, {}))
    check("Candidate không aux: compute() gọi signals(bars) như cũ",
          _MA("ma", "H4", {"n": [5]}).compute(b, {"n": 5}).equals(_MA("ma", "H4", {"n": [5]}).signals(b, n=5)))
    assert_causal(cand, b, {"peek": False}, aux)
    caught = False
    try:
        assert_causal(cand, b, {"peek": True}, aux)
    except AssertionError:
        caught = True
    check("assert_causal cắt cả aux: nhìn trộm aux tương lai bị bắt", caught)


def test_sign_flips():
    from strategy.indicators import sign_flips
    print("sign_flips:")
    z = pd.Series([np.nan, 1.0, 2.0, 0.0, -1.0, 0.0, -2.0, 3.0])
    check("+ → 0 → − là 1 lần đổi; 0 và NaN không tạo lần đổi",
          list(sign_flips(z)) == [False, False, False, False, True, False, False, True])


def _usd_world(n=3000, seed=7, freq="4h", drift=0.0, resid_theta=None):
    """Symbol XXX/USD = 1/USD5 × phần riêng: USD5 đi bộ ngẫu nhiên (+drift), phần riêng là
    đi bộ ngẫu nhiên hoặc OU (resid_theta). Trả về (bars, aux)."""
    rng = np.random.default_rng(seed)
    lu = (rng.standard_normal(n) * 0.004 + drift).cumsum()
    if resid_theta is None:
        le = (rng.standard_normal(n) * 0.002).cumsum()
    else:
        le = np.zeros(n)
        for i in range(1, n):
            le[i] = le[i - 1] * (1 - resid_theta) + rng.standard_normal() * 0.004
    idx = pd.date_range("2020-01-01", periods=n, freq=freq)
    c = 100 * np.exp(-lu + le)
    o = np.concatenate([[c[0]], c[:-1]])
    b = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999,
                      "close": c}, index=idx)
    return b, pd.DataFrame({"USD5": 100 * np.exp(lu)}, index=idx)


def test_d_usdtrend():
    from strategy.candidates.d_usdtrend import ATR_PERIOD, CANDIDATE as D, usd_trend_z
    from strategy.indicators import atr
    print("D dollar trend:")
    b, aux = _usd_world()
    p = {"lookback": 30, "k": 0.5, "stop_atr": 3.0}
    s = D.compute(b, p, aux)
    z = usd_trend_z(aux.USD5, 30)
    a = atr(b, ATR_PERIOD)
    sells, buys = s.index[s.signal == -1], s.index[s.signal == 1]
    check("USD mạnh (z ≥ k) → BÁN, USD yếu (z ≤ −k) → MUA, cả 2 phía đều có",
          len(sells) > 50 and len(buys) > 50 and (z[sells] >= 0.5).all() and (z[buys] <= -0.5).all())
    check("|z| < k → không tín hiệu", (s.signal[z.abs() < 0.5] == 0).all())
    check("SL = close ∓ stop_atr·ATR(20), market, không TP",
          np.allclose(s.sl[buys], b.close[buys] - 3 * a[buys]) and np.allclose(s.sl[sells], b.close[sells] + 3 * a[sells])
          and s.tp.isna().all() and (s.entry_type == "market").all())
    check("z = log-return L bar / (σ₁·√L)",
          abs(z.iloc[-1] - np.log(aux.USD5.iloc[-1] / aux.USD5.iloc[-31])
              / (np.log(aux.USD5).diff().iloc[-500:].std() * np.sqrt(30))) < 1e-9)
    check("flat ở mỗi lần z đổi dấu (trễ pha: k không áp cho lối ra)", s.flat.sum() > 20)
    assert_causal(D, b, p, aux)
    check("D nhân quả (cả aux)", True)
    check("3 tham số, khai báo aux USD5", len(D.param_grid) == 3 and D.aux == ["USD5"])
    b2, aux2 = _usd_world(drift=0.002)                   # USD tăng đều → symbol giảm đều
    tr = simulate(b2, D.compute(b2, p, aux2), CFG0)
    check("USD tăng đều → bán chiếm ưu thế, tổng R dương",
          len(tr) > 0 and tr.r.sum() > 0 and (tr.direction == "sell").mean() > 0.5)


def test_e_usdresid():
    from strategy.candidates.e_usdresid import CANDIDATE as E, residual_z
    print("E residual fade:")
    b, aux = _usd_world(resid_theta=0.1)
    p = {"n": 6, "k": 2.0, "stop_atr": 2.0, "max_bars": 12}
    s = E.compute(b, p, aux)
    z = residual_z(b.close, aux.USD5, 6)
    r_sym, r_usd = np.log(b.close).diff(), np.log(aux.USD5).diff()
    beta = (r_sym.rolling(500).cov(r_usd) / r_usd.rolling(500).var()).shift(1)
    check("β ước lượng ≈ −1 (symbol = 1/USD5 × phần riêng)", abs(beta.iloc[-1] + 1) < 0.15)
    longs, shorts = s.index[s.signal == 1], s.index[s.signal == -1]
    check("mua = bar ĐẦU TIÊN z ≤ −k, bán = bar đầu tiên z ≥ +k (sự kiện)",
          len(longs) > 5 and len(shorts) > 5 and (z[longs] <= -2).all() and (z.shift()[longs] > -2).all()
          and (z[shorts] >= 2).all() and (z.shift()[shorts] < 2).all())
    check("flat khi z cắt 0; max_bars trên mọi hàng", s.flat.sum() > 50 and (s.max_bars == 12).all())
    own = (np.log(b.close / 100) + np.log(aux.USD5 / 100)).diff().rolling(6).sum()   # phần riêng thật
    usd = np.log(aux.USD5).diff().rolling(6).sum()
    ok = z.notna()
    check("z bám phần RIÊNG của symbol (corr > 0.9), bỏ qua phần USD (|corr| < 0.2)",
          z[ok].corr(own[ok]) > 0.9 and abs(z[ok].corr(usd[ok])) < 0.2)
    assert_causal(E, b, p, aux)
    check("E nhân quả (cả aux, β cuộn)", True)
    check("4 tham số, khai báo aux USD5", len(E.param_grid) == 4 and E.aux == ["USD5"])
    tr = simulate(b, E.compute(b, {**p, "k": 1.5}, aux), CFG0)
    check("phần dư hồi quy về 0 (OU) → tổng R dương; thoát chủ yếu bằng FLAT",
          len(tr) > 20 and tr.r.sum() > 0 and {"FLAT"} <= set(tr.result))


def test_f_realyield():
    from strategy.candidates.f_realyield import CANDIDATE as F
    print("F real-yield direction:")
    rng = np.random.default_rng(8)
    idx = pd.date_range("2016-01-01", "2020-12-31", freq="D")
    idx = idx[idx.dayofweek != 5]                          # có mẩu Chủ Nhật
    y = pd.Series((rng.standard_normal(len(idx)) * 0.04).cumsum(), index=idx)
    c = 100 + (rng.standard_normal(len(idx))).cumsum()
    o = np.concatenate([[c[0]], c[:-1]])
    b = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3, "close": c},
                     index=idx)
    aux = pd.DataFrame({"DFII10": y})
    sun = b.index.dayofweek == 6
    aux_mad = aux.copy()
    aux_mad.loc[sun, "DFII10"] = 99.0                      # giá trị Chủ Nhật "điên" không được ảnh hưởng
    p = {"lookback": 20, "k": 0.5, "stop_atr": 3.0}
    s = F.compute(b, p, aux_mad)
    sw = F.compute(b[~sun], p, aux[~sun])
    check("bỏ Chủ Nhật: ngày thường y hệt khi xóa hẳn Chủ Nhật; CN không tín hiệu/flat",
          s.loc[~sun, "signal"].equals(sw.signal) and s.loc[~sun, "flat"].equals(sw.flat)
          and (s.signal[sun] == 0).all() and not s.flat[sun].any())
    yw = aux.DFII10[~sun]
    d = yw - yw.shift(20)
    z = d / d.rolling(250).std().shift(1)
    check("lợi suất thực tăng (z ≥ k) → BÁN, giảm → MUA",
          (sw.signal[z >= 0.5] == -1).all() and (sw.signal[z <= -0.5] == 1).all()
          and (sw.signal[z.abs() < 0.5] == 0).all() and (sw.signal != 0).sum() > 200)
    assert_causal(F, b, p, aux)
    check("F nhân quả (cả aux, qua Chủ Nhật)", True)
    check("3 tham số, khai báo aux DFII10", len(F.param_grid) == 3 and F.aux == ["DFII10"])
    trend = pd.Series(np.arange(len(idx)) * 0.01, index=idx)          # lợi suất thực tăng đều
    down = b.copy()
    down[["open", "high", "low", "close"]] = down[["open", "high", "low", "close"]].sub(
        np.arange(len(idx)) * 0.3, axis=0) + 500
    tr = simulate(down, F.compute(down, p, pd.DataFrame({"DFII10": trend + y * 0.1})), CFG0)
    check("lợi suất thực tăng + giá giảm → bán, tổng R dương", len(tr) > 0 and tr.r.sum() > 0
          and (tr.direction == "sell").mean() > 0.5)


def _refused_any(fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
    except (Exception, SystemExit):
        return True
    return False


def _refused(fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
    except SystemExit:
        return True
    return False


def test_holdout():
    import json
    import tempfile
    from pathlib import Path
    from backtest import holdout as ho
    from datafeed.bars import HOLDOUT_START
    print("Final Holdout (1 lần):")
    tr = lambda rs: pd.DataFrame({"r": rs, "symbol": "X",
                                  "exit_time": pd.date_range("2025-10-02", periods=len(rs), freq="D")})
    check("PF = 1.0, DD nhỏ → qua (ngưỡng PF ≥ 1.0)", ho.holdout_verdict(tr([2.0, -1.0, -1.0]))["passed"])
    check("PF < 1 → trượt", not ho.holdout_verdict(tr([1.0, -1.0, -1.0]))["passed"])
    check("DD > 15% → trượt dù PF cao", not ho.holdout_verdict(tr([-17.0, 40.0]))["passed"])
    check("0 lệnh → trượt", not ho.holdout_verdict(tr([]))["passed"])

    cand = _MA("ma_test", "H1", {"n": [5, 10]})
    real_run, real_sim = ho.wf.run, ho.simulate
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        p = ho.claim("x", {"a": 1}, d)
        check("claim tạo marker", json.loads(p.read_text(encoding="utf-8"))["a"] == 1)
        check("claim lần 2 bị từ chối, marker cũ giữ nguyên",
              _refused(ho.claim, "x", {"a": 2}, d) and json.loads(p.read_text(encoding="utf-8"))["a"] == 1)

        def boom(*a, **k):
            raise AssertionError("không được chạy walk-forward khi holdout đã dùng")
        ho.wf.run = boom
        ho.claim("ma_test", {}, d)
        check("marker đã có → từ chối TRƯỚC khi chạy walk-forward",
              _refused(ho.preflight, cand, ["X"], marker_dir=d, require_clean=False))
        (d / "ma_test.json").unlink()

        ho.wf.run = lambda *a, **k: {"passed": False}
        check("chưa qua walk-forward → từ chối",
              _refused(ho.preflight, cand, ["X"], marker_dir=d, require_clean=False))

        ho.wf.run = boom
        closed = _MA("ma_closed", "H1", {"n": [5]}, retired="Bake-off #1")
        check("Candidate đã đóng → từ chối TRƯỚC khi chạy walk-forward",
              _refused(ho.preflight, closed, ["X"], marker_dir=d, require_clean=False))

        # Đường chạy đủ trên dữ liệu tổng hợp 2022-09 → 2026-09 (H1).
        rng = np.random.default_rng(7)
        idx = pd.date_range("2022-09-01", "2026-09-30 23:00", freq="1h")
        c = 100 + rng.standard_normal(len(idx)).cumsum() * 0.1
        bars = pd.DataFrame({"open": c, "high": c + 0.2, "low": c - 0.2, "close": c}, index=idx)
        is_trades = pd.DataFrame({"entry_time": pd.date_range("2023-01-01", periods=40, freq="7D"),
                                  "r": np.r_[[1.0, -0.5] * 20]})
        ho.wf.run = lambda *a, **k: {"passed": True, "combos": [{"n": 5}, {"n": 10}],
                                     "by_combo": [is_trades.iloc[:5], is_trades],
                                     "summary": {"pf": 1.5}}
        real_load = ho.load_bars
        ho.load_bars = lambda s, tf, include_holdout=False: bars
        try:
            ctx = ho.preflight(cand, ["XAUUSDm"], marker_dir=d, require_clean=False)
            check("tham số chọn trên 3 năm trước holdout (bộ đủ ≥30 lệnh)", ctx["params"] == {"n": 10})

            ho.simulate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash"))
            try:
                ho.spend(cand, ["XAUUSDm"], ctx, marker_dir=d)
            except RuntimeError:
                pass
            m = json.loads((d / "ma_test.json").read_text(encoding="utf-8"))
            check("crash sau khi claim → marker 'started' vẫn còn (holdout coi như đã dùng)",
                  m["status"] == "started" and m["params"] == {"n": 10})
            check("chạy lại sau crash bị từ chối",
                  _refused(ho.preflight, cand, ["XAUUSDm"], marker_dir=d, require_clean=False))

            (d / "ma_test.json").unlink()
            ho.simulate = real_sim
            v = ho.spend(cand, ["XAUUSDm"], ctx, marker_dir=d)
            m = json.loads((d / "ma_test.json").read_text(encoding="utf-8"))
            check("chạy đủ → marker 'done' kèm verdict, chỉ lệnh vào từ HOLDOUT_START",
                  m["status"] == "done" and m["verdict"]["passed"] == v["passed"]
                  and v["trades"] > 0 and pd.Timestamp(m["first_entry"]) >= HOLDOUT_START)
        finally:
            ho.wf.run, ho.simulate, ho.load_bars = real_run, real_sim, real_load


def test_bakeoff():
    import json
    import tempfile
    from pathlib import Path
    from backtest import bakeoff as bk
    print("Bake-off:")
    every = bk.discover(include_retired=True)
    check("tìm thấy đủ Candidate c1/c2/c4 (kể cả đã đóng)", {"c1_donchian", "c2_orb", "c4_smc"} <= set(every))
    check("Bake-off mặc định bỏ Candidate đã đóng (C1/C2/C4 sau Bake-off #1)",
          not {"c1_donchian", "c2_orb", "c4_smc"} & set(bk.discover()))

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        check("chưa có marker → 'chưa dùng'", bk.holdout_status("a", d) == "chưa dùng")
        (d / "a.json").write_text(json.dumps({"status": "started"}), encoding="utf-8")
        (d / "b.json").write_text(json.dumps({"status": "done", "verdict": {"passed": False, "pf": 0.8}}),
                                  encoding="utf-8")
        check("marker started/done đọc đúng", bk.holdout_status("a", d).startswith("ĐÃ DÙNG (dở")
              and bk.holdout_status("b", d).startswith("TRƯỢT"))

        fake = {
            "z_pass": {"passed": True, "summary": {"oos_trades": 300, "pf": 1.4, "max_dd_pct": 9.0,
                                                   "winning_windows": "7/9"}},
            "a_fail": {"passed": False, "summary": {"oos_trades": 150, "pf": 1.6, "max_dd_pct": 5.0,
                                                    "winning_windows": "8/9"}},
        }

        def runner(name):
            if name == "boom":
                raise SystemExit("dữ liệu thủng")
            return fake[name]
        rows = bk.run_bakeoff(["z_pass", "boom", "a_fail"], runner=runner, marker_dir=d)
        check("lỗi 1 Candidate không dừng cả Bake-off, ghi lại lỗi",
              [r["candidate"] for r in rows] == ["a_fail", "boom", "z_pass"]
              and rows[1]["gate"] == "LỖI" and "thủng" in rows[1]["note"])
        check("không xếp hạng theo PF: thứ tự theo tên, PF cao mà trượt vẫn là trượt",
              rows[0]["gate"] == "TRƯỢT" and rows[2]["gate"] == "QUA")
        check("chỉ Candidate QUA + holdout chưa dùng mới được gợi ý holdout",
              bk.holdout_next(rows) == ["z_pass"])
        check("có Candidate LỖI → CHƯA KẾT LUẬN (lỗi ≠ trượt)", bk.conclusion(rows).startswith("CHƯA KẾT LUẬN"))
        rows = bk.run_bakeoff(["a_fail"], runner=runner, marker_dir=d)
        check("tất cả trượt (không lỗi) → 0/N là kết quả hợp lệ", "0/1" in bk.conclusion(rows))


def test_killswitch():
    import json
    import tempfile
    from pathlib import Path
    from risk.killswitch import KillSwitch, limits_from_holdout
    print("Kill-switch:")
    t = lambda h: pd.Timestamp("2027-01-01") + pd.Timedelta(hours=h)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        path = d / "control.json"
        ks = KillSwitch(path, {"c1": 10.0, "c2": 4.0}, now=lambda: t(0))
        check("mới khởi tạo → được vào lệnh", ks.can_enter("c1") and ks.can_enter("c2"))

        dd_trades = [(t(1), -8.0), (t(2), -8.0)]          # ~15.4% DD @1% ≥ 1.5 × 10%
        reason = ks.evaluate("c1", dd_trades)
        check("DD ≥ 1.5× OOS DD → dừng, có lý do", reason is not None and "DD" in reason and not ks.can_enter("c1"))
        check("đã dừng → không báo lại lần 2", ks.evaluate("c1", dd_trades) is None)
        check("c2 không bị ảnh hưởng (per Strategy)", ks.can_enter("c2"))
        check("trạng thái dừng sống qua restart",
              not KillSwitch(path, {"c1": 10.0, "c2": 4.0}, now=lambda: t(3)).can_enter("c1"))

        ks.now = lambda: t(5)
        ks.resume("c1")
        check("/resume → tái kích hoạt từ đầu: lệnh cũ không tính, không dừng lại ngay",
              ks.can_enter("c1") and ks.evaluate("c1", dd_trades) is None)

        pf_trades = [(t(10 + i), 1.0 if i % 5 < 2 else -1.0) for i in range(50)]   # PF 20/30
        check("49 lệnh PF thấp → chưa xét PF", ks.evaluate("c1", pf_trades[:49]) is None)
        r = ks.evaluate("c1", pf_trades)
        check("50 lệnh PF < 0.9 → dừng", r is not None and "PF" in r and not ks.can_enter("c1"))

        armed_c2 = ks.status("c2")["armed_at"]
        ks.pause()
        check("/pause → mọi Strategy ngừng vào lệnh", not ks.can_enter("c1") and not ks.can_enter("c2"))
        ks.now = lambda: t(100)
        ks.resume()
        check("/resume → mở lại hết; Strategy chỉ bị pause giữ nguyên mốc DD",
              ks.can_enter("c1") and ks.can_enter("c2") and ks.status("c2")["armed_at"] == armed_c2
              and ks.status("c1")["armed_at"] == str(t(100)))
        ks.pause("c2")
        check("/pause c2 → chỉ c2", ks.can_enter("c1") and not ks.can_enter("c2"))
        try:
            ks.pause("nope")
            unknown = False
        except KeyError:
            unknown = True
        check("Strategy lạ → KeyError", unknown)

        hd = d / "holdout"
        hd.mkdir()
        (hd / "c1.json").write_text(json.dumps({"status": "done", "verdict": {"passed": True},
                                                "walkforward": {"max_dd_pct": 9.5}}), encoding="utf-8")
        (hd / "c2.json").write_text(json.dumps({"status": "done", "verdict": {"passed": False},
                                                "walkforward": {"max_dd_pct": 5.0}}), encoding="utf-8")
        check("giới hạn lấy từ marker holdout đã QUA", limits_from_holdout(["c1"], hd) == {"c1": 9.5})
        check("holdout trượt / chưa có → không được chạy",
              _refused(limits_from_holdout, ["c2"], hd) and _refused(limits_from_holdout, ["c4"], hd))


def test_telegram_control():
    import tempfile
    from pathlib import Path
    from execution.telegram_control import TelegramControl
    from risk.killswitch import KillSwitch
    print("Telegram control:")
    now = 1_800_000_000
    OWNER = 111

    def upd(uid, text, chat=OWNER, ctype="private", age=0):
        return {"update_id": uid, "message": {"chat": {"id": chat, "type": ctype}, "from": {"id": chat},
                                              "date": now - age, "text": text}}

    class FakeApi:
        def __init__(self):
            self.queue, self.sent, self.offsets = [], [], []

        def __call__(self, method, payload):
            if method == "getUpdates":
                self.offsets.append(payload.get("offset"))
                out, self.queue = self.queue, []
                return {"ok": True, "result": out}
            self.sent.append(payload)
            return {"ok": True}

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ks = KillSwitch(d / "control.json", {"c1": 10.0, "c2": 4.0})
        api = FakeApi()
        tc = TelegramControl(api, OWNER, ks, d / "tg.json", clock=lambda: now)

        api.queue = [upd(1, "/pause", chat=999), upd(2, "/pause", chat=OWNER, ctype="group")]
        tc.poll_once()
        check("người lạ / group chat → bỏ qua, không trả lời", api.sent == [] and ks.can_enter("c1"))

        api.queue = [upd(3, "/pause", age=600)]
        tc.poll_once()
        check("lệnh cũ > 5 phút → bỏ qua (không phát lại sau restart)", ks.can_enter("c1") and api.sent == [])

        api.queue = [upd(4, "/pause")]
        tc.poll_once()
        check("/pause từ chủ → dừng hết, có trả lời cho chủ",
              not ks.can_enter("c1") and not ks.can_enter("c2") and api.sent[-1]["chat_id"] == OWNER)
        api.queue = [upd(5, "/resume c1")]
        tc.poll_once()
        check("/resume c1 → chỉ c1", ks.can_enter("c1") and not ks.can_enter("c2"))
        api.queue = [upd(6, "/status")]
        tc.poll_once()
        txt = api.sent[-1]["text"]
        check("/status liệt kê từng Strategy kèm DD/PF", "c1" in txt and "c2" in txt and "DD" in txt and "PF" in txt)
        api.queue = [upd(7, "/buy XAUUSDm 1")]
        tc.poll_once()
        check("lệnh lạ (vd đặt lệnh) → chỉ trả trợ giúp, không có lệnh giao dịch",
              "/status" in api.sent[-1]["text"] and ks.can_enter("c1") and not ks.can_enter("c2"))

        check("offset tăng dần", api.offsets[-1] == 7)
        tc2 = TelegramControl(api, OWNER, ks, d / "tg.json", clock=lambda: now)
        tc2.poll_once()
        check("offset lưu đĩa, sống qua restart", api.offsets[-1] == 8)

        def broken(method, payload):
            raise OSError("mạng rớt")
        check("lỗi mạng → không crash vòng lặp",
              TelegramControl(broken, OWNER, ks, d / "tg2.json", clock=lambda: now).poll_once() == [])


def test_journal_and_alert():
    import tempfile
    from pathlib import Path
    from datetime import datetime
    from execution.journal import TradeJournal
    from execution.notifier import TelegramNotifier
    print("Journal theo Strategy + cảnh báo:")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "trades_X.csv"
        p.write_text("ticket,symbol,mode,created,direction,entry,sl,tp,lot,rr,risk_amount,reason,"
                     "result,closed,close_price,profit\n1,X,live,2027-01-01T00:00:00,buy,1,0,2,0.1,2,10,"
                     "old,success,2027-01-02T00:00:00,2,20\n", encoding="utf-8")
        j = TradeJournal("X", str(p))
        j.record_open(2, "buy", 1, 0, 2, 0.1, 2, 10.0, "r", strategy="c1")
        j.record_open(3, "sell", 1, 2, 0, 0.1, 2, 10.0, "r", strategy="c1")
        j.record_open(4, "sell", 1, 2, 0, 0.1, 2, 10.0, "r", strategy="c2")
        j.record_close(2, "failed", close_price=0, profit=-10.0, closed=datetime(2027, 1, 3))
        j.record_close(4, "success", close_price=0, profit=15.0, closed=datetime(2027, 1, 3))
        check("CSV cũ (không cột strategy) vẫn đọc được", TradeJournal("X", str(p)).has(1))
        check("closed_r theo Strategy: R = profit / risk_amount, bỏ lệnh còn mở",
              [r for _, r in j.closed_r("c1")] == [-1.0] and [r for _, r in j.closed_r("c2")] == [1.5])

    sent = []
    n = TelegramNotifier("t", "1")
    n._send = sent.append
    n.notify_alert("c1", "PF 50 lệnh gần nhất 0.80 < 0.9")
    check("cảnh báo Kill-switch escape HTML ('<' không làm hỏng tin)", "&lt; 0.9" in sent[0] and "c1" in sent[0])


class _FakeBroker:
    """Broker giả cho LiveRunner: bar theo chỉ số `k` (bar đang hình thành), giá cố định."""

    def __init__(self, bars, px=100.0, lag=pd.Timedelta(seconds=30)):
        self.bars, self.k, self.px, self.lag = bars, 10, px, lag
        self.pos, self.orders, self.closed, self.calls = [], {}, {}, []
        self.ticket = 100

    def closed_bars(self, tf, n):
        return self.bars.iloc[:self.k], self.bars.index[self.k]

    def positions(self):
        return list(self.pos)

    def pending(self):
        return list(self.orders)

    def price(self, d):
        return self.px

    def now(self):
        return self.bars.index[self.k] + self.lag

    def balance(self):
        return 10_000.0

    def symbol_info(self):
        return {"contract_size": 100.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0}

    def _new(self):
        self.ticket += 1
        return self.ticket

    def place_market(self, d, lot, sl, tp, comment):
        from execution.live import Pos
        t = self._new()
        self.pos.append(Pos(t, d, self.px, sl, tp, lot))
        self.calls.append(("market", d, lot, sl))
        return t

    def place_pending(self, d, kind, lot, price, sl, tp, expires, comment):
        t = self._new()
        self.orders[t] = (d, kind, lot, price, sl, tp, expires)
        self.calls.append(("pending", d, kind, price))
        return t

    def fill(self, t):
        from execution.live import Pos
        d, _, lot, price, sl, tp, _ = self.orders.pop(t)
        self.pos.append(Pos(t, d, price, sl, tp, lot))

    def cancel(self, t):
        self.calls.append(("cancel", t))
        return self.orders.pop(t, None) is not None

    def modify_sl(self, t, sl):
        self.calls.append(("modify", t, sl))
        self.pos = [replace(p, sl=sl) if p.ticket == t else p for p in self.pos]
        return True

    def close(self, t, profit=None):
        p = next(p for p in self.pos if p.ticket == t)
        pnl = (self.px - p.entry if p.direction == "buy" else p.entry - self.px) * p.lot * 100
        self.pos.remove(p)
        self.closed[t] = ("closed", self.px, round(pnl if profit is None else profit, 2))
        self.calls.append(("close", t))
        return True

    def close_info(self, t):
        return self.closed.get(t)


class _Notes:
    def __init__(self):
        self.alerts, self.opened, self.closed = [], [], []

    def notify_alert(self, name, reason):
        self.alerts.append((name, reason))

    def notify_opened(self, sym, plan):
        self.opened.append(plan)

    def notify_closed(self, sym, rec, result, profit):
        self.closed.append((rec["ticket"], result, profit))


def test_live_loop():
    print("live loop (broker giả):")
    import tempfile
    from pathlib import Path
    from execution.journal import TradeJournal
    from execution.live import LiveRunner, Slot, pending_expiry, slot_magic
    from risk.killswitch import KillSwitch

    bars = _bars([[100, 101, 99, 100]] * 40)
    t = bars.index
    nan = float("nan")

    class Scripted(Candidate):
        table: dict = None

        def signals(self, b, **kw):
            s = empty_signals(b.index)
            for col in ("oco_price", "oco_sl", "oco_tp", "trail_long", "trail_short", "max_bars"):
                s[col] = nan
            s["flat"] = False
            for at, vals in self.table.items():
                if at in s.index:
                    for k, v in vals.items():
                        s.loc[at, k] = v
            return s

    def rig(table, execute=True, lag=pd.Timedelta(seconds=30), oos_dd=10.0):
        tmp = Path(tempfile.mkdtemp())
        cand = Scripted(name="scripted", timeframe="H1")
        cand.table = table
        br = _FakeBroker(bars, lag=lag)
        notes = _Notes()
        # Đồng hồ cố định: nhật ký cắt giờ đóng về giây, armed_at "bây giờ" có micro giây
        # → lệnh đóng cùng giây với lúc arm sẽ bị coi là TRƯỚC mốc.
        ks = KillSwitch(tmp / "ks.json", {"scripted": oos_dd}, now=lambda: pd.Timestamp("2020-01-01"))
        slot = Slot(cand, {}, get_config("gold"), br, TradeJournal("XAUUSDm", tmp / "j.csv"), state_dir=tmp)
        return LiveRunner([slot], ks, notes, execute=execute), br, notes, ks, slot

    # Market → trail → trail lùi bị bỏ → trail vượt giá thì đóng.
    run, br, notes, ks, slot = rig({t[9]: {"signal": 1, "sl": 95.0},
                                    t[10]: {"trail_long": 97.0}, t[11]: {"trail_long": 96.0},
                                    t[12]: {"trail_long": 101.0}})
    run.step()
    check("market: vào đúng 1 lệnh, lot = 0.5% × 10k / (5 × 100) = 0.1",
          br.calls == [("market", "buy", 0.1, 95.0)])
    run.step()
    check("cùng bar không xử lý lại (không vào lệnh thứ 2)", len(br.calls) == 1)
    check("nhật ký: risk_amount = 1R thật, gắn tên Strategy",
          float(slot.journal.open_records()[0]["risk_amount"]) == 50.0
          and slot.journal.open_records()[0]["strategy"] == "scripted")
    br.k = 11
    run.step()
    check("trail hàng 10 dời SL 95 → 97", br.pos[0].sl == 97.0)
    br.k = 12
    run.step()
    check("trail lùi (96) không dời SL", br.pos[0].sl == 97.0 and br.calls[-1][0] == "modify")
    br.k = 13
    run.step()
    check("trail 101 vượt giá 100 → đóng ngay", not br.pos and br.calls[-1][0] == "close")
    run.step()
    check("đóng → nhật ký + báo Telegram", not slot.journal.open_records() and len(notes.closed) == 1)

    # OCO stop: 2 chân, 1 chân khớp → hủy chân kia; flat → đóng.
    run, br, notes, ks, slot = rig({t[9]: {"signal": 1, "entry_type": "stop", "entry_price": 102.0,
                                           "sl": 98.0, "tp": 106.0, "oco_price": 98.0, "oco_sl": 102.0,
                                           "oco_tp": 94.0, "expiry": 3},
                                    t[10]: {"flat": True}})
    run.step()
    legs = [c for c in br.calls if c[0] == "pending"]
    check("OCO: đặt 2 lệnh stop ngược hướng", [(c[1], c[3]) for c in legs] == [("buy", 102.0), ("sell", 98.0)])
    check("hết hạn = open(9) + (3+1)h", set(slot.state["pending"].values()) == {str(t[9] + pd.Timedelta(hours=4))})
    buy_leg = min(br.orders)
    br.fill(buy_leg)
    run.step()
    check("chân buy khớp → hủy chân sell, giữ 1 vị thế", not br.orders and len(br.pos) == 1
          and br.calls[-1] == ("cancel", buy_leg + 1))
    br.k = 11
    run.step()
    check("flat hàng 10 → đóng vị thế", not br.pos and br.calls[-1] == ("close", buy_leg))

    # max_bars: market khớp trong bar 10 → đóng khi bar 10, 11 đã đóng (= open bar 12, như engine).
    run, br, *_ = rig({t[9]: {"signal": 1, "sl": 95.0, "max_bars": 2}})
    run.step()
    br.k = 11
    run.step()
    check("max_bars=2: sau 1 bar vẫn giữ vị thế", len(br.pos) == 1)
    br.k = 12
    run.step()
    check("max_bars=2: đóng tại open bar khớp+2", not br.pos and br.calls[-1][0] == "close")
    # Lệnh chờ: đếm từ bar KHỚP (khớp trong bar 11 → đóng tại open bar 13).
    run, br, *_ = rig({t[9]: {"signal": 1, "entry_type": "stop", "entry_price": 102.0, "sl": 98.0,
                              "expiry": 5, "max_bars": 2}})
    run.step()
    br.k = 11
    br.fill(min(br.orders))
    run.step()
    br.k = 12
    run.step()
    check("max_bars lệnh chờ: chưa đóng ở open bar khớp+1", len(br.pos) == 1)
    br.k = 13
    run.step()
    check("max_bars lệnh chờ: đóng ở open bar khớp+2", not br.pos and br.calls[-1][0] == "close")
    # max_bars NaN → không đóng theo thời gian.
    run, br, *_ = rig({t[9]: {"signal": 1, "sl": 95.0, "max_bars": nan}})
    run.step()
    br.k = 20
    run.step()
    check("max_bars NaN → không time stop", len(br.pos) == 1)

    # Lệnh chờ hết hạn (bot tự hủy dự phòng).
    run, br, *_ = rig({t[9]: {"signal": -1, "entry_type": "limit", "entry_price": 101.0, "sl": 103.0,
                              "tp": 97.0, "expiry": 1}})
    run.step()
    br.k = 11
    run.step()
    check("limit expiry=1 hết hạn tại open(11) → hủy", not br.orders and br.calls[-1][0] == "cancel")

    # Dry run / tín hiệu trễ / SL sai phía → không đặt lệnh.
    run, br, *_ = rig({t[9]: {"signal": 1, "sl": 95.0}}, execute=False)
    run.step()
    check("dry run: không gọi broker", br.calls == [])
    run, br, *_ = rig({t[9]: {"signal": 1, "sl": 95.0}}, lag=pd.Timedelta(minutes=20))
    run.step()
    check("vào trễ > 15 phút (vd restart giữa bar) → bỏ", br.calls == [])
    run, br, *_ = rig({t[9]: {"signal": 1, "sl": 105.0}})
    run.step()
    check("SL sai phía entry → bỏ (như engine.open_pos)", br.calls == [])

    # Kill-switch: /pause chặn; 2 lệnh thua 1R với giới hạn 1.5×1% → dừng + báo.
    run, br, notes, ks, slot = rig({t[9]: {"signal": 1, "sl": 95.0}}, oos_dd=1.0)
    ks.pause("scripted")
    run.step()
    check("Strategy tạm dừng → không vào lệnh", br.calls == [])
    ks.resume("scripted")
    for tk in (1, 2):
        slot.journal.record_open(tk, "buy", 100, 95, "", 0.1, 0, 50.0, "x", strategy="scripted")
        br.closed[tk] = ("failed", 95.0, -50.0)
    br.k = 11
    run.step()
    check("2 × −1R → DD 1.99% ≥ 1.5% → Kill-switch dừng + notify_alert",
          not ks.can_enter("scripted") and len(notes.alerts) == 1)

    check("slot_magic khác nhau cho C1/C2/C4 trên cùng symbol",
          len({slot_magic(20260723, n) for n in ("c1_donchian", "c2_orb", "c4_smc")}) == 3)
    check("pending_expiry: expiry 0 tính như 1 (engine dùng max(exp, 1))",
          pending_expiry(t[0], "M15", 0) == t[0] + pd.Timedelta(minutes=30))


def test_crosscheck_verdict():
    print("crosscheck verdict:")
    from datafeed.crosscheck import verdict
    good = {"tf": "M15", "coverage_of_exness": 0.99, "median_close_diff_atr": -0.05,
            "p95_abs_close_diff_atr": 0.4, "return_corr": 0.97, "bar_direction_agree": 0.92}
    check("M15 tốt → mọi metric ĐẠT", all(ok for *_, ok in verdict(good)))
    v = {m: ok for m, _, _, ok in verdict({**good, "median_close_diff_atr": -0.15})}
    check("median lệch âm vượt |0.10| → TRƯỢT (so trị tuyệt đối)", not v["median_close_diff_atr"])
    v = {m: ok for m, _, _, ok in verdict({**good, "return_corr": float("nan")})}
    check("NaN → TRƯỢT", not v["return_corr"])
    v = {m: ok for m, _, _, ok in verdict({**good, "tf": "H4"})}
    check("H4 chặt hơn M15 (p95 0.4 ATR, corr 0.97 trượt ở H4)",
          not v["p95_abs_close_diff_atr"] and not v["return_corr"])


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định cp1252
    test_engine()
    test_swap_nights()
    test_causal_and_stats()
    test_c1()
    test_c2()
    test_c4()
    test_a_zfade()
    test_b_tsmom()
    test_c_intramom()
    test_auxdata()
    test_sign_flips()
    test_d_usdtrend()
    test_e_usdresid()
    test_f_realyield()
    test_holdout()
    test_bakeoff()
    test_killswitch()
    test_telegram_control()
    test_journal_and_alert()
    test_crosscheck_verdict()
    test_live_loop()
    print("TẤT CẢ OK")
