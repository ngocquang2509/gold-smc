"""
Final Holdout — chạy ĐÚNG 1 LẦN cho mỗi Candidate (ADR 0001, GLOSSARY.md).

Thứ tự (không đảo):
  1. preflight (không tác dụng phụ): marker chưa có → code đã commit → walk-forward QUA
     → chọn tham số trên 3 năm ngay trước holdout (cùng t-stat như walk-forward)
     → dữ liệu holdout đủ (Oct 2025 → Sep 2026, không thủng).
  2. claim: tạo marker `holdout/<candidate>.json` (tạo độc quyền, git-tracked) TRƯỚC khi
     tính kết quả. Crash/hủy sau bước này vẫn tính là ĐÃ DÙNG holdout.
  3. Mô phỏng bằng tham số đã đóng băng, chi phí ×1.5, chỉ lấy lệnh vào từ HOLDOUT_START.
     Ghi verdict vào marker → commit marker.

Ngưỡng qua (cố định trước khi có Candidate nào chạy; KHÔNG sửa): PF ≥ 1.0 và DD ≤ 15% @ 1%.
12 tháng là mẫu nhỏ — holdout kiểm tra edge CÒN SỐNG, walk-forward 8+ năm mới là phép đo chính.

    python -m backtest.holdout --candidate c1_donchian            # chỉ preflight
    python -m backtest.holdout --candidate c1_donchian --confirm  # TIÊU holdout (1 lần)
"""
import argparse
import hashlib
import importlib
import inspect
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest import walkforward as wf
from backtest.engine import simulate
from config.config import COST_STRESS, get_config, stressed
from datafeed.bars import DATA_END, HOLDOUT_START, load_bars
from strategy.candidate import Candidate

ROOT = Path(__file__).resolve().parent.parent
MARKER_DIR = ROOT / "holdout"
HOLDOUT_END = pd.Timestamp(DATA_END) + pd.Timedelta(days=1)
SELECT_START = HOLDOUT_START - pd.DateOffset(years=wf.IS_YEARS)

# ── Ngưỡng Final Holdout (cố định — chọn 2026-10-03 trước mọi kết quả; KHÔNG sửa) ──
HOLDOUT_MIN_PF = 1.0
HOLDOUT_MAX_DD_PCT = 15.0

# Code quyết định kết quả — phải sạch trong git để commit ghi trong marker tái tạo được.
TRACKED_PATHS = ["backtest", "config", "datafeed", "risk", "strategy"]


def holdout_verdict(trades: pd.DataFrame) -> dict:
    eq = wf.equity_curve(trades, wf.GATE_REF_RISK_PCT) if len(trades) else pd.Series(dtype=float)
    pf = wf.profit_factor(trades["r"]) if len(trades) else 0.0
    dd = wf.max_drawdown_pct(eq)
    return {
        "trades": len(trades), "pf": round(pf, 3), "max_dd_pct": round(dd, 2),
        "total_r": round(float(trades["r"].sum()), 2) if len(trades) else 0.0,
        "by_symbol_r": trades.groupby("symbol")["r"].sum().round(2).to_dict() if len(trades) else {},
        "passed": bool(len(trades) and pf >= HOLDOUT_MIN_PF and dd <= HOLDOUT_MAX_DD_PCT),
    }


