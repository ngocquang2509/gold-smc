# Luồng Scalping M5 độc lập — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independent, single-timeframe (M5) scalping trading stream — EMA trend-pullback + RSI momentum + ATR-based SL/TP — usable on XAUUSDm/EURUSDm/GBPUSDm, running as a separate process from the existing SMC bot with zero shared config/risk/journal state.

**Architecture:** 4 new files mirror the existing `config.py`/`strategy.py`/`backtest.py`/`main.py` split: `scalp_config.py` (own dataclass schema, 3 symbol instances), `scalp_strategy.py` (pure `analyze_scalp()` function, no HTF), `scalp_backtest.py` (bar-by-bar M5 backtest, own SL/TP-only exit engine — does NOT call `risk.manage_step`), `scalp_main.py` (live/demo loop, market-order entry, no breakeven/partial management needed since SL/TP are fixed at the broker). Reuses `risk.py`'s `calc_lot_size`/`validate_rr`/`trade_cost`/`PositionState`/`TradePlan`/`RiskGuard`, `journal.py`'s `TradeJournal` (with an override path), `mt5_client.py`'s `MT5Client` as-is, and `notifier.py`'s `TelegramNotifier` unchanged.

**Tech Stack:** Python, pandas (EMA/RSI/ATR computed manually via `.ewm()` — no new dependency), existing `MetaTrader5` wrapper.

**No test suite in this repo** (see `CLAUDE.md`: "There is no test suite, linter, or build step. Backtesting IS the validation workflow"). This plan replaces pytest-style "write failing test → implement → pass" steps with the project's actual convention: small throwaway sanity scripts (run via `python -c` or a scratch `.py` file, NOT committed) to check pure functions against synthetic data, followed by a real backtest run as the final validation gate for each runnable component — exactly how `smc/*` and `strategy.py` were validated historically per `docs/superpowers/specs/2026-07-28-*.md`.

**Spec:** `docs/superpowers/specs/2026-07-31-scalp-m5-design.md` — read it first; this plan implements it verbatim, including the fixes from spec review (notifier dependency, custom `_manage_scalp_step` instead of `manage_step`, EMA-tie guard, warmup guard, ATR>0 guard).

---

### Task 1: `scalp_config.py` — config schema + per-symbol instances

**Files:**
- Create: `d:\gold-smc-bot\scalp_config.py`

- [ ] **Step 1: Write the file**

```python
"""
Cấu hình cho luồng SCALPING M5 độc lập — TÁCH BIỆT HOÀN TOÀN với TradingConfig/config.py
(bot SMC đa khung H4→M15). Không import/dùng chung tham số nào với config.py.

Mỗi symbol có 1 instance riêng, chọn bằng get_scalp_config(name).
Xem docs/superpowers/specs/2026-07-31-scalp-m5-design.md.
"""
from dataclasses import dataclass, field, replace


@dataclass
class ScalpConfig:
    # ── Symbol & khung ────────────────────────────────────
    symbol: str = "XAUUSDm"
    timeframe: str = "M5"
    bars: int = 300              # số nến M5 tải mỗi lần phân tích (đủ warmup EMA50/ATR14/RSI14)

    # ── Trend + entry ─────────────────────────────────────
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    rsi_buy_min: float = 45.0    # dải RSI hợp lệ cho lệnh BUY
    rsi_buy_max: float = 70.0
    rsi_sell_min: float = 30.0   # dải RSI hợp lệ cho lệnh SELL
    rsi_sell_max: float = 55.0

    # ── Volatility filter + SL/TP ─────────────────────────
    atr_period: int = 14
    min_atr_points: float = 0.5  # sàn ATR tối thiểu (đơn vị giá symbol) — điểm khởi đầu,
    #   đo lại chính xác hơn sau lượt backtest đầu tiên (xem Task 5). KHÔNG bao giờ để 0.0
    #   ở live/demo — 0.0 tắt hẳn bộ lọc biến động.
    sl_atr_mult: float = 1.2
    tp_atr_mult: float = 1.8     # R:R thiết kế ~1.5
    min_rr: float = 1.3

    # ── Chống vào lệnh trùng lặp / tần suất ───────────────
    cooldown_bars: int = 3       # nến M5 nghỉ sau khi 1 lệnh đóng (~15 phút), direction-agnostic
    max_trades_per_day: int = 15
    max_open_positions: int = 1

    # ── Risk (TÁCH BIỆT hoàn toàn với bot SMC dù cùng tài khoản) ──
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 3.0
    portfolio_heat_pct: float = 3.0   # không có tác dụng thực tế khi max_open_positions=1

    # ── Session (giờ server MT5) ──────────────────────────
    use_session_filter: bool = True
    sessions: list = field(default_factory=lambda: [("06:00", "19:00")])

    # ── Chi phí giao dịch (field name PHẢI khớp TradingConfig để risk.trade_cost() dùng được) ──
    spread_points: float = 0.28
    commission_per_lot: float = 0.0
    swap_long_per_lot: float = -49.04
    swap_short_per_lot: float = 0.0

    # ── Telegram (tái dùng notifier.py — reconcile_journal() cần notifier) ──
    telegram_enabled: bool = True
    telegram_token: str = ""      # trống = tắt (TelegramNotifier.from_config yêu cầu cả token+chat_id)
    telegram_chat_id: str = ""

    # ── Execution ──────────────────────────────────────────
    magic_number: int = 20260731  # magic RIÊNG cho luồng scalp, khác 3 magic của bot SMC
    deviation: int = 20
    poll_seconds: int = 10        # M5 → poll nhanh hơn SMC, cần bắt kịp nến mới đóng
    dry_run: bool = True


# ═════════════════════════════════════════════════════════
#  Instance riêng theo symbol — chi phí (spread/swap) copy từ config.py (đã đo thật
#  MT5 Exness), min_atr_points là ƯỚC LƯỢNG BAN ĐẦU, tune lại sau Task 5.
# ═════════════════════════════════════════════════════════
XAUUSD_SCALP = ScalpConfig(
    symbol="XAUUSDm",
    magic_number=20260731,
    min_atr_points=0.5,           # ATR M5 vàng thường vài chục cent → 0.5 là sàn thận trọng
    spread_points=0.28,
    commission_per_lot=0.0,
    swap_long_per_lot=-49.04,
    swap_short_per_lot=0.0,
)

EURUSD_SCALP = ScalpConfig(
    symbol="EURUSDm",
    magic_number=20260732,
    min_atr_points=0.00015,       # ~1.5 pip
    sessions=[("10:00", "20:00")],
    spread_points=0.00008,
    commission_per_lot=0.0,
    swap_long_per_lot=-6.0,
    swap_short_per_lot=0.0,
)

GBPUSD_SCALP = ScalpConfig(
    symbol="GBPUSDm",
    magic_number=20260733,
    min_atr_points=0.00018,       # ~1.8 pip (GBPUSD biến động hơn EURUSD)
    spread_points=0.00010,
    commission_per_lot=0.0,
    swap_long_per_lot=-1.5,
    swap_short_per_lot=-1.1,
)

SCALP_CONFIGS = {
    "XAUUSDm": XAUUSD_SCALP,
    "EURUSDm": EURUSD_SCALP,
    "GBPUSDm": GBPUSD_SCALP,
}
_ALIASES = {
    "gold": "XAUUSDm", "xau": "XAUUSDm", "xauusd": "XAUUSDm", "vang": "XAUUSDm",
    "eur": "EURUSDm", "eurusd": "EURUSDm",
    "gbp": "GBPUSDm", "gbpusd": "GBPUSDm",
}


def get_scalp_config(name: str) -> "ScalpConfig":
    """Trả về config của symbol (hoặc alias). Trả về BẢN SAO để override runtime không rò rỉ."""
    key = _ALIASES.get(name.lower(), name)
    if key not in SCALP_CONFIGS:
        raise SystemExit(
            f"Symbol '{name}' chưa có scalp config. Hỗ trợ: {list(SCALP_CONFIGS)} "
            f"(alias: {list(_ALIASES)})"
        )
    return replace(SCALP_CONFIGS[key])
```

