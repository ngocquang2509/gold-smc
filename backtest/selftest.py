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


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định cp1252
    test_engine()
    test_causal_and_stats()
    test_c1()
    test_c2()
    test_c4()
    test_holdout()
    print("TẤT CẢ OK")
