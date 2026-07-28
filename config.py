"""
Cấu hình trung tâm cho SMC Bot.

`TradingConfig` là SCHEMA (khai báo mọi tham số + mặc định).
Mỗi symbol có MỘT config RIÊNG BIỆT bên dưới — tune cứng symbol này KHÔNG đụng symbol kia:
  - XAUUSD  → config vàng (đã tune 2 năm)
  - EURUSD  → config forex (scale giá 5-số)

Chọn config khi chạy bằng `get_config("XAUUSDm" | "EURUSDm")` hoặc alias ("gold"/"eurusd").
Xem `backtest.py --symbol` và `main.py --symbol`.
"""
from dataclasses import dataclass, field, replace


@dataclass
class TradingConfig:
    # ── Symbol & Timeframes ──────────────────────────────
    symbol: str = "XAUUSDm"          # Một số broker dùng "GOLD", "XAUUSDm"...
    htf: str = "H4"                 # Khung xác định xu hướng
    ltf: str = "M15"                # Khung vào lệnh
    htf_bars: int = 500             # Số nến HTF tải về mỗi lần phân tích
    ltf_bars: int = 500

    # ── Scale giá theo symbol ────────────────────────────
    # Vàng: giá ~$3000, làm tròn 2 số, các "points" tính bằng USD.
    # Forex 5-số (EURUSD): giá ~1.08, làm tròn 5 số, "points" tính bằng giá (1 pip = 0.0001).
    price_digits: int = 2           # Số chữ số làm tròn entry/SL/TP (vàng=2, forex=5)
    eq_tolerance: float = 1.0       # Gộp equal highs/lows thành 1 pool (USD cho vàng)

    # ── Market Structure (Swing detection) ───────────────
    swing_lookback: int = 3         # Nến trái/phải để xác nhận swing high/low
    htf_swing_lookback: int = 5     # HTF dùng lookback rộng hơn cho cấu trúc sạch

    # ── Order Block ──────────────────────────────────────
    ob_max_age_bars: int = 100      # OB quá cũ thì bỏ qua
    ob_require_imbalance: bool = True  # OB phải đi kèm FVG mới hợp lệ (chất lượng cao hơn)

    # ── Fair Value Gap ───────────────────────────────────
    fvg_min_size_points: float = 0.5   # FVG tối thiểu (USD) — lọc gap nhiễu trên vàng
    fvg_max_age_bars: int = 80

    # ── Liquidity Sweep ──────────────────────────────────
    sweep_lookback: int = 40        # Tìm đỉnh/đáy thanh khoản trong N nến gần nhất
    require_sweep: bool = True      # Bắt buộc có quét thanh khoản trước CHoCH

    # ── Entry logic ──────────────────────────────────────
    entry_mode: str = "ob_or_fvg"   # "ob_only" | "fvg_only" | "ob_or_fvg"
    confirmation: str = "choch"     # Vào lệnh sau CHoCH trên LTF thuận hướng HTF

    # ── Chống vào lệnh trùng lặp / bắn liên thanh ────────
    # Mỗi cú sweep chỉ giao dịch 1 lần; sau khi 1 lệnh đóng phải chờ cooldown_bars
    # nến LTF mới tìm tín hiệu tiếp. Đây là bộ lọc chống re-entry quan trọng nhất.
    one_trade_per_sweep: bool = True
    cooldown_bars: int = 8          # ~2 giờ M15 nghỉ sau mỗi lệnh
    max_trades_per_day: int = 3     # Trần số lệnh/ngày (chống overtrade)

    # ── Lọc chất lượng entry ─────────────────────────────
    min_sl_distance_points: float = 3.0  # SL phải cách entry >= mức này (USD).
    #   Loại các entry "sát SL" khiến lot phình to và bị quét ngay (winrate ~10%).
    max_rr: float = 10.0            # Trần R:R cho TP thanh khoản (tránh TP quá xa)
    # #9 — Trần "tuổi" CẢ CHUỖI setup: sweep→CHoCH→entry phải còn tươi. Nếu sweep HOẶC
    #   cú xác nhận cách nến hiện tại > mức này thì bỏ (bối cảnh đã phân rã). 0 = tắt.
    max_setup_age_bars: int = 30
    # #4 — Lệnh LIMIT nghỉ tại biên vùng hết hạn sau bao nhiêu nến LTF nếu giá không hồi
    #   về khớp (hủy để không "ôm" limit cũ khi bối cảnh đã đổi).
    entry_expiry_bars: int = 12
    # #6 — Chỉ mua ở nửa DISCOUNT / bán ở nửa PREMIUM của dải giao dịch (equilibrium 50%).
    #   ĐÃ ĐO 2 năm: bật lên LÀM GIẢM mạnh cả 2 symbol (vàng: 270→108 lệnh, EURUSD PF 1.24→0.88).
    #   Lý do: đây là chiến lược THUẬN xu hướng — vùng retest OB/FVG thường nằm ở nửa premium
    #   khi trend còn mạnh; ép "mua discount" chỉ bắt các hồi sâu (thường lúc trend sắp gãy).
    #   → MẶC ĐỊNH TẮT. Bật + tinh chỉnh eq_range_swings nếu muốn thử kiểu reversal.
    require_discount_premium: bool = False
    eq_range_swings: int = 6        # Dựng dải từ N swing high/low gần nhất để lấy mốc 50%
    # #8 — Đứng ngoài khi thị trường ĐI NGANG. Sau warmup, detect_structure gần như KHÔNG
    #   bao giờ "neutral" (một cú phá swing cũ đã lật trend) → bot luôn ôm thiên hướng vào
    #   cả lúc chợ loãng. Nếu KHÔNG có BOS/CHoCH HTF mới trong N nến gần nhất → coi là
    #   ranging → trend = neutral → không vào lệnh. 0 = tắt.
    htf_trend_max_age_bars: int = 24

    # #10 — Lọc pool thanh khoản YẾU (đỉnh/đáy phụ nông trong dải). Sweep chỉ hợp lệ nếu
    #   pool bị quét nằm trong N pool CỰC TRỊ nhất (mạnh nhất) cùng chiều còn chưa bị quét
    #   tại thời điểm đó. Tránh vào lệnh theo cú quét 1 đỉnh/đáy phụ mờ nhạt giữa 1 dải giá
    #   rộng hơn nhiều (dải mới là thanh khoản thật). 0 = tắt (mọi pool đều hợp lệ, mặc định).
    sweep_pool_min_rank: int = 0

    # #11 — Cấm mở lệnh MỚI trong N giờ cuối trước khi phiên (session cuối trong ngày) đóng
    #   cửa vào Thứ Sáu — không ảnh hưởng quản lý lệnh đang mở (vẫn chạy mọi lúc). Tránh ôm
    #   lệnh SL sát qua cả cuối tuần không ai quản lý được. 0 = tắt.
    weekend_guard_hours: float = 0.0

    # #7 — Bộ lọc TIN TỨC mạnh (STUB, defer). Bật sau khi cắm nguồn dữ liệu sự kiện (xem news.py).
    news_filter_enabled: bool = False
    news_csv: str = ""              # đường dẫn CSV sự kiện lịch sử (time,currency,impact)
    news_buffer_min: int = 15       # cấm vào lệnh trong ±phút quanh tin impact cao
    news_currencies: tuple = ("USD", "XAU")  # currency filter cho blackout (mặc định vàng)

    # ── Risk Management ──────────────────────────────────
    risk_per_trade_pct: float = 1.15   # % tài khoản mỗi lệnh (1.15% để đạt mục tiêu ~30%/năm)
    min_rr: float = 2.0                # Chỉ vào lệnh nếu R:R >= 2
    tp_rr: float = 2.5                 # TP mặc định = 2.5R (nếu không dùng liquidity target)
    tp_mode: str = "liquidity"         # "fixed_rr" | "liquidity" (TP tại pool thanh khoản đối diện)
    sl_buffer_points: float = 1.5      # Đệm SL ngoài OB/swing (USD) tránh quét râu nến
    max_open_positions: int = 1        # Vàng biến động mạnh — 1 lệnh tại 1 thời điểm
    max_daily_loss_pct: float = 3.0    # Dừng bot trong ngày nếu lỗ 3%
    portfolio_heat_pct: float = 6.0    # Tổng rủi ro mở tối đa (đồng bộ hệ VN30 của bạn)

    # ── Breakeven & Trailing ─────────────────────────────
    # Chốt 25% sớm ở 1.25R để "khoá" lệnh thành net-win (nâng winrate),
    # dời SL về hoà vốn ở 1R để bảo vệ, phần còn lại (75%) chạy tới TP thanh khoản.
    move_sl_to_be_at_rr: float = 1.0   # Dời SL về hòa vốn khi đạt 1R
    partial_close_at_rr: float = 1.25  # Chốt phần nhỏ tại 1.25R
    partial_close_pct: float = 25.0    # Chỉ chốt 25% — để lại runner lớn cho lợi nhuận

    # ── Chi phí giao dịch (mô phỏng thực tế trong backtest) ──
    # Đơn vị GIÁ giống price scale của symbol. Backtest trừ các phí này vào PnL
    # từng lần khớp (kể cả partial, theo tỉ lệ lot) → PF/winrate/CAGR đã net-of-cost.
    # Số mặc định là ước lượng bảo thủ cho tài khoản Standard ("m", không commission);
    # chỉnh theo broker thật của bạn để sát hơn. Tắt toàn bộ bằng cờ --no-costs.
    spread_points: float = 0.25        # Spread trung bình (USD cho vàng ~25 cent). Tính round-trip vào+ra.
    commission_per_lot: float = 0.0    # Hoa hồng round-turn mỗi lot (USD). Tài khoản raw/ECN mới có.
    swap_long_per_lot: float = -4.0    # Swap qua đêm lệnh BUY (USD/lot/đêm, ÂM = trả phí)
    swap_short_per_lot: float = -2.5   # Swap qua đêm lệnh SELL (USD/lot/đêm, ÂM = trả phí)

    # ── Session filter (giờ server MT5, thường GMT+2/+3) ─
    use_session_filter: bool = True
    sessions: list = field(default_factory=lambda: [
        ("08:00", "17:00"),   # London → NY overlap (khung thanh khoản mạnh của vàng)
    ])                        # Tránh phiên Á loãng & giờ rollover cuối US (winrate kém)

    # ── Telegram notifications ───────────────────────────
    # Bắn tin khi: có tín hiệu / đặt lệnh thành công / lệnh đóng (CHỈ ở chế độ live,
    # dry_run không bắn). Có thể override bằng biến môi trường TELEGRAM_TOKEN /
    # TELEGRAM_CHAT_ID (xem notifier.py).
    telegram_enabled: bool = True
    telegram_token: str = "8490073729:AAGvg5l0cq9SNcsHXHLqAznKcdaZyJwIkrc"
    telegram_chat_id: str = "5870497244"

    # ── Execution ────────────────────────────────────────
    magic_number: int = 20260723
    deviation: int = 20             # Slippage tối đa (points)
    poll_seconds: int = 30          # Chu kỳ quét tín hiệu (giây)
    dry_run: bool = False            # True = chỉ log tín hiệu, không đặt lệnh thật


