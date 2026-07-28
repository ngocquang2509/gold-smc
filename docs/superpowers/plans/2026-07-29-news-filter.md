# News Filter (#7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `news.py` stub with a real high-impact-news blackout filter, backed by a ForexFactory-sourced calendar CSV, and validate its effect via backtest-tuning before enabling it by default.

**Architecture:** A standalone CLI script (`fetch_forexfactory_calendar.py`) builds/maintains `data/news_calendar.csv` (all events, all currencies/impacts — filtering happens at read time). `news.py::in_news_blackout` loads that CSV (mtime-cached), filters to `impact == "high"` and `cfg.news_currencies`, and checks whether `ts` (naive UTC / server time) falls within `±cfg.news_buffer_min` minutes of any matching event. `config.py` gains one new field (`news_currencies`); the three existing fields (`news_filter_enabled`, `news_csv`, `news_buffer_min`) are reused unchanged. No call-site in `main.py`/`backtest.py` changes — both already call `in_news_blackout(now, cfg)`.

**Tech Stack:** Python, pandas (already a dependency), stdlib `urllib`/`json`/`argparse` for the fetch script (no new pip dependency).

**Spec:** `docs/superpowers/specs/2026-07-28-news-filter-design.md`

---

## Important project conventions (read before starting)

- **No pytest, no test suite in this repo** (see `.claude/CLAUDE.md`) — backtesting is the project's validation method. Every "test" step below is a small standalone Python script run directly, not a pytest file. Follow the same red→green discipline (run it, watch it fail for the right reason, implement, watch it pass) — just without a test framework.
- Console output must run with `PYTHONIOENCODING=utf-8` (Vietnamese text breaks cp1252 on Windows) — every `python` command below should be prefixed accordingly on Windows, e.g. `PYTHONIOENCODING=utf-8 python ...` (Bash/Git-Bash) or `$env:PYTHONIOENCODING="utf-8"; python ...` (PowerShell).
- `ts` passed into `in_news_blackout` is a **naive** `datetime`/`Timestamp` numerically equal to UTC (confirmed: `mt5_client.py:94` strips `tzinfo` after converting via UTC epoch; `backtest.py` bar timestamps come from the CSV loader, also naive). The calendar CSV must therefore store **naive, UTC-equivalent** timestamps — no timezone suffix — or every comparison silently drifts by however many hours ForexFactory's source timezone is offset from UTC.
- Do not touch `dry_run`, `magic_number` filtering, or the no-lookahead bar-slicing invariant — this feature only adds a boolean gate at the same point `in_session`/`pre_weekend_guard` already gate at.

---

### Task 1: Spike — confirm ForexFactory endpoint scope

**Files:** none (throwaway probe only)

- [ ] **Step 1: Probe the current-week endpoint**

Run:
```bash
curl -s https://nfs.faireconomy.media/ff_calendar_thisweek.json | PYTHONIOENCODING=utf-8 python -c "import json,sys; d=json.load(sys.stdin); print(len(d)); print(d[0])"
```
Expected: prints an event count and one sample event dict with keys including `title`, `country`, `date`, `impact` (capitalized: `"High"`/`"Medium"`/`"Low"`/`"Holiday"`). Record the exact key names you see — Task 4's `_events_to_df` must match them exactly.

- [ ] **Step 2: Probe `nextweek`**

Run the same curl against `https://nfs.faireconomy.media/ff_calendar_nextweek.json`. Expected: succeeds the same way, different date range.

- [ ] **Step 3: Confirm no arbitrary-historical-week parameter exists**

Try `https://nfs.faireconomy.media/ff_calendar_lastweek.json`. If it succeeds, you have exactly a 3-week rolling window (last/this/next) — no deeper history. There is no documented way to request e.g. "the week of 2024-08-01" from this endpoint.

- [ ] **Step 4: Record the decision**

**RESOLVED (spike run 2026-07-29):** `ff_calendar_nextweek.json` and `ff_calendar_lastweek.json` both return a genuine HTTP 404 — they don't exist at all. Only `ff_calendar_thisweek.json` works. So there's no 3-week rolling window, just a single current-week snapshot. `--refresh` mode (Task 4) fetches **only** `thisweek` — do not build `nextweek`/`lastweek` fetch logic, it would be dead code hitting permanent 404s. Backfilling the 2-year backtest window requires a separately-obtained historical file, ingested via a `--normalize-file` mode (also Task 4) — **you (the user) will need to source that historical file yourself** once this plan reaches Task 6. This plan does not fetch or guess a URL for that file.

