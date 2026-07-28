"""
Bộ lọc TIN TỨC tác động mạnh (#7) — HIỆN LÀ STUB (mặc định TẮT).

Lý do defer: thư viện MetaTrader5 (Python) KHÔNG expose lịch kinh tế đáng tin, và để
BACKTEST được bộ lọc tin thì cần dữ liệu SỰ KIỆN LỊCH SỬ (không chỉ realtime). Khi có
nguồn dữ liệu (CSV ForexFactory-style hoặc API), hiện thực hoá `in_news_blackout` bên dưới.

Hợp đồng hàm giữ nguyên để main.py/backtest.py gọi sẵn ngay từ bây giờ mà chưa đổi hành vi:
    in_news_blackout(ts, cfg) -> bool   # True = đang trong vùng cấm quanh tin mạnh

Định dạng CSV dự kiến khi bật (cfg.news_csv):
    time,currency,impact          # time ISO (giờ server), impact: high/medium/low
    2026-08-01 12:30,USD,high
Quy tắc dự kiến: cấm vào lệnh nếu `ts` nằm trong ±cfg.news_buffer_min phút quanh bất kỳ
sự kiện impact>=high có currency ảnh hưởng tới symbol (USD/XAU cho vàng, USD/EUR cho EURUSD).
"""
import logging

log = logging.getLogger("news")

_warned = False


def in_news_blackout(ts, cfg) -> bool:
    """STUB: trả về False (không cấm) khi tính năng TẮT. Bật bằng cfg.news_filter_enabled
    SAU khi cắm nguồn dữ liệu sự kiện. Giữ chữ ký ổn định để call-site không phải đổi."""
    if not getattr(cfg, "news_filter_enabled", False):
        return False
    global _warned
    if not _warned:
        log.warning("news_filter_enabled=True nhưng chưa cắm nguồn dữ liệu sự kiện "
                    "(news.py vẫn là stub) — tạm coi như KHÔNG có tin. Hãy hiện thực hoá "
                    "in_news_blackout() với cfg.news_csv trước khi tin cậy bộ lọc này.")
        _warned = True
    return False
