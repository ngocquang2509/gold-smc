"""
Dữ liệu phụ cho Candidate (ADR 0003): chuỗi KHÔNG nằm trên bar của symbol đang giao dịch.

Mỗi chuỗi là các dòng `(observation, value, available_at, source)`:
  observation   thời điểm giá trị mô tả (ngày quan sát / giờ mở bar)
  available_at  thời điểm (UTC naive, = giờ server GMT+0) giá trị được CÔNG BỐ — mốc nhân quả
Căn vào bar theo giờ ĐÓNG: hàng i chỉ thấy giá trị có available_at ≤ close(i), và lấy giá
trị THẬT công bố gần nhất. Công bố thiếu ("." của FRED, ngày lễ, bản tin không ra) không
bao giờ tạo ra giá trị — không nội suy, không giả vờ đã công bố.

Nguồn (ADR 0003):
  USD5   rổ hình học đều EUR, JPY, GBP, CAD, CHF so với USD, từ bar Dukascopy; công bố = giờ
         đóng bar. TĂNG khi USD mạnh. Không phải ICE DXY (không dùng trọng số/tên ICE).
  DGS2, DGS10, DFII10   lợi suất H.15, giá trị CÔNG BỐ LẦN ĐẦU từ ALFRED (FRED API,
         output_type=4). Giá trị ngày D biết từ ngày làm việc Mỹ kế tiếp 16:15 ET (giờ
         H.15), hoặc muộn hơn nếu vintage ALFRED muộn hơn. Key chỉ lấy từ env FRED_API_KEY.

Holdout: quan sát từ HOLDOUT_START bị ẩn trừ khi include_holdout=True (như load_bars).

    python -m datafeed.auxdata --fetch DGS2 DGS10 DFII10    # cần env FRED_API_KEY
"""
import argparse
import json
import logging
import os
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar
from pandas.tseries.offsets import CustomBusinessDay

from datafeed.bars import HOLDOUT_START, TIMEFRAMES, load_bars

log = logging.getLogger("auxdata")

STORE_DIR = Path(__file__).resolve().parent.parent / "data" / "auxdata"
FRED_SERIES = ("DGS2", "DGS10", "DFII10")
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_FROM = "2013-01-01"      # đủ ấm chỉ báo trước 2014
H15_TIME = pd.Timedelta(hours=16, minutes=15)
US_BDAY = CustomBusinessDay(calendar=USFederalHolidayCalendar())
USD5_PAIRS = ("EURUSDm", "GBPUSDm", "USDJPY", "USDCAD", "USDCHF")


def _et_to_utc(local: pd.Timestamp) -> pd.Timestamp:
    return local.tz_localize("America/New_York").tz_convert("UTC").tz_localize(None)


def h15_available_at(day: pd.Timestamp) -> pd.Timestamp:
    """Giá trị H.15 của ngày D công bố lúc 16:15 ET ngày làm việc Mỹ kế tiếp (UTC)."""
    return _et_to_utc(pd.Timestamp(day).normalize() + US_BDAY + H15_TIME)


# ── Kho dòng ─────────────────────────────────────────────
def hide_holdout(rows: pd.DataFrame, include_holdout: bool) -> pd.DataFrame:
    return rows if include_holdout else rows[rows["observation"] < HOLDOUT_START]


def align(rows: pd.DataFrame, index: pd.DatetimeIndex, timeframe: str) -> pd.Series:
    """Giá trị công bố gần nhất có available_at ≤ giờ đóng mỗi bar (NaN nếu chưa có)."""
    r = rows.dropna(subset=["value"]).sort_values("observation")
    avail = r["available_at"].to_numpy("datetime64[ns]")
    if len(avail) and (np.diff(avail) < np.timedelta64(0)).any():
        raise ValueError("available_at đi lùi theo observation — kho dữ liệu phụ hỏng")
    close = (index + pd.Timedelta(TIMEFRAMES[timeframe])).to_numpy("datetime64[ns]")
    k = np.searchsorted(avail, close, side="right") - 1
    vals = r["value"].to_numpy(float)
    return pd.Series(np.where(k >= 0, vals[np.maximum(k, 0)], np.nan), index=index)


# ── USD5 ──────────────────────────────────────────────────
def usd5_index(eurusd, gbpusd, usdjpy, usdcad, usdchf) -> pd.Series:
    """Rổ hình học đều: USD5 = Π(USD trên 1 đơn vị ngoại tệ)^(-1/5). Tăng khi USD mạnh."""
    usd_per = [np.log(eurusd), np.log(gbpusd), -np.log(usdjpy), -np.log(usdcad), -np.log(usdchf)]
    return np.exp(-sum(usd_per) / 5)


