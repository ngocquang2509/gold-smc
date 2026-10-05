"""
Walk-forward + Acceptance Gate (ADR 0001, GLOSSARY.md) — thước đo DUY NHẤT để một
Candidate được giao dịch tiền thật.

Quy trình:
  1. Nạp bar mọi symbol (KHÔNG gồm Final Holdout), chi phí ×1.5 (Cost Stress).
  2. Kiểm tra nhân quả của Candidate (assert_causal) — vi phạm là loại ngay.
  3. Với MỖI bộ tham số trong grid: mô phỏng toàn lịch sử trên mọi symbol (Shared
     Parameter Set → cùng tham số cho cả 3 symbol). Tín hiệu nhân quả nên mô phỏng
     1 lần rồi cắt theo cửa sổ là tương đương chạy từng cửa sổ.
  4. Cửa sổ cuốn chiếu: in-sample 3 năm → out-of-sample 1 năm, bước 1 năm. Mỗi cửa sổ
     chọn tham số tốt nhất TRÊN IN-SAMPLE (t-stat của R, gộp mọi symbol), rồi lấy lệnh
     out-of-sample của đúng tham số đó.
  5. Nối các đoạn out-of-sample → so với Acceptance Gate.

    python -m backtest.walkforward --candidate c1_donchian
"""
import argparse
import importlib
import itertools
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engine import simulate
from config.config import COST_STRESS, get_config, stressed
from datafeed.bars import HOLDOUT_START, load_bars
from strategy.candidate import Candidate

SYMBOLS = ["XAUUSDm", "EURUSDm", "GBPUSDm"]
IS_YEARS = 3
FIRST_YEAR = 2014
MIN_IS_TRADES = 30          # dưới mức này t-stat in-sample vô nghĩa → bộ tham số không được chọn

# ── Acceptance Gate (cố định — ADR 0001; KHÔNG sửa để Candidate lọt) ──
GATE_MIN_PF = 1.25
GATE_MAX_DD_PCT = 15.0
GATE_REF_RISK_PCT = 1.0     # DD đo ở mức rủi ro trần 1%/lệnh (rủi ro live chỉ ≤ mức này)
GATE_MIN_WINDOW_SHARE = 0.70
GATE_MIN_TRADES = 200

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


# ── Kiểm tra nhân quả ────────────────────────────────────
def assert_causal(cand: Candidate, bars: pd.DataFrame, params: dict, cuts: int = 4) -> None:
    """Tín hiệu tính trên dữ liệu bị cắt tại k phải TRÙNG tín hiệu tính trên toàn bộ dữ
    liệu ở mọi hàng < k. Khác nhau = hàng cũ đã "nhìn" dữ liệu tương lai → loại."""
    full = cand.signals(bars, **params)
    rng = np.random.default_rng(0)
    lo = len(bars) // 4
    for k in sorted(rng.integers(lo, len(bars), size=cuts)):
        part = cand.signals(bars.iloc[:k], **params)
        a, b = full.iloc[:k], part
        for col in part.columns:
            x, y = a[col].to_numpy(), b[col].to_numpy()
            if x.dtype.kind == "f":
                same = np.allclose(x, y, equal_nan=True)
            else:
                same = (x == y).all()
            if not same:
                bad = int(np.argmax(~((x == y) | (pd.isna(x) & pd.isna(y)))))
                raise AssertionError(
                    f"{cand.name}: LOOKAHEAD — cột '{col}' hàng {bars.index[bad]} đổi khi cắt "
                    f"dữ liệu tại {bars.index[k]} (params={params})"
                )


# ── Kiểm tra độ phủ dữ liệu ──────────────────────────────
MAX_DATA_GAP = pd.Timedelta(days=7)   # cuối tuần + lễ dài nhất ~4 ngày


def assert_coverage(symbol: str, bars: pd.DataFrame) -> None:
    """Dữ liệu thủng (tải dở) làm hỏng kết quả âm thầm: cửa sổ trống bị tính là lỗ,
    lệnh 'treo' qua lỗ hổng bị trừ swap nhiều năm. Từ chối chạy thay vì cho số sai."""
    gaps = bars.index.to_series().diff()
    bad = gaps[gaps > MAX_DATA_GAP]
    if len(bad):
        lst = ", ".join(f"{(t - g).date()}→{t.date()}" for t, g in bad.head(5).items())
        raise SystemExit(f"{symbol}: dữ liệu thủng {len(bad)} chỗ > {MAX_DATA_GAP.days} ngày ({lst}) "
                         f"— tải đủ rồi build lại bar (datafeed.dukascopy / datafeed.bars)")
    if bars.index[0] > pd.Timestamp(FIRST_YEAR, 1, 8):
        raise SystemExit(f"{symbol}: dữ liệu bắt đầu {bars.index[0].date()}, cần từ {FIRST_YEAR}-01")