- [ ] **Step 2: Sanity-check it imports and resolves aliases correctly**

Run:
```bash
python -c "from scalp_config import get_scalp_config; c = get_scalp_config('gold'); print(c.symbol, c.magic_number, c.risk_per_trade_pct); e = get_scalp_config('EURUSDm'); print(e.symbol, e.magic_number, e.sessions)"
```
Expected output: `XAUUSDm 20260731 0.5` then `EURUSDm 20260732 [('10:00', '20:00')]`

- [ ] **Step 3: Commit**

```bash
git add scalp_config.py
git commit -m "Add ScalpConfig schema + per-symbol instances for M5 scalping stream"
```

---

### Task 2: `scalp_strategy.py` — indicator helpers (EMA/RSI/ATR)

**Files:**
- Create: `d:\gold-smc-bot\scalp_strategy.py`

- [ ] **Step 1: Write the indicator helpers**

```python
"""
Chiến lược SCALPING M5 độc lập — 1 khung, không HTF, không sweep/CHoCH/OB/FVG.
EMA trend-pullback + RSI momentum + ATR volatility/SL-TP. Xem
docs/superpowers/specs/2026-07-31-scalp-m5-design.md cho spec đầy đủ.
"""
import pandas as pd
from risk import TradePlan, calc_lot_size, validate_rr


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI chuẩn (Wilder smoothing qua ewm alpha=1/period)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(100)   # avg_loss kéo dài về 0 (toàn nến tăng) → RSI=100 (quá mua)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()
```

- [ ] **Step 2: Sanity-check the indicators against known values**

Run this scratch script (temp file, NOT committed — e.g. save to the scratchpad dir, or just paste into `python -c` with a heredoc):

```python
import pandas as pd
from scalp_strategy import _ema, _rsi, _atr

# Nến tăng đều — RSI phải tiến gần 100, ATR phải > 0 và ổn định.
df = pd.DataFrame({
    "open":  [100 + i for i in range(30)],
    "high":  [100.5 + i for i in range(30)],
    "low":   [99.5 + i for i in range(30)],
    "close": [100.3 + i for i in range(30)],
})
rsi = _rsi(df["close"], 14)
atr = _atr(df, 14)
ema20 = _ema(df["close"], 20)
assert rsi.iloc[-1] > 90, f"RSI kỳ vọng >90 khi tăng đều, được {rsi.iloc[-1]}"
assert atr.iloc[-1] > 0, "ATR phải > 0 khi có biên độ nến"
assert ema20.iloc[-1] < df["close"].iloc[-1], "EMA phải bám dưới giá trong xu hướng tăng đều"
print("OK — indicators sanity check passed")
```

Expected: `OK — indicators sanity check passed` with no assertion error.

- [ ] **Step 3: Commit**

```bash
git add scalp_strategy.py
git commit -m "Add EMA/RSI/ATR indicator helpers for scalp strategy"
```