def claim(name: str, info: dict, marker_dir: Path = MARKER_DIR) -> Path:
    """Tạo marker độc quyền (mode 'x'): đã có → từ chối, không ghi đè."""
    marker_dir.mkdir(parents=True, exist_ok=True)
    path = marker_dir / f"{name}.json"
    try:
        with open(path, "x", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2, default=str)
    except FileExistsError:
        raise SystemExit(f"{name}: Final Holdout ĐÃ DÙNG ({path}) — không chạy lại, không tune lại.")
    return path


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def _assert_clean_git() -> str:
    try:
        dirty = _git("status", "--porcelain", "--", *TRACKED_PATHS)
        head = _git("rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError) as e:
        raise SystemExit(f"Không đọc được git ({e}) — holdout cần commit để tái tạo được")
    if dirty:
        raise SystemExit(f"Còn thay đổi chưa commit trong {TRACKED_PATHS}:\n{dirty}\n→ commit trước khi dùng holdout")
    return head


def _assert_holdout_coverage(symbol: str, bars: pd.DataFrame) -> None:
    part = bars[bars.index >= SELECT_START]
    gaps = part.index.to_series().diff()
    if (gaps > wf.MAX_DATA_GAP).any():
        raise SystemExit(f"{symbol}: dữ liệu thủng > {wf.MAX_DATA_GAP.days} ngày từ {SELECT_START.date()} — sửa trước khi dùng holdout")
    if part.empty or part.index[-1] < HOLDOUT_END - wf.MAX_DATA_GAP:
        last = part.index[-1].date() if len(part) else None
        raise SystemExit(f"{symbol}: dữ liệu holdout chỉ tới {last}, cần tới {DATA_END}")


def preflight(cand: Candidate, symbols=wf.SYMBOLS, marker_dir: Path = MARKER_DIR,
              require_clean: bool = True) -> dict:
    """Mọi kiểm tra trước khi tiêu holdout. Không ghi gì."""
    if (marker_dir / f"{cand.name}.json").exists():
        raise SystemExit(f"{cand.name}: Final Holdout ĐÃ DÙNG ({marker_dir / (cand.name + '.json')})")
    commit = _assert_clean_git() if require_clean else None
    res = wf.run(cand, symbols, verbose=False)
    if not res.get("passed"):
        raise SystemExit(f"{cand.name}: chưa qua walk-forward Acceptance Gate → không được xem holdout")
    best, score = wf.pick_best(res["by_combo"], SELECT_START, HOLDOUT_START)
    if best is None:
        raise SystemExit(f"{cand.name}: không bộ tham số nào đủ {wf.MIN_IS_TRADES} lệnh in-sample trước holdout")
    bars = {s: load_bars(s, cand.timeframe, include_holdout=True) for s in symbols}
    for s, b in bars.items():
        _assert_holdout_coverage(s, b)
    return {"params": res["combos"][best], "is_tstat": score, "bars": bars,
            "commit": commit, "walkforward": res.get("summary")}


def spend(cand: Candidate, symbols, ctx: dict, marker_dir: Path = MARKER_DIR) -> dict:
    """KHÔNG đảo ngược: claim marker rồi mới tính kết quả."""
    src = inspect.getsource(type(cand))
    info = {
        "candidate": cand.name, "status": "started", "started_at": datetime.now().isoformat(),
        "commit": ctx["commit"], "source_sha256": hashlib.sha256(src.encode()).hexdigest(),
        "symbols": symbols, "params": ctx["params"], "is_tstat": round(ctx["is_tstat"], 3),
        "selection_window": [str(SELECT_START.date()), str(HOLDOUT_START.date())],
        "holdout": [str(HOLDOUT_START.date()), str(DATA_END)], "cost_stress": COST_STRESS,
        "thresholds": {"min_pf": HOLDOUT_MIN_PF, "max_dd_pct": HOLDOUT_MAX_DD_PCT,
                       "ref_risk_pct": wf.GATE_REF_RISK_PCT},
        "walkforward": ctx["walkforward"],
    }
    path = claim(cand.name, info, marker_dir)

    parts = []
    for s in symbols:
        b = ctx["bars"][s]
        tr = simulate(b, cand.signals(b, **ctx["params"]), stressed(get_config(s)), cand.uses_be_partial)
        tr = tr[tr["entry_time"] >= HOLDOUT_START].copy()
        tr["symbol"] = s
        parts.append(tr)
    trades = pd.concat(parts, ignore_index=True)
    verdict = holdout_verdict(trades)

    info.update(status="done", finished_at=datetime.now().isoformat(), verdict=verdict,
                first_entry=trades["entry_time"].min() if len(trades) else None)
    path.write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    trades.to_csv(path.with_suffix(".trades.csv"), index=False)
    return verdict


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định cp1252
    sys.stderr.reconfigure(encoding="utf-8")   # thông báo SystemExit đi qua stderr
    p = argparse.ArgumentParser(description="Final Holdout — 1 lần cho mỗi Candidate")
    p.add_argument("--candidate", required=True)
    p.add_argument("--symbols", default=",".join(wf.SYMBOLS))
    p.add_argument("--confirm", action="store_true", help="TIÊU holdout (không đảo ngược được)")
    a = p.parse_args()
    cand = importlib.import_module(f"strategy.candidates.{a.candidate}").CANDIDATE
    symbols = a.symbols.split(",")
    ctx = preflight(cand, symbols)
    print(f"Preflight OK: {cand.name} qua walk-forward; tham số đóng băng {ctx['params']} "
          f"(t-stat IS {ctx['is_tstat']:.2f}), commit {ctx['commit'][:8]}")
    if not a.confirm:
        print("→ Chưa tiêu holdout. Chạy lại với --confirm (chỉ được 1 lần, kết quả là cuối cùng).")
        return
    v = spend(cand, symbols, ctx)
    print(json.dumps(v, ensure_ascii=False, indent=2))
    print(f"\n→ {'QUA' if v['passed'] else 'TRƯỢT'} Final Holdout. Commit holdout/{cand.name}.json ngay.")


if __name__ == "__main__":
    main()