# ── Thống kê trên chuỗi R ────────────────────────────────
def profit_factor(r: pd.Series) -> float:
    loss = -r[r < 0].sum()
    return r[r > 0].sum() / loss if loss > 0 else (math.inf if (r > 0).any() else 0.0)


def t_stat(r: pd.Series) -> float:
    if len(r) < 2 or r.std(ddof=1) == 0:
        return -math.inf
    return r.mean() / r.std(ddof=1) * math.sqrt(len(r))


def equity_curve(trades: pd.DataFrame, risk_pct: float) -> pd.Series:
    """Equity kép (bắt đầu 1.0) theo thời điểm đóng lệnh, mỗi lệnh rủi ro risk_pct%."""
    tr = trades.sort_values("exit_time")
    return pd.Series(np.cumprod(1 + tr["r"].to_numpy() * risk_pct / 100), index=tr["exit_time"])


def max_drawdown_pct(eq: pd.Series) -> float:
    if eq.empty:
        return 0.0
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq.to_numpy()]))
    return float(((peak - np.concatenate([[1.0], eq.to_numpy()])) / peak).max() * 100)


# ── Walk-forward ─────────────────────────────────────────
def windows(data_end: pd.Timestamp) -> list[tuple]:
    """[(is_start, oos_start, oos_end)] — OOS năm cuối có thể ngắn (dừng trước holdout)."""
    out = []
    year = FIRST_YEAR + IS_YEARS
    while pd.Timestamp(year=year, month=1, day=1) < data_end:
        oos_start = pd.Timestamp(year=year, month=1, day=1)
        out.append((pd.Timestamp(year=year - IS_YEARS, month=1, day=1), oos_start,
                    min(pd.Timestamp(year=year + 1, month=1, day=1), data_end)))
        year += 1
    return out


def _combos(grid: dict) -> list[dict]:
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def pick_best(by_combo: list[pd.DataFrame], is_start, is_end) -> tuple[int | None, float]:
    """Chỉ số bộ tham số có t-stat R cao nhất trên in-sample [is_start, is_end) — gộp mọi
    symbol, cần ≥ MIN_IS_TRADES lệnh. Dùng chung cho walk-forward và Final Holdout."""
    best, best_score = None, -math.inf
    for ci, tr in enumerate(by_combo):
        ins = tr[(tr["entry_time"] >= is_start) & (tr["entry_time"] < is_end)]["r"]
        if len(ins) >= MIN_IS_TRADES and t_stat(ins) > best_score:
            best, best_score = ci, t_stat(ins)
    return best, best_score


