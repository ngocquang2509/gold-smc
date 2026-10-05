"""
Engine mô phỏng bar-by-bar cho MỘT symbol × MỘT bộ tham số.

Kết quả tính bằng R: mỗi lệnh được size sao cho rủi ro gốc (entry→SL) = 1 đơn vị tiền,
nên PnL ròng (sau chi phí) chính là bội số R. Nhờ vậy PF/expectancy không phụ thuộc
vốn hay làm tròn lot; drawdown ở mức rủi ro bất kỳ dựng lại được từ chuỗi R
(xem walkforward.equity_curve).

Quy tắc khớp (bảo thủ, ghi nhận để không ai "sửa" cho đẹp số):
  - Tín hiệu hàng i → lệnh bắt đầu từ bar i+1. Market khớp tại open bar i+1.
  - Lệnh stop khớp tại max(open, giá) (gap qua giá → khớp open, xấu hơn).
    Lệnh limit khớp đúng giá (không thưởng gap có lợi).
  - Bar khớp lệnh chờ: chỉ xét SL (không xét TP) — không biết thứ tự trong bar.
  - Còn lại dùng risk.manage_step: SL trước TP, SL gap → khớp open (lỗ > 1R).
  - Mỗi symbol tối đa 1 vị thế + 1 lệnh chờ; tín hiệu mới trong lúc đó bị bỏ qua.
  - max_bars (cột tùy chọn, NaN/0 = tắt): vị thế còn mở sau max_bars bar tính từ bar
    KHỚP (lệnh chờ đếm từ lúc khớp, không phải lúc đặt) → đóng tại open bar khớp+max_bars
    ("TIME"). SL/TP/flat trước hạn vẫn thắng.
  - OCO (cột oco_*): 2 chân lệnh chờ ngược hướng, chân khớp trước hủy chân kia. Cả 2
    cùng chạm trong 1 bar → lấy chân gần open hơn (SL của nó thường là chân kia →
    bị SL ngay trên bar khớp → -1R, tức tự động bảo thủ).
"""
from dataclasses import replace

import numpy as np
import pandas as pd

from config.config import TradingConfig
from risk.risk import PositionState, manage_step, position_pnl, trade_cost

INF = float("inf")


def _no_mgmt(cfg: TradingConfig) -> TradingConfig:
    """Tắt BE/partial của manage_step (Candidate không dùng → không tính vào budget)."""
    return replace(cfg, move_sl_to_be_at_rr=INF, partial_close_at_rr=INF)


