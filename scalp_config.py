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
