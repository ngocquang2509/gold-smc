"""
Kill-switch (GLOSSARY.md, ADR 0001): tự NGỪNG VÀO LỆNH MỚI của một Strategy khi
  - DD live ≥ 1.5 × max DD out-of-sample của chính Strategy đó, hoặc
  - PF 50 lệnh gần nhất < 0.9.
Chỉ mở lại bằng lệnh tay (/resume). Vị thế đang mở vẫn được quản lý bình thường.

Đo bằng R ở mức rủi ro tham chiếu 1% — cùng thước với max DD OOS của Acceptance Gate,
không phụ thuộc rủi ro live (đã giảm một nửa) hay các Strategy khác trong Portfolio.

/resume = BẮT ĐẦU LẠI (người dùng chọn 2026-10-03): đỉnh DD đặt lại tại lúc resume, cửa
sổ PF chỉ đếm lệnh đóng SAU resume và chỉ xét khi đủ 50 lệnh. Strategy chỉ bị /pause (không
bị dừng) giữ nguyên mốc cũ — resume không được "quên" drawdown đang có.

Trạng thái ghi đĩa (ghi nguyên tử) → restart máy không xóa được trạng thái dừng.
"""
import json
import math
import os
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

# ── Ngưỡng cố định (GLOSSARY.md) ──
DD_MULT = 1.5
PF_WINDOW = 50
PF_MIN = 0.9
REF_RISK_PCT = 1.0


def _dd_pct(rs: list[float]) -> float:
    eq = peak = 1.0
    worst = 0.0
    for r in rs:
        eq *= 1 + r * REF_RISK_PCT / 100
        peak = max(peak, eq)
        worst = max(worst, (peak - eq) / peak)
    return worst * 100


def _pf(rs: list[float]) -> float:
    win = sum(r for r in rs if r > 0)
    loss = -sum(r for r in rs if r < 0)
    return win / loss if loss > 0 else (math.inf if win > 0 else 0.0)


def limits_from_holdout(names: Iterable[str], marker_dir: Path = ROOT / "holdout") -> dict[str, float]:
    """max DD OOS (@1%) của từng Strategy, lấy từ marker Final Holdout. Strategy chưa qua
    holdout không được chạy live → từ chối."""
    out = {}
    for name in names:
        path = marker_dir / f"{name}.json"
        if not path.exists():
            raise SystemExit(f"{name}: chưa có Final Holdout ({path}) → không được chạy live")
        m = json.loads(path.read_text(encoding="utf-8"))
        if m.get("status") != "done" or not m.get("verdict", {}).get("passed"):
            raise SystemExit(f"{name}: Final Holdout chưa QUA → không được chạy live")
        out[name] = float(m["walkforward"]["max_dd_pct"])
    return out


class KillSwitch:
    def __init__(self, path: Path, oos_max_dd: dict[str, float],
                 now: Callable[[], pd.Timestamp] = pd.Timestamp.now):
        """`now` phải cùng đồng hồ với thời điểm đóng lệnh truyền vào evaluate()."""
        self.path = Path(path)
        self.oos_max_dd = dict(oos_max_dd)
        self.now = now
        self.state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        for name in self.oos_max_dd:
            self.state.setdefault(name, {"halted": False, "reason": "", "halted_at": "", "paused": False,
                                         "armed_at": str(self.now()), "dd_pct": 0.0, "pf": None, "n": 0})
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def _names(self, name: str | None) -> list[str]:
        if name is None:
            return list(self.oos_max_dd)
        if name not in self.oos_max_dd:
            raise KeyError(name)
        return [name]

    def can_enter(self, name: str) -> bool:
        s = self.state[name]
        return not s["halted"] and not s["paused"]

    def evaluate(self, name: str, closed: Iterable[tuple]) -> str | None:
        """closed = [(thời điểm đóng, R)] của Strategy. Trả về lý do nếu VỪA dừng (để báo
        Telegram), None nếu không đổi."""
        s = self.state[name]
        armed = pd.Timestamp(s["armed_at"])
        rs = [r for t, r in sorted(closed, key=lambda x: x[0]) if pd.Timestamp(t) > armed]
        limit = DD_MULT * self.oos_max_dd[name]
        dd = _dd_pct(rs)
        pf = _pf(rs[-PF_WINDOW:]) if len(rs) >= PF_WINDOW else None
        s.update(dd_pct=round(dd, 2), pf=None if pf is None else round(min(pf, 999.0), 3), n=len(rs))
        reason = None
        if not s["halted"]:
            if dd >= limit:
                reason = f"DD {dd:.1f}% ≥ {DD_MULT}× OOS {self.oos_max_dd[name]:.1f}% = {limit:.1f}%"
            elif pf is not None and pf < PF_MIN:
                reason = f"PF {PF_WINDOW} lệnh gần nhất {pf:.2f} < {PF_MIN}"
            if reason:
                s.update(halted=True, reason=reason, halted_at=str(self.now()))
        self._save()
        return reason

    def pause(self, name: str | None = None) -> list[str]:
        names = self._names(name)
        for n in names:
            self.state[n]["paused"] = True
        self._save()
        return names

    def resume(self, name: str | None = None) -> list[str]:
        names = self._names(name)
        for n in names:
            s = self.state[n]
            if s["halted"]:   # chỉ Strategy bị Kill-switch dừng mới bắt đầu lại từ đầu
                s.update(halted=False, reason="", halted_at="", armed_at=str(self.now()),
                         dd_pct=0.0, pf=None, n=0)
            s["paused"] = False
        self._save()
        return names

    def status(self, name: str) -> dict:
        s = dict(self.state[name])
        s["dd_limit_pct"] = round(DD_MULT * self.oos_max_dd[name], 2)
        s["can_enter"] = self.can_enter(name)
        return s
