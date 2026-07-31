# RSI Momentum Confluence Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional RSI-based momentum confluence filter to `strategy.analyze()` that rejects sweep+CHoCH setups where momentum never actually got exhausted and reversed, ships **off by default**, and is validated via `backtest-tuning` before anyone flips it on.

**Architecture:** New pure-function module `indicators.py` (root level, alongside `risk.py`) holds `rsi()` and `rsi_confirms()` — no state, no I/O, same shape as the `smc/*` primitives but kept separate since RSI isn't an SMC concept. `strategy.py` gets one new optional step (4a) between the existing CHoCH confirmation (step 4) and the setup-age cap (step 4b), gated by a new `cfg.rsi_filter_enabled` flag. `config.py` gets 4 new fields (all off/neutral by default) next to the existing `#6`/`#9` optional-filter fields.

**Tech Stack:** Python 3.12, pandas 2.3, numpy 2.2. No pytest in this repo (`CLAUDE.md`: "There is no test suite, linter, or build step" — backtesting is the validation method). Every "test" step below is a **standalone script run directly with `python`**, not committed, following the exact convention already used in `docs/superpowers/plans/2026-07-28-*` and `2026-07-30-*` plans (see e.g. their "no pytest" notes). Red→green discipline still applies: run it, watch it fail for the right reason, implement, watch it pass.

**Spec:** `docs/superpowers/specs/2026-07-31-rsi-momentum-filter-design.md`

---

## Context for the implementer