Confirmed JSON shape from the live probe: keys are `title`, `country`, `date`, `impact`, `forecast`, `previous`. `country` is a 3-letter currency code (`USD`, `EUR`, `JPY`, ...), not a country name. `date` is a single combined ISO-8601 string **with a timezone offset** (e.g. `2026-07-26T19:50:00-04:00` — US/Eastern, not UTC) — Task 4's `_events_to_df` must parse this as timezone-aware and convert to naive UTC, not assume the string is already UTC. `impact` values observed: `"High"`, `"Medium"`, `"Low"` (capitalized) — `"Holiday"` didn't appear in this sample week but the mapping should still handle it defensively.

Also observed: repeated requests within ~10s of each other trigger a Cloudflare 429 with `Retry-After: 300`. Not a concern for the intended usage pattern (manual, roughly weekly), so no retry/backoff logic is being added — just don't hammer the endpoint when testing.

---

### Task 2: `config.py` — add `news_currencies`

**Files:**
- Modify: `config.py:94` (insert after `news_buffer_min`), `config.py:149-163` (XAUUSD block), `config.py:171-217` (EURUSD block)

- [ ] **Step 1: Add the field to the schema**

In `config.py`, right after line 94 (`news_buffer_min: int = 15 ...`):
```python
    news_currencies: tuple = ("USD", "XAU")  # currency filter cho blackout (mặc định vàng)
```
Note: ForexFactory has no `"XAU"` events (it only tracks fiat-country macro releases) — `"XAU"` in the tuple is a harmless no-op today, kept so a future gold-specific event source can plug in without a config change. The real filter for gold is `"USD"` (gold's dominant macro driver).

- [ ] **Step 2: Add EURUSD override**

In the `EURUSD = TradingConfig(...)` block (`config.py:171`), add:
```python
    news_currencies=("USD", "EUR"),
```
(XAUUSD needs no override — it uses the schema default.)

- [ ] **Step 3: Verify the field resolves correctly per symbol**

Run:
```bash
PYTHONIOENCODING=utf-8 python -c "
from config import get_config
g = get_config('XAUUSDm')
e = get_config('EURUSDm')
assert g.news_currencies == ('USD', 'XAU'), g.news_currencies
assert e.news_currencies == ('USD', 'EUR'), e.news_currencies
print('OK')
"
```
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "Add news_currencies config field for #7 news filter"
```

---

### Task 3: `news.py` — implement `in_news_blackout`

**Files:**
- Modify: `news.py` (whole file — replace the stub body, keep the module docstring's contract description, trim it since it's no longer a stub)

- [ ] **Step 1: Write a fixture CSV and a verification script (fails first — function still returns the old stub `False` unconditionally)**

Create a throwaway fixture (in the scratchpad dir, not the repo) — e.g. `verify_news.py`:
```python
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, r"d:\gold-smc-bot")

FIXTURE = "verify_news_calendar.csv"
with open(FIXTURE, "w", encoding="utf-8") as f:
    f.write("time,currency,impact\n")
    f.write("2026-08-01 12:30:00,USD,high\n")
    f.write("2026-08-01 09:00:00,EUR,medium\n")  # medium — must NOT trigger blackout

from config import get_config
from news import in_news_blackout

cfg = get_config("XAUUSDm")
cfg.news_filter_enabled = True
cfg.news_csv = FIXTURE
cfg.news_buffer_min = 15

# Inside the ±15min window of the USD high-impact event → True
assert in_news_blackout(datetime(2026, 8, 1, 12, 40), cfg) is True
# Outside the window → False
assert in_news_blackout(datetime(2026, 8, 1, 13, 0), cfg) is False
# Medium-impact EUR event at 09:00, even exactly on time → must NOT block (impact filter)
assert in_news_blackout(datetime(2026, 8, 1, 9, 0), cfg) is False
# Filter disabled → always False regardless of proximity
cfg.news_filter_enabled = False
assert in_news_blackout(datetime(2026, 8, 1, 12, 30), cfg) is False

print("OK")
os.remove(FIXTURE)
```

Run: `PYTHONIOENCODING=utf-8 python verify_news.py`
Expected: **fails** (`AssertionError` on the first `True` assertion, since the stub always returns `False`) — confirms the script is actually exercising the current code.

- [ ] **Step 2: Implement `news.py`**

Replace the file body (keep a short docstring describing the CSV contract; the "STUB" framing goes away):
```python
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
    return bool(((relevant["time"] - ts).abs() <= window).any())