def simulate(bars: pd.DataFrame, sig: pd.DataFrame, cfg: TradingConfig,
             uses_be_partial: bool = False, apply_costs: bool = True) -> pd.DataFrame:
    """Trả về DataFrame lệnh: entry_time, exit_time, direction, entry, sl, exit, r, cost_r, result."""
    if not uses_be_partial:
        cfg = _no_mgmt(cfg)
    t = bars.index
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    s_sig = sig["signal"].to_numpy(int)
    s_type = sig["entry_type"].to_numpy(object)
    s_px = sig["entry_price"].to_numpy(float)
    s_sl = sig["sl"].to_numpy(float)
    s_tp = sig["tp"].to_numpy(float)
    s_exp = sig["expiry"].to_numpy(int)
    tr_long = sig["trail_long"].to_numpy(float) if "trail_long" in sig else None
    tr_short = sig["trail_short"].to_numpy(float) if "trail_short" in sig else None
    flat = sig["flat"].to_numpy(bool) if "flat" in sig else None
    s_mb = sig["max_bars"].to_numpy(float) if "max_bars" in sig else None
    oco = (tuple(sig[k].to_numpy(float) for k in ("oco_price", "oco_sl", "oco_tp"))
           if "oco_price" in sig else None)
    cs = cfg.contract_size
    n = len(t)

    trades: list[dict] = []
    pos: PositionState | None = None
    pos_rows: list[dict] = []
    pending = None          # (legs [(direction, price, sl, tp)], kind, expires_at_index, max_bars)
    flat_next = False
    close_at = n            # chỉ số bar mà vị thế hiện tại bị đóng theo max_bars (n = không)
    signal_idx = np.flatnonzero(s_sig != 0)

    def open_pos(direction, entry, sl, tp, i):
        dist = abs(entry - sl)
        if dist <= 0 or (direction == "buy") != (sl < entry):
            return None
        return PositionState(direction=direction, entry=entry, sl=sl, original_sl=sl,
                             tp=tp if not np.isnan(tp) else (INF if direction == "buy" else -INF),
                             lot=1.0 / (dist * cs), cs=cs, entry_time=t[i])

    def mb(j) -> int:
        v = s_mb[j] if s_mb is not None else float("nan")
        return int(v) if v == v and v > 0 else 0

    def close_trade(rows, state, exit_px, i, result_override=None):
        r = sum(row["pnl"] for row in rows)
        trades.append({
            "entry_time": state.entry_time, "exit_time": t[i], "direction": state.direction,
            "entry": state.entry, "sl": state.original_sl, "exit": exit_px,
            "r": r, "cost_r": sum(row["cost"] for row in rows),
            "result": result_override or rows[-1]["result"],
        })

    i = 0
    while i < n:
        # Rảnh (không vị thế, không lệnh chờ): nhảy thẳng tới bar sau tín hiệu kế tiếp.
        if pos is None and pending is None:
            k = np.searchsorted(signal_idx, i - 1)   # tín hiệu ở hàng ≥ i-1
            if k >= len(signal_idx):
                break
            i = max(i, signal_idx[k] + 1)
            if i >= n:
                break
            j = i - 1
            direction = "buy" if s_sig[j] > 0 else "sell"
            if s_type[j] == "market":
                pos = open_pos(direction, o[i], s_sl[j], s_tp[j], i)
                pos_rows = []
                close_at = i + mb(j) if mb(j) else n
                if pos is None:
                    i += 1
                    continue
                fill_bar_pending = False
            else:
                legs = [(direction, s_px[j], s_sl[j], s_tp[j])]
                if oco is not None and not np.isnan(oco[0][j]):
                    legs.append(("sell" if direction == "buy" else "buy", oco[0][j], oco[1][j], oco[2][j]))
                pending = (legs, s_type[j], i + max(s_exp[j], 1), mb(j))

        fill_bar_pending = False
        if pending is not None:
            legs, kind, expires, pend_mb = pending
            if i >= expires:
                pending = None
                continue
            hits = []
            for direction, px, sl, tp in legs:
                is_buy = direction == "buy"
                if kind == "stop":
                    hit = h[i] >= px if is_buy else l[i] <= px
                    fill = (max(o[i], px) if is_buy else min(o[i], px))
                else:
                    hit = l[i] <= px if is_buy else h[i] >= px
                    fill = px
                if hit:
                    hits.append((abs(fill - o[i]), direction, fill, sl, tp))
            if not hits:
                i += 1
                continue
            _, direction, fill, sl, tp = min(hits)
            pending = None
            pos = open_pos(direction, fill, sl, tp, i)
            pos_rows = []
            close_at = i + pend_mb if pend_mb else n
            if pos is None:
                i += 1
                continue
            fill_bar_pending = True

        # ── Quản lý vị thế trên bar i ─────────────────────
        if flat_next or i >= close_at:
            cost = trade_cost(pos, t[i], pos.lot, cfg) if apply_costs else 0.0
            pnl = position_pnl(pos, o[i]) - cost
            pos_rows.append({"pnl": pnl, "cost": cost, "result": "FLAT" if flat_next else "TIME"})
            flat_next = False
            close_trade(pos_rows, pos, o[i], i)
            pos = None
            i += 1
            continue

        if fill_bar_pending:
            # Bar khớp: chỉ xét SL (khớp đúng mức SL — lệnh vào trong bar nên không có gap).
            hit_sl = l[i] <= pos.sl if pos.direction == "buy" else h[i] >= pos.sl
            if hit_sl:
                cost = trade_cost(pos, t[i], pos.lot, cfg) if apply_costs else 0.0
                pos_rows.append({"pnl": position_pnl(pos, pos.sl) - cost, "cost": cost, "result": "SL"})
                close_trade(pos_rows, pos, pos.sl, i)
                pos = None
                i += 1
                continue
        else:
            _, rows, closed = manage_step(pos, o[i], h[i], l[i], c[i], t[i], cfg, apply_costs)
            pos_rows.extend(rows)
            if closed:
                close_trade(pos_rows, pos, rows[-1]["exit"], i)
                pos = None
                i += 1
                continue

        # Trail (áp dụng từ bar kế) + cờ đóng theo thời gian.
        if pos.direction == "buy" and tr_long is not None and not np.isnan(tr_long[i]):
            pos.sl = max(pos.sl, tr_long[i])
        elif pos.direction == "sell" and tr_short is not None and not np.isnan(tr_short[i]):
            pos.sl = min(pos.sl, tr_short[i])
        if flat is not None and flat[i]:
            flat_next = True
        i += 1

    if pos is not None:   # hết dữ liệu: đóng tại close cuối
        cost = trade_cost(pos, t[-1], pos.lot, cfg) if apply_costs else 0.0
        pos_rows.append({"pnl": position_pnl(pos, c[-1]) - cost, "cost": cost, "result": "EOD"})
        close_trade(pos_rows, pos, c[-1], n - 1)

    return pd.DataFrame(trades, columns=["entry_time", "exit_time", "direction", "entry",
                                         "sl", "exit", "r", "cost_r", "result"])