---

### Task 3: `scalp_strategy.py` — `analyze_scalp()` entry logic

**Files:**
- Modify: `d:\gold-smc-bot\scalp_strategy.py` (append to file from Task 2)

- [ ] **Step 1: Append `analyze_scalp()`**

```python
def analyze_scalp(df: pd.DataFrame, cfg, balance: float, symbol_info: dict) -> TradePlan | None:
    """Phân tích 1 khung M5 độc lập. `df` PHẢI đã bỏ nến đang chạy — caller truyền
    df.iloc[:-1] (bất biến no-repaint, giống strategy.analyze của bot SMC)."""
    min_bars = max(cfg.ema_slow, cfg.atr_period, cfg.rsi_period) + 1
    if len(df) < min_bars:
        return None

    ema_fast = _ema(df["close"], cfg.ema_fast)
    ema_slow = _ema(df["close"], cfg.ema_slow)
    rsi = _rsi(df["close"], cfg.rsi_period)
    atr = _atr(df, cfg.atr_period)

    ef, es = ema_fast.iloc[-1], ema_slow.iloc[-1]
    if ef == es:
        return None   # tie hiếm gặp — không coi là up cũng không down
    trend = "up" if ef > es else "down"

    a = atr.iloc[-1]
    if not (a > 0) or a < cfg.min_atr_points:
        return None   # thị trường quá lặng, không đủ biên độ cho SL/TP theo ATR

    last = df.iloc[-1]
    r = rsi.iloc[-1]

    if trend == "up":
        pullback = last["low"] <= ef and last["close"] > last["open"] and last["close"] > ef
        rsi_ok = cfg.rsi_buy_min <= r <= cfg.rsi_buy_max
        direction = "buy"
    else:
        pullback = last["high"] >= ef and last["close"] < last["open"] and last["close"] < ef
        rsi_ok = cfg.rsi_sell_min <= r <= cfg.rsi_sell_max
        direction = "sell"

    if not (pullback and rsi_ok):
        return None

    entry = last["close"]
    if direction == "buy":
        sl = entry - cfg.sl_atr_mult * a
        tp = entry + cfg.tp_atr_mult * a
    else:
        sl = entry + cfg.sl_atr_mult * a
        tp = entry - cfg.tp_atr_mult * a

    digits = symbol_info["digits"]
    entry, sl, tp = round(entry, digits), round(sl, digits), round(tp, digits)

    ok, rr = validate_rr(entry, sl, tp, cfg.min_rr)
    if not ok:
        return None

    lot = calc_lot_size(balance, cfg.risk_per_trade_pct, entry, sl,
                        symbol_info["contract_size"], symbol_info["volume_min"],
                        symbol_info["volume_step"], symbol_info["volume_max"])
    if lot <= 0:
        return None

    risk_amount = round(balance * cfg.risk_per_trade_pct / 100.0, 2)
    reason = f"EMA{cfg.ema_fast}/{cfg.ema_slow} {trend} pullback + RSI {r:.1f}"
    return TradePlan(direction=direction, entry=entry, sl=sl, tp=tp, lot=lot, rr=rr,
                     risk_amount=risk_amount, reason=reason, sweep_level=None,
                     order_kind="market")
```

- [ ] **Step 2: Sanity-check against synthetic data covering the guard cases**