Read `d:\gold-smc-bot\CLAUDE.md` first — especially "Entry sequence" and "Critical conventions" (no-lookahead rule). Then read `d:\gold-smc-bot\strategy.py` end to end (194 lines, short) and `d:\gold-smc-bot\config.py` lines 1-105 before touching anything. The spec at the path above has already been through 3 rounds of adversarial review — read it fully; it pins down two subtle bugs (self-comparison when there's no sweep, and a pandas label-vs-position trap with `idxmin`/`idxmax`) that this plan's code snippets already avoid. Don't "simplify" the `rsi_confirms` window/extreme-finding logic back toward something that looks cleaner — the spec explains why the obvious-looking alternatives are wrong.

Key facts you'll rely on repeatedly:
- `ltf_df` has a `DatetimeIndex` (set in `mt5_client.get_rates`), but every `.index` field on `Swing`/`StructureEvent`/`SweepEvent` dataclasses is a **positional** int (built via `enumerate` inside `smc/*`). Never mix the two. Always use `.iloc[...]`, never `.loc[...]` or bare `series[label]`.
- `strategy.analyze()` at `d:\gold-smc-bot\strategy.py:26-163` is the single source of truth for both live and backtest signals — don't fork logic.
- Cached offline data for smoke-testing without MT5/Windows-login lives at `d:\gold-smc-bot\data\xauusd_m15.csv`, `xauusd_h4.csv`, `eurusd_m15.csv`, `eurusd_h4.csv` (~2 years, current to 2026-07-23). No GBPUSD CSV exists yet — GBPUSD validation later needs `--from-mt5`.

---

### Task 1: `indicators.py` — `rsi()` function

**Files:**
- Create: `d:\gold-smc-bot\indicators.py`

- [ ] **Step 1: Write the failing verification script**

Run this directly (it will fail with `ModuleNotFoundError` or `ImportError` since `indicators.py` doesn't exist yet):

```bash
python - <<'EOF'
import pandas as pd
import numpy as np
from indicators import rsi

period = 14

# 1. Monotonically rising prices -> RSI should approach 100 (no losses at all)
rising = pd.Series(np.arange(1, 40, dtype=float))
r_rising = rsi(rising, period)
assert r_rising.iloc[-1] > 95, f"expected near-100 RSI on pure uptrend, got {r_rising.iloc[-1]}"

# 2. Monotonically falling prices -> RSI should approach 0 (no gains at all)
falling = pd.Series(np.arange(40, 1, -1, dtype=float))
r_falling = rsi(falling, period)
assert r_falling.iloc[-1] < 5, f"expected near-0 RSI on pure downtrend, got {r_falling.iloc[-1]}"

# 3. First `period` entries must be NaN (RSI needs warm-up) -- this is the
#    invariant rsi_confirms()'s "insufficient history" fail-safe depends on.
assert r_rising.iloc[:period].isna().all(), "expected NaN warm-up region"
assert pd.notna(r_rising.iloc[period]), "expected RSI to be defined right after warm-up"

# 4. Bounded 0-100 everywhere it's defined
valid = r_rising.dropna()
assert ((valid >= 0) & (valid <= 100)).all(), "RSI out of [0, 100] bounds"

print("OK")
EOF
```

Expected: `ModuleNotFoundError: No module named 'indicators'`.

- [ ] **Step 2: Implement `rsi()`**

Write `d:\gold-smc-bot\indicators.py`:

```python
"""Chỉ báo momentum độc lập (không thuộc smc/), dùng làm confluence filter
cho strategy.analyze(). Hàm thuần, không trạng thái, không I/O — giống quy
ước của smc/*, nhưng RSI không phải khái niệm SMC nên đặt riêng ở đây.
"""
import pandas as pd


def rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI kiểu Wilder smoothing. `period` phần tử đầu là NaN (warm-up)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-12)
    return 100 - (100 / (1 + rs))
```

- [ ] **Step 3: Run the verification script again**

Same command as Step 1.
Expected: prints `OK`, no assertion errors.

- [ ] **Step 4: Commit**

```bash
git add indicators.py
git commit -m "Add rsi() momentum indicator"
```

---

### Task 2: `indicators.py` — `rsi_confirms()` function

**Files:**
- Modify: `d:\gold-smc-bot\indicators.py`

This is the function with the two previously-found bugs already designed out (see spec). Re-derive it carefully rather than from memory of "how RSI filters usually work."

- [ ] **Step 1: Write the failing verification script**

```bash
python - <<'EOF'
import pandas as pd
import numpy as np
from indicators import rsi_confirms
from config import get_config

cfg = get_config("XAUUSDm")
cfg.rsi_period = 14
cfg.rsi_oversold = 30.0
cfg.rsi_overbought = 70.0

def make_df(closes):
    return pd.DataFrame({"close": closes})

# Scenario A: bullish -- price falls hard (RSI plunges into oversold) then
# recovers by the confirm bar. sweep_index marks the low, confirm_index is
# a few bars later where price has bounced.
falling = list(np.linspace(100, 60, 20))          # drives RSI down into oversold
bouncing = list(np.linspace(60, 75, 6))            # recovers by confirm bar
closes_a = falling + bouncing
df_a = make_df(closes_a)
sweep_idx_a = 19          # the low point (end of the falling leg)
confirm_idx_a = len(closes_a) - 1
assert rsi_confirms(df_a, cfg, "bullish", confirm_idx_a, sweep_idx_a) is True, \
    "expected True: RSI oversold at sweep, recovered by confirm"

# Scenario B: bearish, mirror of A.
rising = list(np.linspace(60, 100, 20))
dropping = list(np.linspace(100, 85, 6))
closes_b = rising + dropping
df_b = make_df(closes_b)
sweep_idx_b = 19
confirm_idx_b = len(closes_b) - 1
assert rsi_confirms(df_b, cfg, "bearish", confirm_idx_b, sweep_idx_b) is True, \
    "expected True: RSI overbought at sweep, dropped by confirm"

# Scenario C: insufficient history -- confirm_index near the very start of
# the series, not enough bars before it to even warm up RSI.
short_df = make_df(list(np.linspace(100, 60, 5)))
assert rsi_confirms(short_df, cfg, "bullish", 4, 2) is False, \
    "expected False (fail-safe): not enough history for RSI warm-up"

# Scenario D: no sweep (sweep_index=None) -- falls back to a rsi_period-bar
# lookback window instead of degenerating to a 1-bar window. IMPORTANT: the
# no-sweep fallback is `start_index = confirm_index - rsi_period`, and the
# warm-up guard requires `start_index >= rsi_period` -- so this path needs
# confirm_index >= 2*rsi_period (28, here) of total history. Scenario A's
# shape (26 bars) is NOT long enough for this path; use a longer series with
# a mild lead-in so the fall+bounce lands exactly inside the lookback window.
lead = list(np.linspace(50, 55, 14))     # mild rise, keeps RSI moderate (not pinned near 100)
fall = list(np.linspace(55, 20, 10))     # sharp fall -> pushes RSI into oversold
bounce = list(np.linspace(20, 35, 5))    # bounce -> RSI ticks back up
closes_d = lead + fall + bounce           # 29 bars total, confirm_index=28, start_index=14
df_d = make_df(closes_d)
confirm_idx_d = len(closes_d) - 1
result_d = rsi_confirms(df_d, cfg, "bullish", confirm_idx_d, None)
assert result_d is True, \
    "expected True: lookback window should find the oversold dip+bounce without an explicit sweep"

# Scenario E: flat/noisy prices, no real exhaustion+reversal -- must reject.
flat = list(100 + np.sin(np.linspace(0, 3, 26)))   # mild wobble, never near 30/70
df_e = make_df(flat)
assert rsi_confirms(df_e, cfg, "bullish", 25, 10) is False, \
    "expected False: no real oversold exhaustion happened"

print("OK")
EOF
```

Expected: `ImportError: cannot import name 'rsi_confirms'`.

(This exact test data has already been hand-verified against the implementation below during plan-writing — all 5 assertions hold. Don't "fix" Scenario D by tuning amplitude if it ever fails again; the fix is total series length relative to `2 * rsi_period`, not the slope.)

- [ ] **Step 2: Implement `rsi_confirms()`**

Append to `d:\gold-smc-bot\indicators.py`:

```python
def rsi_confirms(ltf_df: pd.DataFrame, cfg, trend: str,
                  confirm_index: int, sweep_index: int | None) -> bool:
    """True nếu momentum đã kiệt sức (chạm oversold/overbought) trong cửa sổ
    trước confirm_index rồi đảo chiều tới lúc đó.

    Mọi chỉ mục là VỊ TRÍ (positional), không phải label của DatetimeIndex.
    sweep_index=None (require_sweep=False) -> dùng cửa sổ lùi lại
    cfg.rsi_period nến làm mốc bắt đầu.
    """
    s = rsi(ltf_df["close"], cfg.rsi_period)

    start_index = sweep_index if sweep_index is not None else max(0, confirm_index - cfg.rsi_period)
    if start_index < cfg.rsi_period:
        return False  # RSI vẫn NaN (warm-up) tại start_index -- không đủ dữ liệu

    window = s.iloc[start_index:confirm_index + 1].to_numpy()

    if trend == "bullish":
        extreme_pos = start_index + int(window.argmin())
        return bool(s.iloc[extreme_pos] <= cfg.rsi_oversold and s.iloc[confirm_index] > s.iloc[extreme_pos])
    else:
        extreme_pos = start_index + int(window.argmax())
        return bool(s.iloc[extreme_pos] >= cfg.rsi_overbought and s.iloc[confirm_index] < s.iloc[extreme_pos])
```

Note the explicit `bool(...)` wrapping the final return in both branches —
**do not drop this.** Verified during plan-writing: without it, the
comparisons on `pd.Series.iloc[...]` values produce `numpy.bool_`, not
Python `bool`. `numpy.bool_(True) is True` evaluates to `False` (identity
check fails even though the value prints as `True`) — this would silently
break every `is True`/`is False` assertion in this task's own verification
script below (they'd all fail with the value visibly `True`/`False` on
screen, which is a confusing failure to debug blind).

- [ ] **Step 3: Run the verification script again**

Same command as Step 1.
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add indicators.py
git commit -m "Add rsi_confirms() sweep-to-CHoCH momentum confluence check"
```

---

### Task 3: `config.py` — new fields

**Files:**
- Modify: `d:\gold-smc-bot\config.py:72-73` (right after the `#6` `require_discount_premium`/`eq_range_swings` block, before the `#8` comment at line 74)

- [ ] **Step 1: Write the failing verification script**

```bash
python - <<'EOF'
from config import get_config

for name in ("XAUUSDm", "EURUSDm", "GBPUSDm"):
    cfg = get_config(name)
    assert cfg.rsi_filter_enabled is False, f"{name}: expected off by default"
    assert cfg.rsi_period == 14, f"{name}: expected default period 14"
    assert cfg.rsi_oversold == 30.0, f"{name}: expected default oversold 30.0"
    assert cfg.rsi_overbought == 70.0, f"{name}: expected default overbought 70.0"

print("OK")
EOF
```

Expected: `AttributeError: 'TradingConfig' object has no attribute 'rsi_filter_enabled'`.

- [ ] **Step 2: Add the fields**

In `d:\gold-smc-bot\config.py`, insert right after the existing `eq_range_swings: int = 6` line (end of the `#6` block, before the `#8` comment):

```python
    # #12 — Lọc confluence RSI: chỉ vào lệnh nếu momentum đã kiệt sức tại điểm
    #   sweep (RSI chạm vùng oversold/overbought) rồi đảo chiều tới lúc CHoCH xác
    #   nhận. Lọc các setup cấu trúc "giả" (sweep không có momentum kiệt sức thật).
    #   MẶC ĐỊNH TẮT — cần backtest-tuning đo trước khi bật, theo đúng cách #6 đã làm.
    rsi_filter_enabled: bool = False
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
```

- [ ] **Step 3: Run the verification script again**

Same command as Step 1.
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "Add rsi_filter_enabled config fields (off by default)"
```

---

### Task 4: `strategy.py` — integrate step 4a

**Files:**
- Modify: `d:\gold-smc-bot\strategy.py:17-21` (imports), `strategy.py:55-58` (insert new step between existing step 4 and step 4b)

- [ ] **Step 1: Write the failing regression + gating script**

This uses a real signal from cached data as a fixture, then monkeypatches
`strategy.rsi_confirms` (the name bound into `strategy`'s namespace by the
`from indicators import rsi_confirms` import) to deterministically prove
three things without depending on marginal real RSI values: (a) with the flag
off, `rsi_confirms` is never even called (zero behavior change, the default),
(b) with the flag on and confluence forced to fail, the setup is rejected
(proves the gate is wired), (c) with the flag on and confluence forced to
pass, the exact same plan comes out as the baseline (proves the gate doesn't
otherwise alter the signal).

```bash
python - <<'EOF'
import pandas as pd
from config import get_config
import strategy

htf = pd.read_csv("data/xauusd_h4.csv", parse_dates=["time"]).set_index("time")
ltf = pd.read_csv("data/xauusd_m15.csv", parse_dates=["time"]).set_index("time")
symbol_info = {"contract_size": 100, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 50.0}

cfg = get_config("XAUUSDm")

# Find the first bar that already produces a signal with the flag off
# (current default) -- this becomes the fixture for the rest of the script.
# (Verified during plan-writing: XAUUSDm cached data produces one at i=896.)
i_star, baseline_plan = None, None
for i in range(100, len(ltf)):
    htf_slice = htf[htf.index <= ltf.index[i]]
    if len(htf_slice) < 50:
        continue
    plan = strategy.analyze(htf_slice, ltf.iloc[:i], cfg, balance=10000, symbol_info=symbol_info)
    if plan is not None:
        i_star, baseline_plan = i, plan
        break
assert i_star is not None, "fixture not found -- widen the scan range"
htf_star, ltf_star = htf[htf.index <= ltf.index[i_star]], ltf.iloc[:i_star]

# (a) Flag OFF: rsi_confirms must not be consulted at all.
call_count = 0
def counting_false_stub(*a, **k):
    global call_count
    call_count += 1
    return False
original = strategy.rsi_confirms   # <-- fails with AttributeError until Task 4 Step 2 is done
strategy.rsi_confirms = counting_false_stub
plan_off = strategy.analyze(htf_star, ltf_star, cfg, balance=10000, symbol_info=symbol_info)
assert call_count == 0, "rsi_confirms must not be called when rsi_filter_enabled=False"
assert plan_off is not None, "flag off must reproduce the baseline signal"

# (b) Flag ON + confluence forced False -> must reject.
cfg_on = get_config("XAUUSDm")
cfg_on.rsi_filter_enabled = True
plan_reject = strategy.analyze(htf_star, ltf_star, cfg_on, balance=10000, symbol_info=symbol_info)
assert plan_reject is None, "flag on + rsi_confirms=False must reject the setup"

# (c) Flag ON + confluence forced True -> must reproduce the same plan.
strategy.rsi_confirms = lambda *a, **k: True
plan_accept = strategy.analyze(htf_star, ltf_star, cfg_on, balance=10000, symbol_info=symbol_info)
strategy.rsi_confirms = original
assert plan_accept is not None
assert (plan_accept.direction, plan_accept.entry) == (baseline_plan.direction, baseline_plan.entry), \
    "flag on + rsi_confirms=True must not otherwise change the signal"

print("OK")
EOF
```

Expected: **`AttributeError: module 'strategy' has no attribute 'rsi_confirms'`** at the `original = strategy.rsi_confirms` line — a genuine red state (confirmed by hand during plan-writing against the current, unmodified `strategy.py`).

- [ ] **Step 2: Wire up the import and the new step**

In `d:\gold-smc-bot\strategy.py`, add to the imports (near line 20, alongside the other `smc`/`risk` imports):

```python
from indicators import rsi_confirms
```

Then insert the new step between the existing step 4 (ends at `confirm is None: return None`, around line 56) and step 4b (`#9 Trần tuổi CẢ CHUỖI`, around line 58):

```python
    # ── 4a. RSI momentum confluence (tùy chọn, mặc định tắt) ────
    if cfg.rsi_filter_enabled:
        sweep_idx = last_sweep.index if last_sweep else None
        if not rsi_confirms(ltf_df, cfg, trend, confirm.index, sweep_idx):
            log.debug("RSI không xác nhận momentum kiệt sức trước điểm confirm — bỏ.")
            return None
```

- [ ] **Step 3: Run the script again**

Same command as Step 1.
Expected: prints `OK`.

- [ ] **Step 4: Commit**

```bash
git add strategy.py
git commit -m "Wire rsi_filter_enabled into strategy.analyze() as step 4a"
```

---

### Task 5: Validate via `backtest-tuning` (baseline vs enabled)

This is not a TDD step — it's the actual decision-making validation the spec requires before anyone enables the filter for real trading. Follow the `backtest-tuning` skill's checklist exactly.

**Files:** none (config toggled ad-hoc during comparison runs, not committed unless the user decides to enable it for a symbol).

- [ ] **Step 1: Baseline run (flag off, current committed state)** for XAUUSDm and EURUSDm using cached CSV data:

```bash
python backtest.py --csv-ltf data/xauusd_m15.csv --csv-htf data/xauusd_h4.csv --symbol XAUUSDm --balance 10000
python backtest.py --csv-ltf data/eurusd_m15.csv --csv-htf data/eurusd_h4.csv --symbol EURUSDm --balance 10000
```

Record PF / winrate / CAGR / trade count / max DD for full period and H1/H2 halves (per the `backtest-tuning` skill's format).

- [ ] **Step 2: Enabled run** — temporarily set `rsi_filter_enabled: bool = True` in `config.py` (both the dataclass default and/or per-symbol override, whichever the `backtest-tuning` skill convention uses), rerun the same two commands, record the same metrics.

- [ ] **Step 3: GBPUSDm** — no cached CSV exists yet for this symbol. Run via `--from-mt5` (requires Windows + MT5 terminal logged in with Algo Trading enabled):

```bash
python backtest.py --from-mt5 --symbol GBPUSDm --years 2 --balance 10000
```
repeated once with the flag off and once on. If MT5 isn't reachable in this environment, note that GBPUSDm validation is deferred and flag it explicitly rather than skipping silently.

- [ ] **Step 4: Decide** — per the spec: keep `rsi_filter_enabled=True` (as new per-symbol default) only where the enabled run's full-period AND H1/H2 metrics are genuinely better (not just full-period, to catch decayed/overfit edges — see the `nine-fixes-2026-07` memory precedent with EURUSD). Otherwise leave it off and record why in a follow-up memory/commit note.

- [ ] **Step 5: Revert the ad-hoc `config.py` toggle from Step 2** if the decision is "not adopted," so the repo returns to `rsi_filter_enabled=False` everywhere. If adopted for some symbols, commit that as its own change with a clear message citing the measured PF/winrate/CAGR deltas.

---

## Notes for whoever executes this

- Tasks 1-4 are pure-logic TDD with zero MT5 dependency — runnable on any machine with Python + pandas/numpy installed, no MT5 terminal needed.
- Task 5 needs the cached CSVs (already present for gold/EUR) and, for GBPUSDm, a live MT5 login on Windows.
- Don't skip re-deriving the `rsi_confirms` window logic from the spec's reasoning — it looks over-engineered for what sounds like "just check RSI at the sweep," but the straightforward version has already been shown (twice, in spec review) to have real bugs.
