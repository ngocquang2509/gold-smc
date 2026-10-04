"""
Đối chiếu bar Dukascopy với bar Exness (MT5) — chứng minh dữ liệu nghiên cứu khớp đủ
với thị trường bot thực sự giao dịch (ADR 0001, Q9/Q21).

CHỈ dùng cửa sổ Jul 2024 → Sep 2025 — KHÔNG BAO GIỜ chạm Final Holdout (Oct 2025+).
Cần terminal MT5 đang mở & đăng nhập (Windows).

    python -m datafeed.crosscheck --symbol XAUUSDm
"""
import argparse
import sys

import pandas as pd

from datafeed.bars import HOLDOUT_START, load_bars
from execution.mt5_client import MT5_AVAILABLE, TIMEFRAME_MAP, mt5

CHECK_START = pd.Timestamp("2024-07-01")
CHECK_END = pd.Timestamp("2025-09-30 23:59")
assert CHECK_END < HOLDOUT_START, "cửa sổ đối chiếu không được chạm Final Holdout"

# Ngưỡng "đủ khớp": chốt 2026-10-03 TRƯỚC khi xem bất kỳ kết quả đối chiếu/gate nào.
# Trượt ngưỡng = điều tra pipeline (lệch múi giờ, thang giá, thiếu ngày), KHÔNG nới ngưỡng.
# (metric, "min"/"maxabs", ngưỡng) — "maxabs" so |giá trị| ≤ ngưỡng.
THRESHOLDS = {
    "M15": [("coverage_of_exness", "min", 0.98), ("median_close_diff_atr", "maxabs", 0.10),
            ("p95_abs_close_diff_atr", "maxabs", 0.50), ("return_corr", "min", 0.95),
            ("bar_direction_agree", "min", 0.90)],
    "H4": [("coverage_of_exness", "min", 0.98), ("median_close_diff_atr", "maxabs", 0.10),
           ("p95_abs_close_diff_atr", "maxabs", 0.25), ("return_corr", "min", 0.98),
           ("bar_direction_agree", "min", 0.95)],
}


def verdict(row: dict) -> list[tuple[str, float, str, bool]]:
    """Chấm 1 dòng kết quả `compare` theo THRESHOLDS → [(metric, giá trị, ngưỡng, đạt?)].
    Giá trị NaN (vd. thiếu dữ liệu) tính là TRƯỢT."""
    out = []
    for metric, kind, bound in THRESHOLDS[row["tf"]]:
        v = row[metric]
        ok = bool(pd.notna(v) and (v >= bound if kind == "min" else abs(v) <= bound))
        out.append((metric, v, f"≥ {bound}" if kind == "min" else f"|x| ≤ {bound}", ok))
    return out


def _exness_bars(symbol: str, tf: str) -> pd.DataFrame:
    rates = mt5.copy_rates_range(symbol, TIMEFRAME_MAP[tf],
                                 CHECK_START.to_pydatetime(), CHECK_END.to_pydatetime())
    if rates is None or len(rates) == 0:
        raise SystemExit(f"MT5 không trả bar {symbol} {tf}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df.set_index("time")[["open", "high", "low", "close"]]


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def compare(symbol: str, tf: str) -> dict:
    duka = load_bars(symbol, tf).loc[CHECK_START:CHECK_END]
    exn = _exness_bars(symbol, tf)
    both = duka.join(exn, how="inner", lsuffix="_d", rsuffix="_e")
    atr = _atr(exn).reindex(both.index)
    diff_atr = ((both["close_d"] - both["close_e"]) / atr).dropna()
    ret_d = both["close_d"].pct_change()
    ret_e = both["close_e"].pct_change()
    dir_d = (both["close_d"] > both["open_d"])
    dir_e = (both["close_e"] > both["open_e"])
    return {
        "tf": tf,
        "bars_exness": len(exn),
        "bars_duka": len(duka),
        "coverage_of_exness": len(both) / len(exn),
        "median_close_diff_atr": diff_atr.median(),
        "p95_abs_close_diff_atr": diff_atr.abs().quantile(0.95),
        "return_corr": ret_d.corr(ret_e),
        "bar_direction_agree": (dir_d == dir_e).mean(),
    }


def main():
    p = argparse.ArgumentParser(description="Đối chiếu bar Dukascopy vs Exness MT5 (Jul 2024 → Sep 2025)")
    p.add_argument("--symbol", required=True)
    a = p.parse_args()
    for stream in (sys.stdout, sys.stderr):   # console Windows mặc định cp1252
        stream.reconfigure(encoding="utf-8")
    if not MT5_AVAILABLE or not mt5.initialize():
        raise SystemExit("Cần terminal MT5 đang chạy (Windows)")
    if mt5.symbol_info(a.symbol) is None or not mt5.symbol_select(a.symbol, True):
        acc = mt5.account_info()
        mt5.shutdown()
        raise SystemExit(f"Tài khoản {acc.login} ({acc.server}) không có symbol {a.symbol} — "
                         f"đăng nhập tài khoản Exness Standard (symbol hậu tố 'm')")
    try:
        rows = [compare(a.symbol, tf) for tf in ("M15", "H4")]
    finally:
        mt5.shutdown()
    print(f"\n{a.symbol}: Dukascopy vs Exness, {CHECK_START.date()} → {CHECK_END.date()}")
    print(pd.DataFrame(rows).set_index("tf").T.to_string(float_format=lambda v: f"{v:.4f}"))
    all_ok = True
    for row in rows:
        print(f"\n{row['tf']}:")
        for metric, v, bound, ok in verdict(row):
            all_ok &= ok
            print(f"  {'ĐẠT ' if ok else 'TRƯỢT'} {metric:<24} {v:.4f}  (ngưỡng {bound})")
    print(f"\n{a.symbol}: {'ĐẠT — dữ liệu Dukascopy dùng được' if all_ok else 'TRƯỢT — điều tra pipeline, KHÔNG nới ngưỡng'}")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
