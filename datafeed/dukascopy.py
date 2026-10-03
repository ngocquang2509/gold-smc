"""
Tải nến 1 phút (BID) lịch sử từ Dukascopy datafeed công khai — nguồn lịch sử dài
(2014→nay) cho walk-forward, xem ADR 0001 (Q9: phát triển trên Dukascopy, đối chiếu Exness).

Mỗi ngày = 1 file `BID_candles_min_1.bi5` (LZMA), cache nguyên văn dưới
`data/dukascopy/<SYM>/<YYYY>/<MM>/<DD>.bi5` → chạy lại chỉ tải phần còn thiếu.
Ngày không có dữ liệu (404 / file rỗng) cache thành file rỗng để không hỏi lại.

Thời gian Dukascopy là UTC; server Exness là GMT+0 → khớp nhau, không cần dịch giờ.

    python -m datafeed.dukascopy --symbol XAUUSDm --from 2014-01-01 --to 2026-09-30
"""
import argparse
import http.client
import logging
import lzma
import ssl
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("dukascopy")

HOST = "datafeed.dukascopy.com"
PATH = "/datafeed/{inst}/{y:04d}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "dukascopy"

# Symbol broker (Exness) → (tên Dukascopy, hệ số chia giá nguyên)
INSTRUMENTS = {
    "XAUUSDm": ("XAUUSD", 1_000),
    "EURUSDm": ("EURUSD", 100_000),
    "GBPUSDm": ("GBPUSD", 100_000),
}

# Bản ghi nến: giây lệch từ 00:00 UTC, open, close, low, high (int giá), volume (float)
_REC = struct.Struct(">5if")


def _raw_path(inst: str, day: date) -> Path:
    return RAW_DIR / inst / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}.bi5"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# Mỗi luồng giữ 1 kết nối HTTPS keep-alive. Đo 2026-10-03: MỞ kết nối tới server
# Dukascopy rất chập chờn (~50% timeout, 3–25s mỗi lần) nhưng request trên kết nối đã mở
# chỉ ~0.25s → tái dùng kết nối nhanh hơn ~100 lần so với mở mới mỗi file.
_local = threading.local()

# Server bắt đầu trả 503 rồi bóp băng thông sau ~1000 request liên tục ở ~4 req/s
# (đo 2026-10-03) → giãn cách tối thiểu giữa 2 request, và khi bị 503/rớt kết nối thì
# lùi DÀI (chờ hết cửa sổ bóp) thay vì thử lại dồn dập làm bị bóp lâu hơn.
MIN_INTERVAL_S = 0.4
_pace_lock = threading.Lock()
_last_request = 0.0


def _pace() -> None:
    global _last_request
    with _pace_lock:
        wait = _last_request + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()


def _conn() -> http.client.HTTPSConnection:
    if getattr(_local, "conn", None) is None:
        _local.conn = http.client.HTTPSConnection(HOST, timeout=30, context=_ssl_context())
    return _local.conn


def _drop_conn() -> None:
    if getattr(_local, "conn", None) is not None:
        _local.conn.close()
        _local.conn = None


def _fetch_day(inst: str, day: date, retries: int = 8) -> None:
    """Tải 1 ngày vào cache (bỏ qua nếu đã có). Tháng trong URL Dukascopy đánh số từ 0."""
    path = _raw_path(inst, day)
    if path.exists():
        return
    url = PATH.format(inst=inst, y=day.year, m=day.month - 1, d=day.day)
    err = None
    for attempt in range(retries):
        _pace()
        try:
            conn = _conn()
            conn.request("GET", url)
            resp = conn.getresponse()
            body = resp.read()
            if resp.status == 404:
                body = b""
                break
            if resp.status == 200:
                break
            err = f"HTTP {resp.status}"
        except (http.client.HTTPException, OSError) as e:
            err = e
            _drop_conn()   # kết nối hỏng → lần sau mở lại
        time.sleep(min(15 * 2 ** attempt, 300))   # 15s, 30s, 60s ... tối đa 5 phút
    else:
        log.warning(f"{inst} {day}: tải thất bại sau {retries} lần ({err}) — bỏ qua, chạy lại sau")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(path)   # ghi nguyên tử: không bao giờ để lại file cache dở dang


def decode_day(inst: str, day: date, divisor: int) -> pd.DataFrame:
    """Giải mã 1 ngày từ cache → DataFrame M1. Bỏ nến volume=0 (phút không có tick —
    Dukascopy điền giá phẳng cho cả giờ đóng cửa)."""
    path = _raw_path(inst, day)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    raw = lzma.decompress(path.read_bytes())
    if not raw:
        return pd.DataFrame()
    arr = np.array(list(_REC.iter_unpack(raw)), dtype=float)
    df = pd.DataFrame(arr, columns=["sec", "open", "close", "low", "high", "volume"])
    df = df[df["volume"] > 0]
    df["time"] = pd.Timestamp(day) + pd.to_timedelta(df["sec"], unit="s")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] / divisor
    return df.set_index("time")[["open", "high", "low", "close", "volume"]]


def _days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() != 5:   # Thứ Bảy: thị trường đóng cả ngày
            yield d
        d += timedelta(days=1)


def download(symbol: str, start: date, end: date, workers: int = 1) -> None:
    inst, _ = INSTRUMENTS[symbol]
    days = [d for d in _days(start, end) if not _raw_path(inst, d).exists()]
    log.info(f"{symbol}: cần tải {len(days)} ngày ({start} → {end})")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, _ in enumerate(pool.map(lambda d: _fetch_day(inst, d), days), 1):
            if i % 250 == 0:
                log.info(f"{symbol}: {i}/{len(days)}")


def load_m1(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Ghép M1 từ cache cho khoảng ngày (không tải mạng)."""
    inst, divisor = INSTRUMENTS[symbol]
    frames = [decode_day(inst, d, divisor) for d in _days(start, end)]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    return pd.concat(frames).sort_index()


def main():
    p = argparse.ArgumentParser(description="Tải nến M1 Dukascopy vào cache cục bộ")
    p.add_argument("--symbol", required=True, choices=list(INSTRUMENTS))
    p.add_argument("--from", dest="start", default="2014-01-01")
    p.add_argument("--to", dest="end", default="2026-09-30")
    # Mặc định 1: đo 2026-10-03, 4 kết nối song song bị server bóp (mỗi luồng kẹt sau
    # 1 file), còn 1 kết nối keep-alive chạy ổn ~0.25s/file; cộng MIN_INTERVAL_S → ~2 giờ cho 3 symbol 2014→2026.
    p.add_argument("--workers", type=int, default=1)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    download(a.symbol, date.fromisoformat(a.start), date.fromisoformat(a.end), a.workers)


if __name__ == "__main__":
    main()