def usd5_rows(timeframe: str, include_holdout: bool = False) -> pd.DataFrame:
    closes = pd.concat({p: load_bars(p, timeframe, include_holdout)["close"] for p in USD5_PAIRS}, axis=1)
    closes = closes.sort_index().ffill().dropna()   # close gần nhất ĐÃ BIẾT của từng cặp
    v = usd5_index(*(closes[p] for p in USD5_PAIRS))
    bar = pd.Timedelta(TIMEFRAMES[timeframe])
    return pd.DataFrame({"observation": closes.index, "value": v.to_numpy(),
                         "available_at": closes.index + bar, "source": f"dukascopy:USD5:{timeframe}"})


# ── FRED / ALFRED ─────────────────────────────────────────
def parse_fred(payload: dict, series: str) -> pd.DataFrame:
    """JSON output_type=4 (lần công bố đầu) → dòng kho."""
    obs = payload["observations"]
    day = pd.to_datetime([o["date"] for o in obs])
    vintage = pd.to_datetime([o["realtime_start"] for o in obs])
    value = [float(o["value"]) if o["value"] not in (".", "") else np.nan for o in obs]
    rule = [h15_available_at(d) for d in day]
    seen = [_et_to_utc(v + H15_TIME) for v in vintage]
    return pd.DataFrame({"observation": day, "value": value,
                         "available_at": [max(a, b) for a, b in zip(rule, seen)],
                         "source": f"alfred:{series}"})


def fetch_fred(series: str, api_key: str) -> pd.DataFrame:
    q = urllib.parse.urlencode({"series_id": series, "api_key": api_key, "file_type": "json",
                                "output_type": 4, "realtime_start": "1776-07-04",
                                "realtime_end": "9999-12-31", "observation_start": FRED_FROM})
    with urllib.request.urlopen(f"{FRED_URL}?{q}", timeout=60) as resp:
        payload = json.load(resp)
    if len(payload["observations"]) >= payload.get("limit", 100_000):
        raise RuntimeError(f"{series}: chạm giới hạn 1 request — cần phân trang")
    return parse_fred(payload, series)


def _store_path(series: str) -> Path:
    return STORE_DIR / f"{series}.csv"


@lru_cache(maxsize=None)
def _fred_rows(series: str) -> pd.DataFrame:
    path = _store_path(series)
    if not path.exists():
        raise FileNotFoundError(f"Chưa có {path} — chạy `python -m datafeed.auxdata --fetch {series}`")
    return pd.read_csv(path, parse_dates=["observation", "available_at"])


@lru_cache(maxsize=None)
def _usd5_cached(timeframe: str, include_holdout: bool) -> pd.DataFrame:
    return usd5_rows(timeframe, include_holdout)


def rows_for(name: str, timeframe: str, include_holdout: bool = False) -> pd.DataFrame:
    if name == "USD5":
        rows = _usd5_cached(timeframe, include_holdout)
    elif name in FRED_SERIES:
        rows = _fred_rows(name)
    else:
        raise ValueError(f"Chuỗi phụ '{name}' không có (ADR 0003: USD5, {', '.join(FRED_SERIES)})")
    return hide_holdout(rows, include_holdout)


def build_aux(names, index: pd.DatetimeIndex, timeframe: str, include_holdout: bool = False) -> pd.DataFrame:
    """DataFrame (index = bar của Candidate, cột = tên chuỗi) căn theo giờ đóng bar."""
    return pd.DataFrame({n: align(rows_for(n, timeframe, include_holdout), index, timeframe)
                         for n in names}, index=index)


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Tải chuỗi phụ (ALFRED lần công bố đầu) vào data/auxdata/")
    p.add_argument("--fetch", nargs="+", choices=FRED_SERIES, required=True)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise SystemExit("Thiếu env FRED_API_KEY (key miễn phí tại fred.stlouisfed.org)")
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    for s in a.fetch:
        rows = fetch_fred(s, key)
        rows.to_csv(_store_path(s), index=False)
        log.info(f"{s}: {len(rows)} dòng, {rows['value'].isna().sum()} không công bố, "
                 f"{rows['observation'].min().date()} → {rows['observation'].max().date()}")


if __name__ == "__main__":
    main()