```

- [ ] **Step 3: Run the verification script again**

Run: `PYTHONIOENCODING=utf-8 python verify_news.py`
Expected: prints `OK`, no assertion errors.

- [ ] **Step 4: Verify the fail-fast path**

```bash
PYTHONIOENCODING=utf-8 python -c "
from config import get_config
from news import in_news_blackout
from datetime import datetime
cfg = get_config('XAUUSDm')
cfg.news_filter_enabled = True
cfg.news_csv = 'does_not_exist.csv'
try:
    in_news_blackout(datetime(2026,8,1), cfg)
    print('FAIL: should have raised')
except FileNotFoundError:
    print('OK')
"
```
Expected: prints `OK`.

- [ ] **Step 5: Commit**

```bash
git add news.py
git commit -m "Implement real news blackout filter (#7), replacing the stub"
```

---

### Task 4: `fetch_forexfactory_calendar.py` — fetch + normalize CLI

**Files:**
- Create: `fetch_forexfactory_calendar.py`

- [ ] **Step 1: Write a verification script against a fixture JSON (fails first — file doesn't exist yet)**

In scratchpad, `verify_fetch.py`:
```python
import json
import os
import sys

sys.path.insert(0, r"d:\gold-smc-bot")

FIXTURE_JSON = "verify_ff_sample.json"
with open(FIXTURE_JSON, "w", encoding="utf-8") as f:
    json.dump([
        {"title": "Non-Farm Payrolls", "country": "USD", "date": "2026-08-01T12:30:00-04:00", "impact": "High"},
        {"title": "German Retail Sales", "country": "EUR", "date": "2026-08-01T06:00:00-04:00", "impact": "Medium"},
    ], f)

from fetch_forexfactory_calendar import normalize_file
import pandas as pd

OUT = "verify_news_calendar.csv"
normalize_file(FIXTURE_JSON, OUT)
df = pd.read_csv(OUT, parse_dates=["time"])
assert len(df) == 2, len(df)
assert set(df["currency"]) == {"USD", "EUR"}, df["currency"].tolist()
assert set(df["impact"]) == {"high", "medium"}, df["impact"].tolist()
# -04:00 input must convert to naive UTC: 12:30-04:00 -> 16:30 UTC
row = df[df["currency"] == "USD"].iloc[0]
assert row["time"].hour == 16 and row["time"].minute == 30, row["time"]
assert row["time"].tzinfo is None
print("OK")
os.remove(FIXTURE_JSON)
os.remove(OUT)
```

Run: `PYTHONIOENCODING=utf-8 python verify_fetch.py`
Expected: **fails** with `ModuleNotFoundError: No module named 'fetch_forexfactory_calendar'`.

- [ ] **Step 2: Implement the script**

Confirmed live (Task 1 spike, 2026-07-29): JSON key names are `title`/`country`/`date`/`impact`; `date` is timezone-aware ISO-8601 (e.g. `-04:00` offset, NOT UTC); `country` is a currency code; only `ff_calendar_thisweek.json` exists — `nextweek`/`lastweek` both 404. Build accordingly:

```python
"""
Lấy/chuẩn hoá dữ liệu lịch kinh tế cho bộ lọc tin (#7, xem news.py). Ghi TẤT CẢ
currency/impact vào CSV chuẩn `time,currency,impact` (time = ISO naive UTC) —
news.py lọc impact=high/currency lúc đọc, không lọc ở đây.

Chế độ:
    --refresh            Lấy tuần HIỆN TẠI từ ForexFactory (nfs.faireconomy.media/
                          ff_calendar_thisweek.json — endpoint duy nhất tồn tại, đã
                          xác nhận nextweek/lastweek đều 404), append + dedupe vào
                          --out. Dùng cho live (chạy định kỳ, vd mỗi CN).
    --normalize-file F    Chuẩn hoá 1 file JSON (định dạng ForexFactory) hoặc CSV đã có sẵn
                          cột time/currency/impact, dùng để BACKFILL lịch sử — endpoint FF
                          KHÔNG hỗ trợ tuần quá khứ tuỳ ý, chỉ có tuần hiện tại (xem
                          docs/superpowers/specs/2026-07-28-news-filter-design.md).
"""
import argparse
import json
import os
import urllib.request

