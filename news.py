"""
Bộ lọc TIN TỨC tác động mạnh (#7). Cấm vào lệnh MỚI trong ±cfg.news_buffer_min phút
quanh bất kỳ sự kiện impact=high nào có currency thuộc cfg.news_currencies. Lệnh
đang mở KHÔNG bị ảnh hưởng — chỉ gate ở điểm quyết định entry (main.py/backtest.py).

CSV (cfg.news_csv) chứa TẤT CẢ currency/impact (không lọc trước) — sinh bởi
fetch_forexfactory_calendar.py:
    time,currency,impact          # time: ISO, NAIVE, UTC-equivalent (khớp server_time())
    2026-08-01 12:30:00,USD,high
"""
import logging
import os

import pandas as pd

log = logging.getLogger("news")

_cache = {}  # path -> (mtime, DataFrame đã lọc impact=high)


def _load_calendar(path: str) -> pd.DataFrame:
    mtime = os.path.getmtime(path)
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    df = pd.read_csv(path, parse_dates=["time"])
    df = df[df["impact"] == "high"]
    _cache[path] = (mtime, df)
    return df


def in_news_blackout(ts, cfg) -> bool:
    """True nếu ts nằm trong vùng cấm quanh tin impact=high (currency ∈ cfg.news_currencies)."""
    if not getattr(cfg, "news_filter_enabled", False):
        return False
    if not cfg.news_csv or not os.path.exists(cfg.news_csv):
        raise FileNotFoundError(
            f"news_filter_enabled=True nhưng news_csv không tồn tại: {cfg.news_csv!r}. "
            "Chạy fetch_forexfactory_calendar.py trước khi bật bộ lọc tin."
        )
    df = _load_calendar(cfg.news_csv)
    relevant = df[df["currency"].isin(cfg.news_currencies)]
    if relevant.empty:
        return False
    window = pd.Timedelta(minutes=cfg.news_buffer_min)
    hits = relevant[(relevant["time"] - ts).abs() <= window]
    if not hits.empty:
        ev = hits.iloc[0]
        log.debug(
            f"Chặn vào lệnh (news blackout) tại ts={ts} — sự kiện {ev['currency']} "
            f"impact={ev['impact']} lúc {ev['time']} (buffer ±{cfg.news_buffer_min} phút)."
        )
        return True
    return False
