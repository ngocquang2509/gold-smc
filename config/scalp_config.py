"""
Cấu hình cho luồng SCALPING M5 độc lập — TÁCH BIỆT HOÀN TOÀN với TradingConfig/config.py
(bot SMC đa khung H4→M15). Không import/dùng chung tham số nào với config.py.

Mỗi symbol có 1 instance riêng, chọn bằng get_scalp_config(name).
Xem docs/superpowers/specs/2026-07-31-scalp-m5-design.md.

═══════════════════════════════════════════════════════════
BASELINE CHƯA TUNE (đo 2026-07-31, --from-mt5 --years 2) — mọi symbol đều LỖ RÒNG:
  XAUUSDm : n=3774  WR=39.6%  PF=0.84  CAGR=-54.5%  MaxDD=79.97%
  EURUSDm : n=2987  WR=39.7%  PF=0.77  CAGR=-70.8%  MaxDD=92.08%
  GBPUSDm : n=4082  WR=38.6%  PF=0.69  CAGR=-85.1%  MaxDD=97.93%
Root cause đo được (2026-08-03, xem scalp-tuning-2026-08 memory): KHÔNG PHẢI chi phí
(spread/swap chỉ ~$2/lệnh với gold) — winrate thực (~38-40%) nằm dưới ngưỡng hoà vốn
cần thiết cho RR thiết kế 1.5 (~40-44% sau chi phí). Setup EMA-pullback+RSI không phân
biệt được trend thật với dao động sideway/chop.

VÒNG TUNE 2026-08-03 (ADX trend-strength gate + RSI/TP): chỉ cải thiện được XAUUSDm —
adx_min_threshold=30 + tp_atr_mult=2.2 + RSI buy(55-70)/sell(30-45) đưa PF 0.84→1.00
(hoà vốn, KHÔNG lãi rõ ràng — H1 PF 1.10 nhưng H2 PF 0.90) và giảm mạnh MaxDD 80%→17%,
n giảm 3774→598 (tần suất thấp hơn nhiều). EURUSDm/GBPUSDm: đã thử ADX threshold
20/25/30 và RSI/TP tương tự — KHÔNG cải thiện (PF vẫn 0.62-0.78, dưới hoặc quanh
baseline) nên ADX gate đang TẮT cho 2 symbol này (xem comment tại từng instance).
KẾT LUẬN: đây không phải lỗi tham số đơn giản — signal EMA-pullback+RSI có vẻ không có
edge thời gian thực đáng kể trên M5 cho 3 symbol này với broker/chi phí hiện tại. XAUUSDm
sau tune chỉ ở mức hoà vốn (chưa chứng minh lãi ổn định) — KHÔNG chạy live/demo với bất
kỳ symbol nào cho tới khi có thêm xác nhận (forward test dài hơn, hoặc redesign entry
logic — vd thêm xác nhận đa khung/price-action thay vì chỉ EMA+RSI 1 khung).

VÒNG TUNE 2026-08-05 (EURUSDm + GBPUSDm, mục tiêu: đạt ngang XAUUSDm) — grid 4 phase
(ADX threshold, RSI band, TP/SL multiplier, session window) cho từng symbol, mỗi bước
qua H1/H2 split để loại overfit:
  EURUSDm : PF 0.77→0.85 (chỉ giữ adx_min_threshold=35; RSI/TP/session không cải thiện
            thêm, session hẹp về NY-overlap còn gây overfit rõ H2/H1=52-66%). MaxDD
            92%→12.5%. Vẫn LỖ RÒNG, dưới XAUUSDm.
  GBPUSDm : PF 0.69→0.78 (ADX gate CÓ HẠI — ngược hẳn 2 symbol kia, giữ TẮT; đòn bẩy
            chính là session hẹp về London-NY overlap 12:00-16:00 — ổn định H1 0.73/H2
            0.85, không overfit; + tp_atr_mult=2.2). MaxDD 98%→65%. Vẫn LỖ RÒNG, dưới
            XAUUSDm, và MaxDD vẫn cao.
Cả 2 symbol đều KHÔNG đạt ngưỡng hoà vốn của XAUUSDm sau grid 4 phase — theo điều kiện
dừng đã thống nhất, đây là dấu hiệu setup EMA-pullback+RSI không có edge cấu trúc trên
2 symbol này ở M5; cần cân nhắc redesign (vd tách trend/range-method riêng) thay vì tiếp
tục grind tham số. Chi tiết per-symbol xem comment tại từng instance bên dưới.
═══════════════════════════════════════════════════════════
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

    # ── ADX trend-strength gate (tùy chọn, tắt mặc định) ──
    # Winrate thực đo (baseline 2026-07-31, 3 symbol) đều ~38-40%, DƯỚI ngưỡng hoà vốn
    # cần thiết cho RR thiết kế 1.5 (~40-44% sau chi phí) — root cause không phải chi phí
    # (chỉ ~$2/lệnh với gold) mà là setup EMA-pullback+RSI bắt cả dao động trong thị
    # trường sideway/chop, không phân biệt được trend thật. Gate này (tái dùng
    # strategy/indicators.py.adx — cùng công thức bot SMC) chặn tín hiệu khi ADX đo tại
    # nến trigger dưới ngưỡng, để lọc bớt chop. Bật/tắt độc lập theo từng symbol.
    adx_filter_enabled: bool = False
    adx_period: int = 14
    adx_min_threshold: float = 20.0

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
    # Dùng chung bot/chat với bot SMC (config.py) — có thể override bằng biến môi trường
    # TELEGRAM_TOKEN / TELEGRAM_CHAT_ID nếu muốn tách kênh riêng cho scalp.
    telegram_enabled: bool = True
    telegram_token: str = "8490073729:AAGvg5l0cq9SNcsHXHLqAznKcdaZyJwIkrc"
    telegram_chat_id: str = "5870497244"

    # ── Execution ──────────────────────────────────────────
    magic_number: int = 20260731  # magic RIÊNG cho luồng scalp, khác 3 magic của bot SMC
    deviation: int = 20
    poll_seconds: int = 10        # M5 → poll nhanh hơn SMC, cần bắt kịp nến mới đóng
    dry_run: bool = False


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
    adx_filter_enabled=True,
    adx_min_threshold=30.0,
    tp_atr_mult=2.2,
    rsi_buy_min=55.0,
    rsi_buy_max=70.0,
    rsi_sell_min=30.0,
    rsi_sell_max=45.0,
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
    # ADX gate — tune 2026-08-05: grid 15/20/25/30/35/40/45 (giữ RSI/TP/session mặc định).
    # threshold=35 là điểm tốt nhất KHÔNG bị overfit-flag: PF 0.77(baseline)→0.85, H1 0.90/H2
    # 0.80 (H2/H1=89%, ổn định), MaxDD 92%→12.5%, n 2974→252. threshold=40+ cho PF cao hơn
    # trên giấy (0.91, 1.10) nhưng H2/H1 tụt xuống 76% rồi 58% — dấu hiệu overfit rõ do n quá
    # nhỏ (40: n=123; 45: n=50, H2 chỉ 20 lệnh). RSI band và TP/SL multiplier grid (Phase 2-3)
    # KHÔNG cải thiện thêm so với mặc định — đã thử tighten RSI kiểu XAUUSDm (tệ hơn, PF 0.73),
    # TP 1.5-2.2 và SL 1.0-1.5 (mọi combo đều dưới 0.85). Session hẹp về NY-overlap (12-16,
    # 11-18) cho PF full-period nhỉnh hơn (0.90, 0.88) nhưng H2/H1 rơi xuống 52%/66% — KHÔNG
    # giữ, kém ổn định hơn session gốc. KẾT LUẬN: PF 0.85 vẫn LỖ RÒNG (chưa hòa vốn) — chưa đạt
    # ngang XAUUSDm (PF~1.00). Vẫn KHÔNG chạy live/demo.
    adx_filter_enabled=True,
    adx_min_threshold=35.0,
)

GBPUSD_SCALP = ScalpConfig(
    symbol="GBPUSDm",
    magic_number=20260733,
    min_atr_points=0.00018,       # ~1.8 pip (GBPUSD biến động hơn EURUSD)
    spread_points=0.00010,
    commission_per_lot=0.0,
    swap_long_per_lot=-1.5,
    swap_short_per_lot=-1.1,
    # Tune 2026-08-05 (baseline PF 0.69, n=4070, MaxDD 97.9%):
    # - ADX gate CÓ HẠI — PF giảm ĐƠN ĐIỆU khi tăng threshold (15=0.68, 20=0.66, 25=0.67,
    #   30=0.62, 35=0.60). Ngược hẳn XAUUSDm/EURUSDm (ADX cao hơn giúp cả 2 symbol đó). TẮT.
    # - RSI band tighten (moderate lẫn kiểu XAUUSDm) không thay đổi gì đáng kể (0.69/0.68).
    #   Giữ mặc định.
    # - tp_atr_mult tăng dần 1.8→3.0 cải thiện PF (0.69→0.72→0.73) nhưng MaxDD vẫn ~97-99%
    #   ở mọi mức — vấn đề không nằm ở TP. Chọn 2.2 (PF 0.72, H1/H2 ổn định 0.71/0.76).
    # - Session hẹp về London-NY overlap là đòn bẩy MẠNH NHẤT: 12:00-16:00 → PF 0.78, H1
    #   0.73/H2 0.85 (RẤT ổn định, không overfit), MaxDD giảm mạnh 97.9%→65.4%. Test thêm
    #   11:00-17:00 (PF bằng 0.78 nhưng MaxDD tệ hơn 74.8%) và 13:00-15:00 (PF thấp hơn
    #   0.76, MaxDD 47.5% nhưng n chỉ 771) — 12:00-16:00 là điểm cân bằng tốt nhất.
    # KẾT LUẬN: PF 0.78 vẫn LỖ RÒNG, CHƯA đạt XAUUSDm (PF~1.00). MaxDD vẫn cao (65%) dù
    # cải thiện nhiều so với baseline. Vẫn KHÔNG chạy live/demo.
    tp_atr_mult=2.2,
    sessions=[("12:00", "16:00")],
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