import pandas as pd

FF_THISWEEK_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
IMPACT_MAP = {"High": "high", "Medium": "medium", "Low": "low", "Holiday": "low"}


def _fetch_json(url: str) -> list:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _events_to_df(events: list) -> pd.DataFrame:
    rows = [
        {
            "time": e["date"],
            "currency": e["country"],
            "impact": IMPACT_MAP.get(e.get("impact"), "low"),
        }
        for e in events
    ]
    df = pd.DataFrame(rows, columns=["time", "currency", "impact"])
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_localize(None)
    return df


def _merge_and_write(new_df: pd.DataFrame, out_path: str):
    if os.path.exists(out_path):
        old_df = pd.read_csv(out_path, parse_dates=["time"])
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=["time", "currency"]).sort_values("time")
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    combined.to_csv(out_path, index=False)
    print(f"Đã ghi {len(combined)} sự kiện vào {out_path} (+{len(new_df)} nạp lần này).")


def refresh(out_path: str):
    events = _fetch_json(FF_THISWEEK_URL)
    _merge_and_write(_events_to_df(events), out_path)


def normalize_file(path: str, out_path: str):
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as f:
            events = json.load(f)
        df = _events_to_df(events)
    else:
        df = pd.read_csv(path, parse_dates=["time"])
    _merge_and_write(df, out_path)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--normalize-file")
    ap.add_argument("--out", default="data/news_calendar.csv")
    args = ap.parse_args()

    if args.refresh:
        refresh(args.out)
    elif args.normalize_file:
        normalize_file(args.normalize_file, args.out)
    else:
        ap.error("Chỉ định --refresh hoặc --normalize-file")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the verification script**

Run: `PYTHONIOENCODING=utf-8 python verify_fetch.py`
Expected: prints `OK`.

- [ ] **Step 4: Manual smoke-test of `--refresh` (needs real internet — run once, by hand)**

```bash
PYTHONIOENCODING=utf-8 python fetch_forexfactory_calendar.py --refresh --out data/news_calendar_smoketest.csv
PYTHONIOENCODING=utf-8 python -c "
import pandas as pd
df = pd.read_csv('data/news_calendar_smoketest.csv', parse_dates=['time'])
print(len(df), 'events')
print(df['impact'].value_counts())
print(df['time'].min(), '->', df['time'].max())
"
rm data/news_calendar_smoketest.csv
```
Expected: a nonzero event count, a spread of impact levels, and a time range spanning roughly the current calendar week only. Delete the smoke-test file afterward — it's not the real data file. Note: don't re-run this within ~10s of Task 1's spike probes or each other — the endpoint 429s (Cloudflare `Retry-After: 300`) under rapid repeated requests.

- [ ] **Step 5: Commit**

```bash
git add fetch_forexfactory_calendar.py
git commit -m "Add ForexFactory calendar fetch/normalize CLI for news filter (#7)"
```

---

### Task 5: End-to-end wiring check (backtest, filter ON, tiny fixture)

**Files:** none permanent (throwaway fixture + scratch script)

This task exists to catch integration mistakes (e.g., a currency/impact mismatch, an off-by-timezone error) that Tasks 2-4's unit-level checks can't see, by exercising the real `backtest.py` loop.

Note: Step 2/3 re-run `backtest.py --from-mt5`, which pulls a fresh live window ending at "now" — not the static `data/xauusd_m15.csv` file Step 1 reads to pick a fixture timestamp. These two data sources cover overlapping historical ranges in practice, but if Step 3 shows no measurable trade-count drop, first confirm the fixture's chosen timestamp actually falls inside the `--from-mt5` pull's date range before concluding the wiring is broken.

- [ ] **Step 1: Build a tiny fixture calendar covering a known high-impact-news moment inside your existing cached backtest window**

```bash
PYTHONIOENCODING=utf-8 python -c "
import pandas as pd
ltf = pd.read_csv('data/xauusd_m15.csv')
ltf['time'] = pd.to_datetime(ltf['time'], unit='s') if ltf['time'].dtype != object else pd.to_datetime(ltf['time'])
mid = ltf['time'].iloc[len(ltf)//2]
pd.DataFrame([{'time': mid.strftime('%Y-%m-%d %H:%M:%S'), 'currency': 'USD', 'impact': 'high'}]).to_csv('data/news_calendar_e2e_test.csv', index=False)
print('fixture event at', mid)
"
```

