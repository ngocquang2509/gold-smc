"""
Quản lý vốn & rủi ro:
- Position sizing theo % rủi ro cố định (≤1%/lệnh — xem ADR 0001).
- Kiểm tra R:R tối thiểu trước khi vào lệnh.
- Daily loss limit + portfolio heat cap.
- ENGINE QUẢN LÝ LỆNH DÙNG CHUNG (breakeven + partial + mô phỏng SL/TP):
  backtest, live và paper (dry_run) đều gọi chung engine này để KHÔNG lệch nhau.
"""
from dataclasses import dataclass
import pandas as pd


@dataclass
class TradePlan:
    direction: str        # "buy" | "sell"
    entry: float
    sl: float
    tp: float
    lot: float
    rr: float
    risk_amount: float
    reason: str
    order_kind: str = "market"         # "market" | "limit" (pending tại giá entry)


def calc_lot_size(balance: float, risk_pct: float, entry: float, sl: float,
                  contract_size: float = 100.0, volume_min: float = 0.01,
                  volume_step: float = 0.01, volume_max: float = 100.0) -> float:
    """
    Với XAUUSD chuẩn: 1 lot = 100 oz → 1 USD di chuyển giá = $100/lot.
    lot = (balance * risk%) / (khoảng SL tính bằng USD * contract_size)
    """
    risk_amount = balance * risk_pct / 100.0
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return 0.0
    raw_lot = risk_amount / (sl_distance * contract_size)
    # Làm tròn xuống theo volume_step để không vượt rủi ro cho phép
    stepped = (raw_lot // volume_step) * volume_step
    return round(max(min(stepped, volume_max), 0.0), 2) if stepped >= volume_min else 0.0


def validate_rr(entry: float, sl: float, tp: float, min_rr: float) -> tuple[bool, float]:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return False, 0.0
    rr = reward / risk
    return rr >= min_rr, round(rr, 2)


# ═════════════════════════════════════════════════════════
#  ENGINE QUẢN LÝ LỆNH DÙNG CHUNG (unified management)
#  Một nguồn sự thật duy nhất cho breakeven + partial + SL/TP:
#   - manage_step(): dùng cho tiêu thụ theo NẾN (backtest + paper dry_run).
#   - manage_tick(): dùng cho LIVE (chỉ BE + partial; SL/TP nằm ở broker).
#  Nhờ vậy backtest ≡ paper ≡ live, không còn 3 mô hình quản lý lệch nhau.
# ═════════════════════════════════════════════════════════
@dataclass
class PositionState:
    """Trạng thái sống của MỘT vị thế trong lúc quản lý."""
    direction: str            # "buy" | "sell"
    entry: float
    sl: float                 # SL hiện tại (có thể đã dời về BE)
    original_sl: float        # SL gốc — mẫu số để tính R (không đổi sau BE)
    tp: float
    lot: float                # lot CÒN LẠI (giảm sau partial)
    cs: float                 # contract_size (100 vàng / 100_000 forex)
    entry_time: object = None
    reason: str = ""
    rr: float = 0.0
    be_done: bool = False
    partial_done: bool = False


def position_pnl(state: PositionState, exit_price: float, lot: float = None) -> float:
    lot = state.lot if lot is None else lot
    mult = 1 if state.direction == "buy" else -1
    return (exit_price - state.entry) * mult * lot * state.cs


def current_r(state: PositionState, price: float) -> float:
    """R hiện tại, đo theo RỦI RO GỐC (original_sl) — không đổi sau khi dời BE."""
    risk = abs(state.entry - state.original_sl)
    if risk <= 0:
        return 0.0
    mult = 1 if state.direction == "buy" else -1
    return (price - state.entry) * mult / risk


def breakeven_level(state: PositionState, cfg) -> float:
    """#3 — Dời SL về mức HÒA VỐN THẬT (bù chi phí round-trip), KHÔNG phải đúng entry.
    Đặt SL đúng entry khiến mọi lần chạm BE là một khoản LỖ bằng spread. Ở đây bù
    spread round-trip + commission (quy ra GIÁ) để chạm BE là net ≥ 0."""
    cost_price = cfg.spread_points + (cfg.commission_per_lot / state.cs if state.cs else 0.0)
    return state.entry + cost_price if state.direction == "buy" else state.entry - cost_price


def _nights(entry_time, exit_time) -> int:
    """Số đêm giữ lệnh (số lần bắc qua rollover 00:00 giờ server).
    Rollover thứ Tư tính x3 (triple swap bù cuối tuần) → mỗi T4 cộng thêm 2 đêm."""
    e0 = pd.Timestamp(entry_time).normalize()
    e1 = pd.Timestamp(exit_time).normalize()
    n = (e1 - e0).days
    if n <= 0:
        return 0
    cur = e0
    for _ in range(n):
        cur += pd.Timedelta(days=1)
        if cur.weekday() == 2:   # bắc cầu sang thứ Tư → triple swap
            n += 2
    return n


def trade_cost(state: PositionState, exit_time, lot: float, cfg) -> float:
    """Tổng chi phí (USD, dương = trừ vào PnL) cho một phần lot khớp lệnh.
    Spread round-trip (vào+ra); commission round-turn; swap qua đêm.
    Cộng dồn qua các partial sẽ phủ đúng chi phí round-trip của toàn vị thế."""
    spread_cost = cfg.spread_points * lot * state.cs
    commission = cfg.commission_per_lot * lot
    swap_rate = cfg.swap_long_per_lot if state.direction == "buy" else cfg.swap_short_per_lot
    swap_adj = swap_rate * lot * _nights(state.entry_time, exit_time)  # ký hiệu: âm = chi phí
    return spread_cost + commission - swap_adj


def _exit_row(state: PositionState, exit_price, exit_time, pnl, cost, result, lot=None) -> dict:
    """Dòng nhật ký một lần khớp thoát — giữ nguyên schema cũ để _report/CSV không đổi."""
    return {
        "direction": state.direction, "entry": state.entry,
        "sl": state.sl, "sl_orig": state.original_sl, "tp": state.tp,
        "lot": state.lot if lot is None else lot, "rr": state.rr,
        "entry_time": state.entry_time, "reason": state.reason,
        "exit": exit_price, "exit_time": exit_time, "pnl": pnl,
        "cost": cost, "result": result,
    }


def manage_step(state: PositionState, open_: float, high: float, low: float, close: float,
                now, cfg, apply_costs: bool = True) -> tuple[float, list[dict], bool]:
    """Quản lý theo NẾN (backtest + paper). Trả về (balance_delta, rows, closed).
    Thứ tự bảo thủ trong 1 nến: SL → TP → (BE/partial tại close).
    GAP: nếu nến MỞ đã vượt qua SL (gap cuối tuần/tin), khớp SL tại giá open — lỗ thật
    lớn hơn 1R, không phải tại mức SL. Gap qua TP thì vẫn khớp tại TP (không thưởng)."""
    is_buy = state.direction == "buy"
    hit_sl = low <= state.sl if is_buy else high >= state.sl
    hit_tp = high >= state.tp if is_buy else low <= state.tp

    if hit_sl:
        gapped = open_ <= state.sl if is_buy else open_ >= state.sl
        fill = open_ if gapped else state.sl
        cost = trade_cost(state, now, state.lot, cfg) if apply_costs else 0.0
        pnl = position_pnl(state, fill) - cost
        return pnl, [_exit_row(state, fill, now, pnl, cost, "SL-GAP" if gapped else "SL/BE")], True
    if hit_tp:
        cost = trade_cost(state, now, state.lot, cfg) if apply_costs else 0.0
        pnl = position_pnl(state, state.tp) - cost
        return pnl, [_exit_row(state, state.tp, now, pnl, cost, "TP")], True

    balance_delta = 0.0
    rows: list[dict] = []
    r = current_r(state, close)
    if not state.partial_done and r >= cfg.partial_close_at_rr:
        part_lot = state.lot * cfg.partial_close_pct / 100
        cost = trade_cost(state, now, part_lot, cfg) if apply_costs else 0.0
        pnl = position_pnl(state, close, part_lot) - cost
        balance_delta += pnl
        state.lot -= part_lot
        state.partial_done = True
        rows.append(_exit_row(state, close, now, pnl, cost, "PARTIAL", lot=part_lot))
    if not state.be_done and r >= cfg.move_sl_to_be_at_rr:
        state.sl = breakeven_level(state, cfg) if apply_costs else state.entry
        state.be_done = True
    return balance_delta, rows, False


def manage_tick(state: PositionState, price: float, cfg) -> dict:
    """Quản lý theo TICK (live). SL/TP đã đặt ở broker → chỉ quyết định BE + partial.
    Trả về hành động; CALLER thực thi qua MT5 rồi mới cập nhật cờ trạng thái.
    Dùng CHUNG ngưỡng/công thức với manage_step nên live ≡ backtest."""
    r = current_r(state, price)
    out = {"partial_lot": None, "move_sl_to": None}
    if not state.partial_done and r >= cfg.partial_close_at_rr:
        out["partial_lot"] = round(state.lot * cfg.partial_close_pct / 100, 2)
    if not state.be_done and r >= cfg.move_sl_to_be_at_rr:
        out["move_sl_to"] = breakeven_level(state, cfg)
    return out


class RiskGuard:
    """Theo dõi giới hạn rủi ro trong ngày và tổng heat của tài khoản."""

    def __init__(self, max_daily_loss_pct: float, portfolio_heat_pct: float):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.portfolio_heat_pct = portfolio_heat_pct
        self.day_start_balance: float | None = None
        self.current_day = None

    def update_day(self, today, balance: float):
        if self.current_day != today:
            self.current_day = today
            self.day_start_balance = balance

    def daily_loss_exceeded(self, balance: float) -> bool:
        if not self.day_start_balance:
            return False
        loss_pct = (self.day_start_balance - balance) / self.day_start_balance * 100
        return loss_pct >= self.max_daily_loss_pct

    def heat_exceeded(self, open_risk_pct: float, new_risk_pct: float) -> bool:
        return (open_risk_pct + new_risk_pct) > self.portfolio_heat_pct