# ═════════════════════════════════════════════════════════
#  CONFIG VÀNG (XAUUSDm) — đã tune trên backtest 2 năm.
#  Mặc định của TradingConfig CHÍNH LÀ giá trị vàng, nên tune vàng = sửa
#  mặc định phía trên HOẶC ghi đè trong khối này.
# ═════════════════════════════════════════════════════════
XAUUSD = TradingConfig(
    symbol="XAUUSDm",
    magic_number=20260723,
    # scale giá vàng (khớp mặc định — để rõ ràng, độc lập với EURUSD)
    price_digits=2,
    eq_tolerance=1.0,               # gộp equal H/L trong $1
    fvg_min_size_points=0.5,        # FVG tối thiểu $0.5
    sl_buffer_points=1.5,           # đệm SL $1.5
    min_sl_distance_points=3.0,     # sàn khoảng SL $3
    # chi phí THẬT đo từ MT5 Exness (Standard "m") ngày 2026-07-23:
    spread_points=0.28,             # median 280 points phiên 08–17 (ổn định, p90=280)
    commission_per_lot=0.0,         # Standard = miễn commission
    swap_long_per_lot=-49.04,       # swap_long thật (POINTS -490.4 × $0.10/point)
    swap_short_per_lot=0.0,         # Exness miễn swap chiều BÁN vàng
)

