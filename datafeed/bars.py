"""
Nguồn bar DUY NHẤT cho nghiên cứu/backtest: M1 Dukascopy → M15/H1/H4/D1.

Chốt Final Holdout NẰM Ở ĐÂY (tầng dữ liệu), không ở harness: `load_bars` cắt bỏ mọi
bar từ HOLDOUT_START trở đi trừ khi gọi tường minh `include_holdout=True` — việc đó
chỉ được làm ĐÚNG 1 LẦN cho Candidate đã qua walk-forward (ADR 0001, GLOSSARY.md).

Bar resample neo theo 00:00 UTC (= giờ server Exness GMT+0) nên H4 khớp nến H4 của MT5.
Kết quả resample cache ở `data/bars/<SYM>_<TF>.csv.gz` (xóa file để build lại).

    python -m datafeed.bars --symbol XAUUSDm      # build cache M15/H1/H4/D1 từ M1 đã tải
"""
import argparse
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from datafeed.dukascopy import INSTRUMENTS, load_m1

log = logging.getLogger("bars")

DATA_START = date(2014, 1, 1)
HOLDOUT_START = pd.Timestamp("2025-10-01")   # Final Holdout: Oct 2025 → Sep 2026
DATA_END = date(2026, 9, 30)

BARS_DIR = Path(__file__).resolve().parent.parent / "data" / "bars"
TIMEFRAMES = {"M15": "15min", "H1": "1h", "H4": "4h", "D1": "1D"}


def _resample(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = m1.resample(rule, origin="start_day", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])   # khung không có tick nào (cuối tuần/lễ) → bỏ


def build(symbol: str) -> None:
    """Resample toàn bộ M1 đã tải thành mọi khung, ghi cache (gồm cả holdout — chỉ
    load_bars mới quyết định trả ra phần nào)."""
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    frames = {tf: [] for tf in TIMEFRAMES}
    # Xử lý từng năm để không giữ ~4.5 triệu nến M1 trong RAM cùng lúc. Ranh giới năm
    # là 00:00 UTC 1/1 — trùng ranh giới mọi khung (kể cả H4/D1) nên không cắt đôi bar.
    for year in range(DATA_START.year, DATA_END.year + 1):
        m1 = load_m1(symbol, max(DATA_START, date(year, 1, 1)), min(DATA_END, date(year, 12, 31)))
        if m1.empty:
            log.warning(f"{symbol} {year}: không có M1 trong cache — đã tải chưa?")
            continue
        for tf, rule in TIMEFRAMES.items():
            frames[tf].append(_resample(m1, rule))
        log.info(f"{symbol} {year}: {len(m1)} nến M1")
    for tf, parts in frames.items():
        if parts:
            pd.concat(parts).to_csv(_cache_path(symbol, tf))


def _cache_path(symbol: str, tf: str) -> Path:
    return BARS_DIR / f"{symbol}_{tf}.csv.gz"


def load_bars(symbol: str, timeframe: str, include_holdout: bool = False) -> pd.DataFrame:
    """Bar OHLCV (index = thời gian mở bar, UTC). Mặc định KHÔNG gồm Final Holdout."""
    if symbol not in INSTRUMENTS:
        raise ValueError(f"Symbol '{symbol}' không có trong INSTRUMENTS: {list(INSTRUMENTS)}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Khung '{timeframe}' không hỗ trợ: {list(TIMEFRAMES)}")
    path = _cache_path(symbol, timeframe)
    if not path.exists():
        raise FileNotFoundError(f"Chưa có {path} — chạy `python -m datafeed.bars --symbol {symbol}`")
    df = pd.read_csv(path, index_col="time", parse_dates=["time"])
    if not include_holdout:
        # Cắt theo thời điểm ĐÓNG bar: bar nào chạm sang holdout cũng bị loại.
        bar_len = pd.Timedelta(TIMEFRAMES[timeframe])
        df = df[df.index + bar_len <= HOLDOUT_START]
    return df


def main():
    p = argparse.ArgumentParser(description="Build cache bar M15/H1/H4/D1 từ M1 Dukascopy")
    p.add_argument("--symbol", required=True, choices=list(INSTRUMENTS))
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build(a.symbol)


if __name__ == "__main__":
    main()
