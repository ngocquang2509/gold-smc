"""
Bake-off (ADR 0001, GLOSSARY.md): chạy MỌI Candidate qua cùng walk-forward Acceptance
Gate trên cùng dữ liệu, giữ TẤT CẢ Candidate qua gate (không chọn "tốt nhất").

Bảng xếp theo TÊN, không theo PF: xếp hạng/chọn Candidate theo kết quả OOS là thêm một
bước tối ưu trên chính dữ liệu OOS → số liệu của kẻ thắng bị thổi phồng.
Không tự chạy Final Holdout — việc đó là thao tác 1 lần, tường minh (backtest.holdout).

    python -m backtest.bakeoff                       # mọi Candidate CHƯA ĐÓNG trong strategy/candidates/
    python -m backtest.bakeoff --candidates c1_donchian,c2_orb
"""
import argparse
import importlib
import json
import pkgutil
from datetime import datetime
from pathlib import Path

import pandas as pd

import strategy.candidates
from backtest import walkforward as wf
from backtest.holdout import MARKER_DIR


def discover(include_retired: bool = False) -> list[str]:
    """Candidate đã đóng (CANDIDATE.retired) bị bỏ trừ khi include_retired hoặc gọi tên qua --candidates."""
    names = sorted(m.name for m in pkgutil.iter_modules(strategy.candidates.__path__))
    if include_retired:
        return names
    return [n for n in names
            if not importlib.import_module(f"strategy.candidates.{n}").CANDIDATE.retired]


def holdout_status(name: str, marker_dir: Path = MARKER_DIR) -> str:
    path = marker_dir / f"{name}.json"
    if not path.exists():
        return "chưa dùng"
    m = json.loads(path.read_text(encoding="utf-8"))
    if m.get("status") != "done":
        return "ĐÃ DÙNG (dở — tính là đã dùng)"
    v = m["verdict"]
    return f"{'QUA' if v['passed'] else 'TRƯỢT'} (PF {v['pf']}, DD {v.get('max_dd_pct')}%)"


def _run_walkforward(name: str) -> dict:
    cand = importlib.import_module(f"strategy.candidates.{name}").CANDIDATE
    res = wf.run(cand, verbose=False)
    print(f"  đã lưu {wf.save_report(res)}.json", flush=True)
    return res


def run_bakeoff(names: list[str], runner=_run_walkforward, marker_dir: Path = MARKER_DIR) -> list[dict]:
    rows = []
    for name in sorted(names):
        print(f"[{name}] walk-forward…", flush=True)
        row = {"candidate": name, "gate": None, "holdout": holdout_status(name, marker_dir), "note": ""}
        try:
            res = runner(name)
        except (SystemExit, Exception) as e:   # 1 Candidate lỗi không làm hỏng cả Bake-off
            rows.append({**row, "gate": "LỖI", "note": str(e)})
            continue
        s = res["summary"]
        rows.append({**row, "gate": "QUA" if res["passed"] else "TRƯỢT",
                     "oos_trades": s["oos_trades"], "pf": s["pf"], "max_dd_pct": s["max_dd_pct"],
                     "winning_windows": s["winning_windows"],
                     "cagr_pct_at_1pct": s.get("cagr_pct_at_ref_risk"),
                     "failed_checks": ", ".join(k for k, ok in res.get("gate", {}).items() if not ok)})
    return rows


def conclusion(rows: list[dict]) -> str:
    """LỖI ≠ TRƯỢT: Bake-off chỉ có kết luận khi MỌI Candidate chạy xong."""
    errors = [r["candidate"] for r in rows if r["gate"] == "LỖI"]
    if errors:
        return f"CHƯA KẾT LUẬN — {len(errors)} Candidate lỗi ({', '.join(errors)}); sửa lỗi rồi chạy lại cả Bake-off."
    passed = [r["candidate"] for r in rows if r["gate"] == "QUA"]
    if not passed:
        return (f"Qua walk-forward: 0/{len(rows)} — kết quả hợp lệ: bot ở lại dry run; "
                "Bake-off sau cần ý tưởng MỚI, không tune lại.")
    return f"Qua walk-forward: {len(passed)}/{len(rows)} ({', '.join(passed)})"


def holdout_next(rows: list[dict]) -> list[str]:
    return [r["candidate"] for r in rows if r["gate"] == "QUA" and r["holdout"] == "chưa dùng"]


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows mặc định cp1252
    sys.stderr.reconfigure(encoding="utf-8")   # thông báo SystemExit đi qua stderr
    p = argparse.ArgumentParser(description="Bake-off: mọi Candidate qua walk-forward Acceptance Gate")
    p.add_argument("--candidates", help="danh sách module, mặc định: mọi Candidate chưa đóng")
    a = p.parse_args()
    names = a.candidates.split(",") if a.candidates else discover()
    rows = run_bakeoff(names)

    print("\n══ Bake-off (chi phí ×1.5, walk-forward 3y→1y, chưa gồm Final Holdout) ══")
    print(pd.DataFrame(rows).fillna("").to_string(index=False))
    print("\n" + conclusion(rows))
    for name in holdout_next(rows):
        print(f"  → bước kế (1 lần): python -m backtest.holdout --candidate {name}")

    wf.REPORT_DIR.mkdir(exist_ok=True)
    out = wf.REPORT_DIR / f"bakeoff-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Đã lưu: {out}")


if __name__ == "__main__":
    main()
