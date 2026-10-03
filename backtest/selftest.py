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


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định cp1252
    test_engine()
    test_causal_and_stats()
    print("TẤT CẢ OK")