# ═════════════════════════════════════════════════════════
#  CONFIG EURUSD (EURUSDm) — forex 5-số. TUNE CỨNG TẠI ĐÂY,
#  không ảnh hưởng vàng. Hiện là baseline (scale từ vàng, CHƯA tối ưu riêng).
#  Muốn EURUSD khác vàng ở tham số nào (min_rr, tp_rr, sessions, risk...) →
#  thêm dòng đó vào khối này.
# ═════════════════════════════════════════════════════════
EURUSD = TradingConfig(
    symbol="EURUSDm",
    magic_number=20260724,          # magic RIÊNG — chạy live song song không đụng lệnh vàng
    news_currencies=("USD", "EUR"),
    # scale giá forex 5-số (1 pip = 0.0001)
    price_digits=5,
    eq_tolerance=0.0005,            # gộp equal H/L trong 5 pip
    sl_buffer_points=0.0003,       # đệm SL 3 pip ngoài OB/swing
    min_sl_distance_points=0.0010, # sàn khoảng SL 10 pip (chống lot phình)
    # ── Đã tune 2 năm (2024-07→2026-07), tối ưu Profit Factor ──
    #   Full-2y: PF 1.58, WR 48%, CAGR 37.6%, MaxDD 6.2%, ~186 lệnh.
    #   Robustness: PF nửa đầu 1.96 / nửa sau 1.28 (edge yếu dần — kỳ vọng thực gần H2 hơn).
    fvg_min_size_points=0.0002,    # FVG tối thiểu 2 pip
    min_rr=2.5,                    # chỉ nhận TP thanh khoản nếu >= 2.5R, nếu không dùng fixed
    tp_rr=3.0,                     # TP fixed = 3.0R (sweet spot PF cho EURUSD)
    tp_mode="liquidity",           # ưu tiên pool thanh khoản, fallback 3.0R
    ob_require_imbalance=True,     # OB phải kèm FVG (lọc chất lượng)
    sessions=[("13:00", "18:00")], # CHỈ phiên NY-overlap (giờ server) — PF cao nhất cho EURUSD
    # ── Tune lại 2 năm SAU khi sửa 9 lỗi (limit-entry #4, SL-theo-râu #5, ...) ──
    #   Cơ chế mới ĐÚNG hơn nhưng làm lộ ra edge EURUSD mỏng hơn backtest cũ (vốn được
    #   thổi phồng bởi chính các lỗi đó). Best honest: PF 1.31, WR 47%, CAGR 12.4%, DD 8.1%, n=131.
    entry_expiry_bars=48,          # #4: EURUSD hồi về vùng CHẬM → cửa sổ khớp limit rộng hơn nhiều (sweep: 48 > 36 > 12)
    # max_setup_age_bars giữ mặc định 30 (đã là tốt nhất cho EURUSD trong sweep)
    # ── #10/#11 (2026-07-27): sau case lệnh SELL lỗ 24/07 — HTF đã ranging 10 ngày
    #   (1.136–1.148) khi tín hiệu bắn, sweep chỉ là 1 đỉnh phụ nông trong dải (không phải
    #   biên dải thật), và lệnh mở lúc 16:45 giờ server (Thứ Sáu, ~1h15 trước khi đóng cửa
    #   cuối tuần) nên ôm nguyên cuối tuần không ai quản lý được. Bật lại 3 chốt chặn RIÊNG
    #   cho EURUSD (KHÔNG đụng default/vàng) — chưa re-sweep lại 2 năm, ưu tiên giảm rủi ro
    #   theo review hơn là tối ưu PF thuần, xem lại nếu số lệnh giảm quá mạnh.
    htf_trend_max_age_bars=40,     # #8/#10: bật lại lọc ranging (đã TẮT hẳn = 0 trước đây),
                                    # nới hơn vàng (24) vì EURUSD hồi/hình thành trend chậm hơn.
    # sweep_pool_min_rank: THỬ 2 năm (2026-07-27) — rank theo cực trị TOÀN BỘ cửa sổ
    # ltf_bars (500 nến) chứ không theo cục bộ/gần giá hiện tại → trong thị trường có xu
    # hướng, "3 pool cực trị nhất" thường là các đỉnh/đáy CŨ xa giá hiện tại, gần như
    # không bao giờ được test lại → n 131→14, PF 1.27→0.66. TẮT (giữ mặc định 0) cho đến
    # khi có phép đo "nổi bật cục bộ" đúng hơn thay vì cực trị toàn cửa sổ.
    weekend_guard_hours=3.0,        # #11: không mở lệnh MỚI trong 3h cuối trước khi phiên
                                    # đóng cửa cuối tuần (Thứ Sáu) — quản lý lệnh đang mở vẫn
                                    # chạy bình thường, chỉ chặn tín hiệu mới.
    # risk lever (2026-07-25): winrate/PF không đổi theo risk% (edge cố định), chỉ CAGR/DD đổi.
    # Sweep 1.15→3.0%: mục tiêu lợi nhuận 40-50%/2y → chọn 2.0% (ret 49.3%, CAGR 21.3%, DD 13.8%).
    risk_per_trade_pct=2.0,
    # chi phí THẬT đo từ MT5 Exness (Standard "m") ngày 2026-07-23:
    spread_points=0.00008,         # median 8 points (0.8 pip) phiên NY 13–18
    commission_per_lot=0.0,        # Standard = miễn commission
    swap_long_per_lot=-6.0,        # swap_long thật (POINTS -6.0 × $1.00/point)
    swap_short_per_lot=0.0,        # Exness miễn swap chiều BÁN
)


# ── Registry: chọn config theo symbol ────────────────────
CONFIGS = {
    "XAUUSDm": XAUUSD,
    "EURUSDm": EURUSD,
}
# Alias thân thiện cho dòng lệnh
_ALIASES = {
    "gold": "XAUUSDm", "xau": "XAUUSDm", "xauusd": "XAUUSDm", "vang": "XAUUSDm",
    "eur": "EURUSDm", "eurusd": "EURUSDm",
}


def get_config(name: str) -> "TradingConfig":
    """Trả về config của symbol (hoặc alias). Trả về BẢN SAO để chỉnh runtime không rò rỉ."""
    key = _ALIASES.get(name.lower(), name)
    if key not in CONFIGS:
        raise SystemExit(
            f"Symbol '{name}' chưa có config. Hỗ trợ: {list(CONFIGS)} "
            f"(alias: {list(_ALIASES)})"
        )
    return replace(CONFIGS[key])   # copy để dry_run/override từ CLI không sửa bản gốc


# Mặc định (tương thích ngược cho code cũ import CONFIG): vàng.
CONFIG = XAUUSD