def run(cand: Candidate, symbols=SYMBOLS, stress: bool = True, verbose: bool = True) -> dict:
    bars = {s: load_bars(s, cand.timeframe) for s in symbols}
    for s, b in bars.items():
        assert_coverage(s, b)
    cfgs = {s: stressed(get_config(s)) if stress else get_config(s) for s in symbols}
    combos = _combos(cand.param_grid)
    for params in {0: combos[0], len(combos) - 1: combos[-1]}.values():
        assert_causal(cand, bars[symbols[0]], params)

    # Mỗi bộ tham số: lệnh gộp mọi symbol trên toàn lịch sử.
    by_combo = []
    for ci, params in enumerate(combos):
        parts = []
        for s in symbols:
            tr = simulate(bars[s], cand.signals(bars[s], **params), cfgs[s], cand.uses_be_partial)
            tr["symbol"] = s
            parts.append(tr)
        by_combo.append(pd.concat(parts, ignore_index=True))
        if verbose:
            print(f"  [{ci + 1}/{len(combos)}] {params}: {len(by_combo[-1])} lệnh", flush=True)

    data_end = min(HOLDOUT_START, max(b.index[-1] for b in bars.values()) + pd.Timedelta(seconds=1))
    rows, oos_parts = [], []
    for is_start, oos_start, oos_end in windows(data_end):
        best, best_score = pick_best(by_combo, is_start, oos_start)
        if best is None:
            oos = by_combo[0].iloc[0:0]
        else:
            tr = by_combo[best]
            oos = tr[(tr["entry_time"] >= oos_start) & (tr["entry_time"] < oos_end)]
        oos_parts.append(oos)
        rows.append({
            "oos": f"{oos_start.date()}→{(oos_end - pd.Timedelta(days=1)).date()}",
            "params": combos[best] if best is not None else None,
            "is_tstat": round(best_score, 2) if best is not None else None,
            "oos_trades": len(oos), "oos_r": round(oos["r"].sum(), 2),
            "oos_pf": round(profit_factor(oos["r"]), 2) if len(oos) else None,
        })

    oos_all = pd.concat(oos_parts, ignore_index=True)
    eq = equity_curve(oos_all, GATE_REF_RISK_PCT)
    years = max((data_end - windows(data_end)[0][1]).days / 365.25, 1e-9) if rows else 1
    win_share = (sum(1 for r in rows if r["oos_r"] > 0) / len(rows)) if rows else 0.0
    pf = profit_factor(oos_all["r"]) if len(oos_all) else 0.0
    dd = max_drawdown_pct(eq)
    checks = {
        f"OOS PF ≥ {GATE_MIN_PF}": pf >= GATE_MIN_PF,
        f"Max DD ≤ {GATE_MAX_DD_PCT}% @ {GATE_REF_RISK_PCT}% rủi ro": dd <= GATE_MAX_DD_PCT,
        f"Cửa sổ có lãi ≥ {GATE_MIN_WINDOW_SHARE:.0%}": win_share >= GATE_MIN_WINDOW_SHARE,
        f"Lệnh OOS ≥ {GATE_MIN_TRADES}": len(oos_all) >= GATE_MIN_TRADES,
    }
    return {
        "candidate": cand.name, "timeframe": cand.timeframe, "symbols": symbols,
        "cost_stress": COST_STRESS if stress else 1.0, "windows": rows,
        "summary": {
            "oos_trades": len(oos_all), "pf": round(pf, 3), "max_dd_pct": round(dd, 2),
            "winning_windows": f"{sum(1 for r in rows if r['oos_r'] > 0)}/{len(rows)}",
            "winrate": round((oos_all["r"] > 0).mean(), 3) if len(oos_all) else None,
            "avg_r": round(oos_all["r"].mean(), 3) if len(oos_all) else None,
            "cagr_pct_at_ref_risk": round((max(eq.iloc[-1], 0.0) ** (1 / years) - 1) * 100, 2) if len(eq) else None,
            "by_symbol_r": oos_all.groupby("symbol")["r"].sum().round(2).to_dict() if len(oos_all) else {},
        },
        # Không stress chi phí thì không bao giờ tính là qua gate.
        "gate": checks, "passed": stress and all(checks.values()),
        "oos_trades": oos_all,
        "combos": combos, "by_combo": by_combo,   # để chọn tham số cho Final Holdout (không lưu)
    }


def print_report(res: dict) -> None:
    print(f"\n══ {res['candidate']} ({res['timeframe']}, {', '.join(res['symbols'])}, chi phí ×{res['cost_stress']}) ══")
    print(pd.DataFrame(res["windows"]).to_string(index=False))
    print("\nTổng OOS:", json.dumps(res["summary"], ensure_ascii=False, default=str))
    for name, ok in res["gate"].items():
        print(f"  {'✅' if ok else '❌'} {name}")
    print(f"\n→ {'QUA' if res['passed'] else 'TRƯỢT'} Acceptance Gate (chưa gồm Final Holdout)")


def save_report(res: dict) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = REPORT_DIR / f"{res['candidate']}-{stamp}"
    res["oos_trades"].to_csv(base.with_suffix(".trades.csv"), index=False)
    meta = {k: v for k, v in res.items() if k not in ("oos_trades", "combos", "by_combo")}
    base.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return base


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định cp1252
    sys.stderr.reconfigure(encoding="utf-8")   # thông báo SystemExit đi qua stderr
    p = argparse.ArgumentParser(description="Walk-forward + Acceptance Gate cho một Candidate")
    p.add_argument("--candidate", required=True, help="module trong strategy/candidates/, vd c1_donchian")
    p.add_argument("--symbols", default=",".join(SYMBOLS))
    p.add_argument("--no-stress", action="store_true", help="chi phí thật ×1 (chỉ để tham khảo — gate luôn dùng stress)")
    a = p.parse_args()
    cand = importlib.import_module(f"strategy.candidates.{a.candidate}").CANDIDATE
    res = run(cand, a.symbols.split(","), stress=not a.no_stress)
    print_report(res)
    print(f"Đã lưu: {save_report(res)}.json")


if __name__ == "__main__":
    main()