Scratch script (not committed). **Important**: a plain monotonically-increasing price
ramp is NOT a valid test fixture here — Wilder RSI saturates to 100 on a ramp with zero
down-candles, which fails the `rsi_buy_max=70.0` gate even though the implementation is
correct (this exact mistake was caught in plan review — don't reintroduce it). The
fixture below uses an oscillating-but-net-upward warmup series (so RSI cools into the
band) followed by one explicitly crafted green pullback bar (low dips to touch EMA20,
opens near that low, closes back above EMA20) — verified by hand to produce RSI≈69.3,
inside `[45, 70]`:

```python
import math
import pandas as pd
from dataclasses import replace
from scalp_config import get_scalp_config
from scalp_strategy import _ema, analyze_scalp

cfg = get_scalp_config("XAUUSDm")
symbol_info = {"contract_size": 100.0, "volume_min": 0.01, "volume_step": 0.01,
              "volume_max": 100.0, "point": 0.01, "digits": 2}

def make_uptrend_df():
    # 54 nến dao động NHƯNG có drift tăng ròng — giữ RSI không bão hòa ở 100.
    rows = []
    for i in range(54):
        osc = math.sin(i / 2.5) * 1.0
        base = 2000.0 + i * 0.12 + osc
        prev_close = rows[-1]["close"] if rows else base
        open_p, close_p = prev_close, base
        rows.append({"open": open_p, "high": max(open_p, close_p) + 0.15,
                     "low": min(open_p, close_p) - 0.15, "close": close_p})
    df = pd.DataFrame(rows)
    # Nến trigger: craft riêng dựa trên EMA20 hiện tại — chạm đáy rồi bật xanh mạnh.
    ef = _ema(df["close"], cfg.ema_fast).iloc[-1]
    low_p = ef - 0.5
    open_p = low_p + 0.1
    close_p = ef + 1.0
    trigger = {"open": open_p, "high": close_p + 0.2, "low": low_p, "close": close_p}
    return pd.concat([df, pd.DataFrame([trigger])], ignore_index=True)

df = make_uptrend_df()
plan = analyze_scalp(df, cfg, balance=10_000, symbol_info=symbol_info)
print("uptrend+pullback plan:", plan)
assert plan is not None and plan.direction == "buy", "Kỳ vọng có tín hiệu BUY"

# Warmup guard: quá ít nến
short_df = df.iloc[-5:]
assert analyze_scalp(short_df, cfg, 10_000, symbol_info) is None, "Kỳ vọng None khi thiếu nến warmup"

# ATR gate: min_atr_points cực cao → luôn bị chặn. Dùng replace() để KHÔNG mutate `cfg`
# gốc (mutate alias trực tiếp là bẫy copy-paste — bản sao độc lập cho từng assertion).
high_floor_cfg = replace(cfg, min_atr_points=999.0)
assert analyze_scalp(df, high_floor_cfg, 10_000, symbol_info) is None, "Kỳ vọng None khi ATR floor quá cao"

print("OK — analyze_scalp sanity checks passed")
```

Expected: prints a `TradePlan(direction='buy', ..., rr=1.48, ...)` (rr≈1.5 by
construction — sl_atr_mult=1.2/tp_atr_mult=1.8 — exact value depends on rounding), then
`OK — analyze_scalp sanity checks passed` with no assertion error. This exact fixture
was verified end-to-end against the real `risk.py` before being written into this plan.

- [ ] **Step 3: Commit**

```bash
git add scalp_strategy.py
git commit -m "Add analyze_scalp() entry logic: EMA pullback + RSI + ATR SL/TP"
```

---

### Task 4: `scalp_backtest.py` — bar-by-bar M5 backtest

**Files:**
- Create: `d:\gold-smc-bot\scalp_backtest.py`

**Design note (resolves the spec-review ambiguity on fill timing):** the signal is computed
on the slice ending at bar `i` (already closed). If `analyze_scalp` returns a plan, the
trade fills immediately at that bar's close price (`plan.entry`), and SL/TP monitoring
starts from bar `i+1` onward — i.e. the trigger bar itself cannot also be the bar that
hits SL/TP. This mirrors "vào market ngay khi nến trigger đóng" from the spec.

- [ ] **Step 1: Write the file**

```python
"""
Backtest cho luồng SCALPING M5 độc lập (bar-by-bar, single-timeframe, không HTF).

Dùng:
    python scalp_backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
    python scalp_backtest.py --csv-m5 data/xauusd_m5.csv --symbol XAUUSDm

CSV cần cột: time,open,high,low,close (time ISO hoặc epoch giây).
"""
import argparse
import logging
import sys
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from scalp_config import get_scalp_config
from scalp_strategy import analyze_scalp
from risk import PositionState, RiskGuard, position_pnl, trade_cost

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scalp_backtest")

SYMBOL_INFO = {
    "XAUUSDm": {"contract_size": 100.0, "volume_min": 0.01, "volume_step": 0.01,
                "volume_max": 100.0, "point": 0.01, "digits": 2},
    "EURUSDm": {"contract_size": 100_000.0, "volume_min": 0.01, "volume_step": 0.01,
                "volume_max": 200.0, "point": 1e-05, "digits": 5},
    "GBPUSDm": {"contract_size": 100_000.0, "volume_min": 0.01, "volume_step": 0.01,
                "volume_max": 200.0, "point": 1e-05, "digits": 5},
}


def in_session(ts, cfg) -> bool:
    if not cfg.use_session_filter:
        return True
    t = ts.strftime("%H:%M")
    return any(start <= t <= end for start, end in cfg.sessions)


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    if df["time"].dtype != "O":
        df["time"] = pd.to_datetime(df["time"], unit="s")
    else:
        df["time"] = pd.to_datetime(df["time"])
    df.set_index("time", inplace=True)
    return df[["open", "high", "low", "close"]]


def load_from_mt5(cfg, years: float = 2.0):
    from mt5_client import MT5Client, TIMEFRAME_MAP, mt5
    from datetime import datetime, timedelta
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        raise SystemExit("Không kết nối được MT5.")
    end = datetime.now()
    start = end - timedelta(days=int(365 * years) + 10)
    rates = mt5.copy_rates_range(cfg.symbol, TIMEFRAME_MAP[cfg.timeframe], start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"Không lấy được {cfg.timeframe}: {mt5.last_error()}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    symbol_info = client.get_symbol_info()
    client.shutdown()
    log.info(f"Tải {len(df)} nến {cfg.timeframe} ({start.date()} → {end.date()}) | "
             f"symbol_info={symbol_info}")
    return df[["open", "high", "low", "close"]], symbol_info


def _manage_scalp_step(state: PositionState, high, low, now, cfg, apply_costs=True):
    """SL/TP-hit ONLY — KHÔNG BE/partial. Không dùng risk.manage_step (nó đọc
    cfg.partial_close_at_rr/move_sl_to_be_at_rr mà ScalpConfig không có)."""
    is_buy = state.direction == "buy"
    hit_sl = low <= state.sl if is_buy else high >= state.sl
    hit_tp = high >= state.tp if is_buy else low <= state.tp
    if not (hit_sl or hit_tp):
        return 0.0, None, False
    exit_price = state.sl if hit_sl else state.tp
    result = "SL" if hit_sl else "TP"
    cost = trade_cost(state, now, state.lot, cfg) if apply_costs else 0.0
    pnl = position_pnl(state, exit_price) - cost
    row = {"direction": state.direction, "entry": state.entry, "sl": state.sl, "tp": state.tp,
          "lot": state.lot, "rr": state.rr, "entry_time": state.entry_time,
          "reason": state.reason, "exit": exit_price, "exit_time": now, "pnl": pnl,
          "cost": cost, "result": result}
    return pnl, row, True


def run_backtest(m5: pd.DataFrame, cfg, initial_balance: float = 10_000.0,
                 symbol_info: dict = None, quiet: bool = False, apply_costs: bool = True):
    symbol_info = symbol_info or SYMBOL_INFO["XAUUSDm"]
    balance = initial_balance
    equity_curve = []
    trades = []
    open_trade = None
    last_exit_i = -10 ** 9
    trades_today = 0
    trade_day = None
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    warmup = max(cfg.ema_slow, cfg.atr_period, cfg.rsi_period) + 1

    for i in range(warmup, len(m5)):
        now = m5.index[i]
        bar = m5.iloc[i]
        guard.update_day(now.date(), balance)

        if open_trade:
            delta, row, closed = _manage_scalp_step(open_trade, bar["high"], bar["low"],
                                                     now, cfg, apply_costs)
            balance += delta
            if row:
                trades.append(row)
            if closed:
                open_trade = None
                last_exit_i = i

        equity_curve.append({"time": now, "balance": balance})

        if open_trade or guard.daily_loss_exceeded(balance):
            continue
        if now.date() != trade_day:
            trade_day = now.date()
            trades_today = 0
        if not in_session(now, cfg):
            continue
        if i - last_exit_i < cfg.cooldown_bars:
            continue
        if trades_today >= cfg.max_trades_per_day:
            continue

        m5_slice = m5.iloc[max(0, i + 1 - cfg.bars): i + 1]
        plan = analyze_scalp(m5_slice, cfg, balance, symbol_info)
        if plan:
            trades_today += 1
            open_trade = PositionState(
                direction=plan.direction, entry=plan.entry, sl=plan.sl, original_sl=plan.sl,
                tp=plan.tp, lot=plan.lot, cs=symbol_info["contract_size"],
                entry_time=now, reason=plan.reason, rr=plan.rr,
            )
            if not quiet:
                log.info(f"{now} 🎯 {plan.direction.upper()} @ {plan.entry} SL {plan.sl} "
                         f"TP {plan.tp} lot {plan.lot} RR {plan.rr} | {plan.reason}")

    metrics = _report(trades, equity_curve, initial_balance, balance, quiet=quiet)
    return trades, equity_curve, metrics


def _report(trades, equity, start_bal, end_bal, quiet: bool = False):
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0.01]
    losses = [t for t in trades if t["pnl"] < -0.01]
    wr = len(wins) / n * 100 if n else 0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    total_pnl = sum(t["pnl"] for t in trades)
    total_cost = sum(t.get("cost", 0.0) for t in trades)
    peak, max_dd = start_bal, 0
    for pt in equity:
        peak = max(peak, pt["balance"])
        max_dd = max(max_dd, (peak - pt["balance"]) / peak * 100)
    ret_pct = (end_bal / start_bal - 1) * 100
    cagr = 0.0
    if equity:
        days = (pd.to_datetime(equity[-1]["time"]) - pd.to_datetime(equity[0]["time"])).days
        yrs = days / 365.25
        if yrs > 0:
            cagr = ((end_bal / start_bal) ** (1 / yrs) - 1) * 100
    metrics = {"n": n, "wr": wr, "pf": pf, "total_pnl": total_pnl, "ret_pct": ret_pct,
              "cagr": cagr, "max_dd": max_dd, "total_cost": total_cost}
    if quiet:
        return metrics
    print("\n" + "=" * 50)
    print("KẾT QUẢ SCALP BACKTEST (M5)")
    print("=" * 50)
    print(f"Số lệnh                : {n}")
    print(f"Winrate                : {wr:.1f}%")
    print(f"Profit factor          : {pf:.2f}")
    print(f"Tổng PnL (ròng)        : {total_pnl:+,.2f} USD")
    print(f"Tổng chi phí           : -{total_cost:,.2f} USD")
    print(f"Balance                : {start_bal:,.0f} → {end_bal:,.2f}  ({ret_pct:+.1f}%)")
    print(f"CAGR                   : {cagr:+.1f}%")
    print(f"Max drawdown           : {max_dd:.2f}%")
    print("=" * 50)
    pd.DataFrame(trades).to_csv("scalp_backtest_trades.csv", index=False)
    pd.DataFrame(equity).to_csv("scalp_backtest_equity.csv", index=False)
    print("Đã lưu scalp_backtest_trades.csv & scalp_backtest_equity.csv")
    return metrics


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--csv-m5")
    p.add_argument("--from-mt5", action="store_true")
    p.add_argument("--symbol", default="XAUUSDm")
    p.add_argument("--years", type=float, default=2.0)
    p.add_argument("--balance", type=float, default=10_000)
    p.add_argument("--no-costs", action="store_true")
    args = p.parse_args()

    cfg = get_scalp_config(args.symbol)
    log.info(f"ScalpConfig {cfg.symbol}: EMA{cfg.ema_fast}/{cfg.ema_slow} RSI{cfg.rsi_period} "
             f"ATR{cfg.atr_period} sl_mult={cfg.sl_atr_mult} tp_mult={cfg.tp_atr_mult} "
             f"risk={cfg.risk_per_trade_pct}%")

    symbol_info = None
    if args.from_mt5:
        m5, symbol_info = load_from_mt5(cfg, args.years)
    elif args.csv_m5:
        m5 = load_csv(args.csv_m5)
        symbol_info = SYMBOL_INFO.get(cfg.symbol)
    else:
        raise SystemExit("Cần --from-mt5 hoặc --csv-m5.")

    run_backtest(m5, cfg, args.balance, symbol_info, apply_costs=not args.no_costs)
```

- [ ] **Step 2: Verify the module at least imports and parses args cleanly**

Run: `python scalp_backtest.py --help`
Expected: argparse help text listing `--csv-m5`, `--from-mt5`, `--symbol`, `--years`, `--balance`, `--no-costs` with no import errors.

- [ ] **Step 3: Commit**

```bash
git add scalp_backtest.py
git commit -m "Add scalp_backtest.py: bar-by-bar M5 backtest engine"
```

---

### Task 5: Backtest smoke run — validate the strategy produces sane output

This is the real validation gate (per project convention: "Backtesting IS the validation
workflow"). Not a code-writing task — run the backtest against real MT5 history for all
3 symbols and sanity-check the output before moving on to the live-loop code.

- [ ] **Step 1: Run for XAUUSDm**

```bash
python scalp_backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
```
Expected: prints "KẾT QUẢ SCALP BACKTEST (M5)" with `n` (number of trades) clearly
higher than the SMC bot's swing-trade count (expect dozens-to-low-hundreds over 2 years,
not thousands — if `n` is 0, the EMA/RSI/ATR gates are too strict or `min_atr_points` is
miscalibrated; if `n` is in the many thousands, a guard is likely broken, e.g. the
warmup/EMA-tie/ATR checks aren't actually filtering).

- [ ] **Step 2: Run for EURUSDm and GBPUSDm**

```bash
python scalp_backtest.py --from-mt5 --symbol EURUSDm --years 2 --balance 10000
python scalp_backtest.py --from-mt5 --symbol GBPUSDm --years 2 --balance 10000
```
Same sanity checks as Step 1.

- [ ] **Step 3: Record findings**

Note the resulting PF/winrate/CAGR/max-DD and current `min_atr_points`/`sl_atr_mult`/
`tp_atr_mult` values for each symbol as a comment block at the top of `scalp_config.py`
(matching how `config.py` documents tuning history for the SMC bot). This is NOT a
tuning pass — just recording the untouched baseline. Deeper parameter tuning is
explicitly out of scope for this plan (see spec's non-goals) and should be done as a
separate follow-up, iterating on `scalp_config.py` + re-running `scalp_backtest.py`.

- [ ] **Step 4: Commit the baseline notes**

```bash
git add scalp_config.py
git commit -m "Record scalp M5 baseline backtest results for XAUUSDm/EURUSDm/GBPUSDm"
```

---

### Task 6: `scalp_main.py` — live/demo loop

**Files:**
- Create: `d:\gold-smc-bot\scalp_main.py`

- [ ] **Step 1: Write the file**

```python
"""
Live/demo loop cho luồng SCALPING M5 độc lập — tiến trình RIÊNG với main.py (bot SMC).
Không dùng chung magic_number/journal/risk với bot SMC. Khớp MARKET ngay khi có tín
hiệu, SL/TP đặt cứng ở broker — KHÔNG cần manage_open_positions (không BE/partial).

Chạy 1 symbol:  python scalp_main.py --symbol XAUUSDm
Chạy nhiều:     python scalp_main.py --symbols EURUSDm,GBPUSDm
Mặc định dry_run=True trong scalp_config.py — kiểm tra kỹ trước khi tắt.

LƯU Ý VẬN HÀNH (xem spec, mục "Rủi ro cần xác minh"): chạy song song với main.py nhắm
CÙNG 1 terminal MT5 thường an toàn (mỗi process có kênh IPC riêng) nhưng NÊN xác minh
bằng 1 lượt demo ngắn trước khi tin tưởng ở live — và market_order() ở mt5_client.py
chưa từng được gọi thật trong codebase này trước luồng này.
"""
import time
import logging
import argparse
from dataclasses import dataclass
from datetime import date
import pandas as pd
from scalp_config import get_scalp_config, ScalpConfig
from scalp_strategy import analyze_scalp
from mt5_client import MT5Client
from journal import TradeJournal
from notifier import TelegramNotifier
from risk import PositionState, RiskGuard, position_pnl, trade_cost

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler("scalp_bot.log", encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("scalp_main")


def in_session(now, cfg) -> bool:
    if not cfg.use_session_filter:
        return True
    t = now.strftime("%H:%M")
    return any(start <= t <= end for start, end in cfg.sessions)


def reconcile_journal(client: MT5Client, journal: TradeJournal, notifier: TelegramNotifier):
    """Copy từ main.py — logic thuần journal + MT5 history, không phụ thuộc TradingConfig."""
    open_now = {str(p.ticket) for p in client.open_positions()}
    for r in journal.open_records():
        if r["mode"] != "live" or r["ticket"] in open_now:
            continue
        info = client.position_close_info(int(r["ticket"]))
        if info:
            result, close_price, profit = info
            journal.record_close(r["ticket"], result, close_price, profit)
            notifier.notify_closed(r["symbol"], r, result, profit)


def resolve_scalp_paper_trades(journal: TradeJournal, m5_df: pd.DataFrame, cfg, symbol_info: dict):
    """Xử lý kết quả PAPER (dry_run): entry đã biết ngay (market fill), chỉ cần theo
    dõi các nến SAU 'created' để tìm SL/TP-hit — không có logic khớp limit như SMC."""
    closed = m5_df.iloc[:-1]
    cs = symbol_info["contract_size"]
    for r in journal.open_records():
        if r["mode"] != "paper":
            continue
        bars = closed[closed.index > pd.to_datetime(r["created"])]
        if bars.empty:
            continue
        state = PositionState(
            direction=r["direction"], entry=float(r["entry"]), sl=float(r["sl"]),
            original_sl=float(r["sl"]), tp=float(r["tp"]), lot=float(r["lot"]), cs=cs,
            entry_time=pd.to_datetime(r["created"]), rr=float(r["rr"]),
        )
        for ts, b in bars.iterrows():
            is_buy = state.direction == "buy"
            hit_sl = b["low"] <= state.sl if is_buy else b["high"] >= state.sl
            hit_tp = b["high"] >= state.tp if is_buy else b["low"] <= state.tp
            if hit_sl or hit_tp:
                exit_price = state.sl if hit_sl else state.tp
                cost = trade_cost(state, ts, state.lot, cfg)
                pnl = position_pnl(state, exit_price) - cost
                result = "success" if hit_tp else "failed"
                journal.record_close(r["ticket"], result, exit_price, profit=round(pnl, 2),
                                     closed=ts.to_pydatetime())
                break


@dataclass
class ScalpRunner:
    """Trạng thái độc lập cho 1 symbol khi nhiều symbol chạy chung 1 vòng lặp."""
    cfg: ScalpConfig
    client: MT5Client
    journal: TradeJournal
    guard: RiskGuard
    notifier: TelegramNotifier
    symbol_info: dict
    last_m5_bar: pd.Timestamp | None = None
    bars_since_exit: int = 10 ** 9
    trades_today: int = 0
    trade_day: date | None = None
    had_open: bool = False


def build_scalp_runner(name: str) -> ScalpRunner | None:
    cfg = get_scalp_config(name)
    client = MT5Client(cfg.symbol, cfg.magic_number, cfg.deviation)
    if not client.connect():
        log.error(f"⛔ Bỏ qua {cfg.symbol} — không kết nối/chọn được trên broker.")
        return None
    symbol_info = client.get_symbol_info()
    guard = RiskGuard(cfg.max_daily_loss_pct, cfg.portfolio_heat_pct)
    journal = TradeJournal(cfg.symbol, path=f"scalp_trades_{cfg.symbol}.csv")
    notifier = TelegramNotifier.from_config(cfg)
    log.info(f"🚀 [SCALP] {cfg.symbol} sẵn sàng | M5 EMA{cfg.ema_fast}/{cfg.ema_slow} | "
             f"risk {cfg.risk_per_trade_pct}%/lệnh | dry_run={cfg.dry_run}")
    log.info(f"📁 [SCALP {cfg.symbol}] Nhật ký: {journal.path.resolve()}")
    return ScalpRunner(cfg=cfg, client=client, journal=journal, guard=guard,
                       notifier=notifier, symbol_info=symbol_info)


def process_scalp_symbol(runner: ScalpRunner) -> None:
    cfg, client, journal, notifier = runner.cfg, runner.client, runner.journal, runner.notifier
    guard = runner.guard
    now = client.server_time()
    balance = client.get_balance()
    guard.update_day(now.date(), balance)

    reconcile_journal(client, journal, notifier)

    if guard.daily_loss_exceeded(balance):
        return
    if not in_session(now, cfg):
        return

    m5_df = client.get_rates("M5", cfg.bars)
    current_bar = m5_df.index[-1]
    if current_bar == runner.last_m5_bar:
        return
    runner.last_m5_bar = current_bar

    resolve_scalp_paper_trades(journal, m5_df, cfg, runner.symbol_info)

    n_open = len(client.open_positions())
    if runner.had_open and n_open == 0:
        runner.bars_since_exit = 0
    runner.had_open = n_open > 0
    runner.bars_since_exit += 1
    if now.date() != runner.trade_day:
        runner.trade_day = now.date()
        runner.trades_today = 0

    if n_open >= cfg.max_open_positions:
        return
    if runner.bars_since_exit < cfg.cooldown_bars or runner.trades_today >= cfg.max_trades_per_day:
        return

    plan = analyze_scalp(m5_df.iloc[:-1], cfg, balance, runner.symbol_info)
    if not plan:
        return

    log.info(f"🎯 [SCALP {cfg.symbol}] {plan.direction.upper()} @ {plan.entry} | "
             f"SL {plan.sl} | TP {plan.tp} | lot {plan.lot} | R:R {plan.rr} | {plan.reason}")

    if cfg.dry_run:
        log.info(f"[SCALP {cfg.symbol}] (dry_run — không đặt lệnh thật)")
        journal.record_open(f"scalp-paper-{int(now.timestamp())}", plan.direction,
                            plan.entry, plan.sl, plan.tp, plan.lot, plan.rr,
                            plan.risk_amount, plan.reason, mode="paper", created=now)
    else:
        ticket = client.market_order(plan.direction, plan.lot, plan.sl, plan.tp,
                                     comment=f"Scalp RR{plan.rr}")
        if ticket:
            journal.record_open(ticket, plan.direction, plan.entry, plan.sl, plan.tp,
                                plan.lot, plan.rr, plan.risk_amount, plan.reason,
                                mode="live", created=now)
            notifier.notify_opened(cfg.symbol, plan)
    runner.trades_today += 1


def main(symbol_names: list[str]) -> None:
    runners = [r for r in (build_scalp_runner(n) for n in symbol_names) if r is not None]
    if not runners:
        raise SystemExit("Không symbol nào kết nối được — dừng scalp bot.")
    poll_seconds = min(r.cfg.poll_seconds for r in runners)
    log.info(f"▶️  Scalp bot khởi động | {len(runners)} symbol: "
             f"{', '.join(r.cfg.symbol for r in runners)} | poll {poll_seconds}s")
    try:
        while True:
            for runner in runners:
                try:
                    process_scalp_symbol(runner)
                except Exception:
                    log.exception(f"⚠️ Lỗi xử lý {runner.cfg.symbol} — bỏ qua tick này.")
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        log.info("Dừng scalp bot theo yêu cầu.")
    finally:
        for runner in runners:
            runner.client.shutdown()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=None,
                   help="1 symbol: XAUUSDm | EURUSDm | GBPUSDm (alias: gold/eurusd/gbpusd).")
    p.add_argument("--symbols", default=None,
                   help="Danh sách symbol phân tách dấu phẩy — vd EURUSDm,GBPUSDm.")
    args = p.parse_args()
    if args.symbols:
        names = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.symbol:
        names = [args.symbol]
    else:
        names = ["XAUUSDm"]
    main(names)
```

- [ ] **Step 2: Verify it imports and parses args cleanly**

Run: `python scalp_main.py --help`
Expected: argparse help text with `--symbol`/`--symbols`, no import errors.

- [ ] **Step 3: Commit**

```bash
git add scalp_main.py
git commit -m "Add scalp_main.py: live/demo loop for M5 scalping stream"
```

---

### Task 7: Dry-run smoke test against real MT5

Validates the whole live path end-to-end in `dry_run` mode (paper trades only — no
real orders placed) before ever flipping `dry_run=False`.

**Known inherited limitation (not a new bug — same pattern as `main.py`):** in
`dry_run` mode, `n_open` (`process_scalp_symbol`) is computed only from
`client.open_positions()`, i.e. REAL MT5 positions. Paper trades never create a real
position, so `n_open` stays 0 and `bars_since_exit` never resets from a paper close —
meaning `max_open_positions`/`cooldown_bars` are effectively no-ops while
`dry_run=True`, and the bot can log a new paper signal on every tick with no gap. This
mirrors `main.py`'s existing dry-run behavior exactly (see `main.py:275-289`), so it is
NOT something to "fix" as part of this plan — just be aware of it when reading paper
journal output in Step 1-3 below (expect a denser stream of paper signals than the
cooldown config alone would suggest; this gating becomes fully effective only once
`dry_run=False` and real positions exist).

- [ ] **Step 1: Run for one symbol for a few minutes**

```bash
python scalp_main.py --symbol XAUUSDm
```
Let it run through at least 2-3 poll cycles. Expected: connects to MT5, logs
"🚀 [SCALP] XAUUSDm sẵn sàng...", then on each new M5 bar either logs nothing (no
signal) or logs a 🎯 signal line followed by "(dry_run — không đặt lệnh thật)" and a
journal write. No exceptions in `scalp_bot.log`. Stop with Ctrl+C — expect the
"Dừng scalp bot theo yêu cầu." log line and clean MT5 shutdown.

- [ ] **Step 2: Confirm `scalp_trades_XAUUSDm.csv` was created with the expected schema**

Check the file has the same columns as `trades_XAUUSDm.csv` (via `journal.py`'s
`FIELDS`) and any paper trade shows `mode=paper`.

- [ ] **Step 3: Run multi-symbol for a few minutes**

```bash
python scalp_main.py --symbols EURUSDm,GBPUSDm
```
Same checks as Step 1, confirming both symbols get independent journals
(`scalp_trades_EURUSDm.csv`, `scalp_trades_GBPUSDm.csv`) and independent cooldown/
trade-count state (no cross-symbol interference).

- [ ] **Step 4: (Optional, only if user wants to proceed toward real live) Concurrent-process check**

Run `python main.py --symbols XAUUSDm` and `python scalp_main.py --symbols XAUUSDm` at
the same time for a few minutes, confirm both keep polling without MT5
`initialize()`/`copy_rates_from_pos` errors in either log. This directly addresses the
spec's flagged risk about concurrent MT5 access from two processes.

No commit for this task (it's a manual verification step, not a code change). If any
step surfaces a bug, fix it in the relevant file from Tasks 1-6 and commit that fix
separately with a descriptive message.

---

### Task 8: Document the new commands in `CLAUDE.md`

**Files:**
- Modify: `d:\gold-smc-bot\CLAUDE.md`

- [ ] **Step 1: Add a new subsection under "Commands" describing the scalp stream**

Add after the existing `main.py`/`backtest.py` command examples:

```markdown
### Scalping M5 stream (independent, separate process)

A fully independent scalping strategy — single-timeframe M5, EMA trend-pullback + RSI +
ATR SL/TP — lives in `scalp_config.py`/`scalp_strategy.py`/`scalp_backtest.py`/
`scalp_main.py`. It shares NOTHING with the SMC bot above (own magic numbers, own
journal files `scalp_trades_<symbol>.csv`, own risk %). See
`docs/superpowers/specs/2026-07-31-scalp-m5-design.md` for the full design.

```bash
# Backtest
python scalp_backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000

# Live/demo (dry_run=True by default in scalp_config.py)
python scalp_main.py --symbol XAUUSDm
python scalp_main.py --symbols EURUSDm,GBPUSDm
```

Can run alongside `main.py` in a separate process on the same MT5 terminal — see the
spec's "Rủi ro cần xác minh" section before relying on this in live trading.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "Document scalp M5 stream commands in CLAUDE.md"
```