- [ ] **Step 2: Run backtest once with the filter OFF, save trade count**

```bash
PYTHONIOENCODING=utf-8 python backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
```
Note the total trade count printed in the summary.

- [ ] **Step 3: Temporarily set `news_filter_enabled=True`, `news_csv='data/news_calendar_e2e_test.csv'`, `news_buffer_min=1440` (24h — deliberately huge, to make the effect unmissable) directly in `config.py`'s `XAUUSD` block, rerun**

```bash
PYTHONIOENCODING=utf-8 python backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
```
Expected: trade count is measurably lower than Step 2's (the 24h blackout window should suppress at least a few entries around the fixture's midpoint date) — confirms the gate is actually wired into the loop and having an effect, not silently a no-op.

- [ ] **Step 4: Revert the temporary config change**

```bash
git checkout config.py
rm data/news_calendar_e2e_test.csv
```
Confirm with `git status` that `config.py` shows no diff before moving on — this step must not be committed.

---

### Task 6: Backfill real historical calendar + validate via backtest-tuning

**Files:**
- Create: `data/news_calendar.csv` (real data, not a fixture)
- Modify: `config.py` (only if Task 6's tuning results justify enabling the filter by default — see decision rule below)

- [ ] **Step 1: Obtain a historical economic-calendar source covering 2024-06 → present**

This is a manual step for you (the user) — Task 1 confirmed ForexFactory's live endpoint can't be queried for arbitrary past weeks. Source a file (manual export, or a historical dataset you trust) in either ForexFactory's JSON event shape or already in the `time,currency,impact` CSV shape.

**If your source is already a CSV** (not JSON): `normalize_file`'s CSV branch does **no timezone conversion** — it trusts the `time` column as-is. Before ingesting, confirm those timestamps are already naive and UTC-equivalent (matching `server_time()`), not local-to-some-timezone. If they aren't, convert them yourself first (e.g. `pd.to_datetime(col, utc=True).dt.tz_convert('UTC').dt.tz_localize(None)` after localizing to the source's actual timezone) — otherwise every blackout check silently drifts by the offset and Task 6's tuning results will be measuring the wrong windows.

- [ ] **Step 2: Normalize it into `data/news_calendar.csv`**

```bash
PYTHONIOENCODING=utf-8 python fetch_forexfactory_calendar.py --normalize-file <your_historical_file> --out data/news_calendar.csv
```
Then sanity-check coverage:
```bash
PYTHONIOENCODING=utf-8 python -c "
import pandas as pd
df = pd.read_csv('data/news_calendar.csv', parse_dates=['time'])
print(len(df), 'events,', df['time'].min(), '->', df['time'].max())
print(df['impact'].value_counts())
"
```
Expected: time range covers at least 2024-06 through today; nonzero `high`-impact events.

- [ ] **Step 3: Set both symbols' `news_csv` to the real file for tuning (leave `news_filter_enabled=False` for now — the sweep in Step 4 controls it per-run)**

```python
# config.py, both XAUUSD and EURUSD blocks
news_csv="data/news_calendar.csv",
```

- [ ] **Step 4: Invoke the `backtest-tuning` skill**

Sweep `news_buffer_min` ∈ {10, 15, 30, 60} × `news_filter_enabled` ∈ {True, False} on both `XAUUSDm` and `EURUSDm`, comparing full-period **and** H1/H2 split PF/WR/CAGR/MaxDD (per this project's established tuning convention — see `[[backtest-tuning-2y]]`/`[[eurusd-baseline]]` memory).

- [ ] **Step 5: Apply the decision rule**

Only set `news_filter_enabled=True` (and pick the winning `news_buffer_min`) for a symbol if the sweep shows a **clear** PF/MaxDD improvement that holds up in the H2 half — not just full-period. If the effect is marginal or only helps full-period-but-not-H2, leave `news_filter_enabled=False` and record the finding (mirrors the #6 premium/discount lesson — a plausible filter that doesn't earn its keep stays off).

- [ ] **Step 6: Commit whatever the tuning run decided**

```bash
git add config.py data/news_calendar.csv
git commit -m "Backfill news calendar + enable/tune news filter based on backtest-tuning results"
```
(If the decision was "leave it off," commit just the calendar CSV backfill with a message saying so — don't flip `news_filter_enabled` if the sweep didn't justify it.)
