"""
Cấu hình HẠ TẦNG cho bot (symbol, scale giá, chi phí, rủi ro, Telegram, execution).

KHÔNG chứa tham số chiến lược. Tầng Strategy cũ (SMC + scalp) đã gỡ — xem tag git
`legacy-v1` và ADR `docs/adr/0001-rebuild-strategy-layer-validation-first.md`.
Mỗi Candidate mới tự khai báo ≤ 4 tham số của nó (Complexity Budget, xem GLOSSARY.md),
dùng CHUNG cho mọi symbol (Shared Parameter Set) — nên ở đây chỉ còn các sự thật về
symbol/broker, không có gì để tune theo từng symbol.

Chọn config bằng `get_config("XAUUSDm" | "EURUSDm" | "GBPUSDm")` hoặc alias ("gold"/"eurusd"/"gbpusd").
"""
from dataclasses import dataclass, replace


@dataclass
class TradingConfig:
    # ── Symbol & scale giá ───────────────────────────────
    symbol: str = "XAUUSDm"         # Một số broker dùng "GOLD", "XAUUSDm"...
    price_digits: int = 2           # Số chữ số làm tròn entry/SL/TP (vàng=2, forex=5)
    contract_size: float = 100.0    # Đơn vị/lot: vàng 100 oz, forex 100_000

    # ── Chi phí giao dịch (đơn vị GIÁ theo scale của symbol) ──
    # Backtest trừ các phí này vào PnL từng lần khớp (kể cả partial, theo tỉ lệ lot).
    # Acceptance Gate đo dưới Cost Stress = 1.5× các số này (xem GLOSSARY.md).
    spread_points: float = 0.25        # Spread trung bình, tính round-trip vào+ra
    commission_per_lot: float = 0.0    # Hoa hồng round-turn mỗi lot (USD)
    swap_long_per_lot: float = -4.0    # Swap qua đêm lệnh BUY (USD/lot/đêm, ÂM = trả phí)
    swap_short_per_lot: float = -2.5   # Swap qua đêm lệnh SELL (USD/lot/đêm, ÂM = trả phí)

    # ── Risk Management ──────────────────────────────────
    # Rủi ro/lệnh được SUY RA sau khi qua Acceptance Gate (OOS max DD ≤ 15%), không bao
    # giờ > 1%, và chia đôi trong 3 tháng live đầu (ADR 0001). 0.5 là mức an toàn tạm.
    risk_per_trade_pct: float = 0.5
    max_open_positions: int = 1
    max_daily_loss_pct: float = 3.0    # RiskGuard: dừng trong ngày nếu lỗ 3%
    portfolio_heat_pct: float = 6.0    # Tổng rủi ro mở tối đa

    # ── Engine quản lý lệnh (risk.manage_step / manage_tick) ──
    # Giữ giá trị cũ để engine còn chạy được; mỗi Candidate sẽ tự quyết có dùng BE/partial
    # hay không (tính vào Complexity Budget của nó — ADR 0001, Q16).
    move_sl_to_be_at_rr: float = 1.0
    partial_close_at_rr: float = 1.25
    partial_close_pct: float = 25.0

    # ── Telegram notifications ───────────────────────────
    # Token/chat id CHỈ lấy từ biến môi trường TELEGRAM_TOKEN / TELEGRAM_CHAT_ID
    # (hoặc file .env đã gitignore) — KHÔNG commit secret vào đây.
    telegram_enabled: bool = True
    telegram_token: str = ""
    telegram_chat_id: str = ""

    # ── Execution ────────────────────────────────────────
    magic_number: int = 20260723
    deviation: int = 20             # Slippage tối đa (points)
    poll_seconds: int = 30          # Chu kỳ quét tín hiệu (giây)
    dry_run: bool = True            # True = chỉ log tín hiệu, không đặt lệnh thật


# ═════════════════════════════════════════════════════════
#  Sự thật symbol/broker (Exness Standard "m"). Chi phí THẬT đo từ MT5.
# ═════════════════════════════════════════════════════════
XAUUSD = TradingConfig(
    symbol="XAUUSDm",
    magic_number=20260723,
    price_digits=2,
    # đo 2026-07-23
    spread_points=0.28,             # median 280 points phiên 08–17
    commission_per_lot=0.0,         # Standard = miễn commission
    swap_long_per_lot=-49.04,       # POINTS -490.4 × $0.10/point
    swap_short_per_lot=0.0,         # Exness miễn swap chiều BÁN vàng
)

EURUSD = TradingConfig(
    symbol="EURUSDm",
    magic_number=20260724,          # magic RIÊNG — không đụng lệnh symbol khác
    price_digits=5,
    contract_size=100_000,
    # đo 2026-07-23
    spread_points=0.00008,          # median 8 points (0.8 pip) phiên NY 13–18
    commission_per_lot=0.0,
    swap_long_per_lot=-6.0,         # POINTS -6.0 × $1.00/point
    swap_short_per_lot=0.0,         # Exness miễn swap chiều BÁN
)

GBPUSD = TradingConfig(
    symbol="GBPUSDm",
    magic_number=20260725,
    price_digits=5,
    contract_size=100_000,
    # đo 2026-07-30
    spread_points=0.00010,          # median 10 points (1 pip)
    commission_per_lot=0.0,
    swap_long_per_lot=-1.5,         # POINTS -1.5 × $1.00/point
    swap_short_per_lot=-1.1,        # POINTS -1.1 × $1.00/point
)


# ── Registry: chọn config theo symbol ────────────────────
CONFIGS = {
    "XAUUSDm": XAUUSD,
    "EURUSDm": EURUSD,
    "GBPUSDm": GBPUSD,
}
# Alias thân thiện cho dòng lệnh
_ALIASES = {
    "gold": "XAUUSDm", "xau": "XAUUSDm", "xauusd": "XAUUSDm", "vang": "XAUUSDm",
    "eur": "EURUSDm", "eurusd": "EURUSDm",
    "gbp": "GBPUSDm", "gbpusd": "GBPUSDm",
}


COST_STRESS = 1.5   # Mọi số liệu Acceptance Gate đo dưới chi phí ×1.5 (GLOSSARY: Cost Stress)


def stressed(cfg: TradingConfig, factor: float = COST_STRESS) -> TradingConfig:
    """Bản sao cfg với chi phí bị phóng đại ×factor. Chỉ phóng CHI PHÍ: swap dương
    (broker trả tiền cho mình) giữ nguyên — stress không được làm lợi nhuận đẹp hơn."""
    def worse(swap: float) -> float:
        return swap * factor if swap < 0 else swap
    return replace(
        cfg,
        spread_points=cfg.spread_points * factor,
        commission_per_lot=cfg.commission_per_lot * factor,
        swap_long_per_lot=worse(cfg.swap_long_per_lot),
        swap_short_per_lot=worse(cfg.swap_short_per_lot),
    )


def get_config(name: str) -> "TradingConfig":
    """Trả về config của symbol (hoặc alias). Trả về BẢN SAO để chỉnh runtime không rò rỉ."""
    key = _ALIASES.get(name.lower(), name)
    if key not in CONFIGS:
        raise SystemExit(
            f"Symbol '{name}' chưa có config. Hỗ trợ: {list(CONFIGS)} "
            f"(alias: {list(_ALIASES)})"
        )
    return replace(CONFIGS[key])   # copy để override từ CLI không sửa bản gốc
