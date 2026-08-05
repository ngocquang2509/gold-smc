# ADX Trend-Strength Gate + ATR-Regime Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two independent, off-by-default optional gate filters to `strategy.analyze()` — an ADX trend-strength gate and an ATR-regime (volatility percentile) gate, both evaluated on the LTF (M15) dataframe at the CHoCH/BOS confirmation bar — so the SMC entry sequence can later be validated (via `backtest-tuning`) for whether these independent, non-swing-based signals improve setup quality.

**Architecture:** One new file `indicators.py` (root, alongside `risk.py`) holds three pure functions (`atr()`, `adx()`, `atr_percentile()`), following the exact Wilder-smoothing style already established in `scalp_strategy.py`'s `_atr()`/`_rsi()` helpers. `config.py` gets two new optional filter blocks (`#12`/`#13`) on `TradingConfig`, both default `False`. `strategy.py` gets two new gate steps (4a, 4a2) inserted between the existing step 4 (CHoCH/BOS confirm) and step 4b (`max_setup_age_bars`), evaluating `.iloc[confirm.index]` on the new indicator series.

**Tech Stack:** Python, pandas 2.3+ (uses `Rolling.rank(pct=True)`, confirmed available) — no new dependency.

**No test suite in this repo** (per `CLAUDE.md`: "There is no test suite, linter, or build step. Backtesting IS the validation workflow"). This plan follows the same convention already used in `docs/superpowers/plans/2026-07-31-scalp-m5-plan.md`: small throwaway sanity scripts (via `python -c` heredoc, NOT committed) to check pure functions against synthetic data, followed by a real backtest smoke run as the final validation gate.

**Spec:** `docs/superpowers/specs/2026-08-01-adx-atr-regime-filter-design.md` — read it first; this plan implements it, with one refinement made during planning (documented in Task 1): `atr()`/`adx()` explicitly mask their warmup rows to `NaN` (matching the spec's documented NaN-fail-safe behavior) rather than relying on `ewm()`'s default behavior, which does NOT produce leading NaNs and would silently defeat the `pd.isna()` guards described in the spec.

**Also in scope (per spec, "ngoài phạm vi" note on branch cleanup):** deleting the stale, unmerged, never-backtested `rsi-momentum-filter` git branch — the user explicitly authorized this during brainstorming (chose "Xoá bỏ nhánh RSI, thay bằng ADX").

---

### Task 1: `indicators.py` — `atr()`, `adx()`, `atr_percentile()`

**Files:**
- Create: `d:\gold-smc-bot\indicators.py`

- [ ] **Step 1: Write the file**

```python
"""
Chỉ báo kỹ thuật độc lập (không phải khái niệm SMC) — dùng cho các gate tuỳ
chọn trong strategy.analyze() (ADX trend-strength, ATR-regime). Xem
docs/superpowers/specs/2026-08-01-adx-atr-regime-filter-design.md.

Mọi hàm đều thuần (không trạng thái, không I/O), nhận `df` đã được caller cắt
tới hiện tại (giống mọi module smc/*) — không có rủi ro lookahead ở đây.
"""
import pandas as pd


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """ATR chuẩn (Wilder smoothing qua ewm alpha=1/period) — cùng công thức
    _atr() đã dùng trong scalp_strategy.py. `period` phần tử đầu bị ép NaN
    tường minh: ewm(adjust=False) của pandas KHÔNG tự sinh NaN đầu chuỗi, nên
    phải ép tay để caller (strategy.py) fail-safe đúng bằng pd.isna() thay vì
    dùng số liệu ATR chưa hội tụ đủ."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    result = tr.ewm(alpha=1 / period, adjust=False).mean()
    result.iloc[:period] = float("nan")
    return result


def adx(df: pd.DataFrame, period: int) -> pd.Series:
    """ADX chuẩn: +DM/-DM Wilder-smoothed thành +DI/-DI (chia ATR cùng
    period) → DX = 100*|+DI - -DI| / (+DI + -DI) → ADX = Wilder-smoothed DX.
    2*period phần tử đầu bị ép NaN tường minh (+DI/-DI cần period nến ấm, ADX
    cần thêm period nến làm mượt DX mới hội tụ đủ)."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)) * down_move

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low, (high - prev_close).abs(), (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, float("nan"))
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    result = dx.ewm(alpha=1 / period, adjust=False).mean()
    result.iloc[:2 * period] = float("nan")
    return result


def atr_percentile(df: pd.DataFrame, atr_period: int, lookback: int) -> pd.Series:
    """Percentile (0-100) của ATR tại mỗi nến so với `lookback` giá trị ATR
    liền trước nó — rolling window KẾT THÚC TẠI nến đó (không nhìn tương
    lai). NaN khi chưa đủ atr_period + lookback nến lịch sử (ATR chưa ấm HOẶC
    cửa sổ rolling chưa đủ `lookback` giá trị ATR hợp lệ — rolling(min_periods=
    lookback) coi các NaN đầu chuỗi ATR là thiếu quan sát, tự động lan NaN cho
    tới khi đủ cửa sổ toàn giá trị hợp lệ)."""
    atr_series = atr(df, atr_period)
    return atr_series.rolling(window=lookback, min_periods=lookback).rank(pct=True) * 100
```

- [ ] **Step 2: Sanity-check against synthetic data**

Run this scratch script (temp file in the scratchpad dir, or `python -c` heredoc — NOT committed):

```python
import pandas as pd
from indicators import atr, adx, atr_percentile

# 60 nến: 30 nến dao động hẹp (chop) rồi 30 nến tăng đều mạnh (trend rõ).
chop = {"open": [], "high": [], "low": [], "close": []}
for i in range(30):
    base = 100 + (i % 2) * 0.2
    chop["open"].append(base); chop["high"].append(base + 0.3)
    chop["low"].append(base - 0.3); chop["close"].append(base + 0.1)
trend = {"open": [], "high": [], "low": [], "close": []}
for i in range(30):
    base = 100 + i * 2
    trend["open"].append(base); trend["high"].append(base + 2.5)
    trend["low"].append(base - 0.5); trend["close"].append(base + 2.0)
df = pd.DataFrame({k: chop[k] + trend[k] for k in chop})

a = atr(df, 14)
x = adx(df, 14)
p = atr_percentile(df, 14, 20)

assert a.iloc[:14].isna().all(), "ATR: 14 phần tử đầu phải NaN"
assert a.iloc[-1] > a.iloc[29], "ATR cuối chuỗi (trend mạnh) phải > ATR lúc chop"
assert x.iloc[:28].isna().all(), "ADX: 28 phần tử đầu phải NaN"
assert x.iloc[-1] > 25, f"ADX cuối chuỗi (trend mạnh, đều) kỳ vọng >25, được {x.iloc[-1]}"
assert p.iloc[:33].isna().all(), "atr_percentile: chưa đủ 14+20 nến phải NaN"
assert p.iloc[-1] == 100.0, f"atr_percentile cuối chuỗi (ATR cao nhất trong lookback) kỳ vọng 100, được {p.iloc[-1]}"
print("OK — indicators sanity check passed")
```

Expected: `OK — indicators sanity check passed` with no assertion error.

- [ ] **Step 3: Commit**

```bash
git add indicators.py
git commit -m "Add atr()/adx()/atr_percentile() indicator helpers"
```

---

### Task 2: `config.py` — new filter config fields

**Files:**
- Modify: `d:\gold-smc-bot\config.py` (insert immediately after the existing `#11` block's last line — NOT right before `# ── Risk Management ──`; the `#7` news-filter block already sits between `#11` and that header)

- [ ] **Step 1: Read the current block boundaries**

Run: `grep -n "weekend_guard_hours\|# ── Risk Management\|news_filter_enabled" config.py`
Current layout (verify against your own read): the `#11` block (`weekend_guard_hours`) ends, then the `#7` news-filter block (`news_filter_enabled`...) follows, then the `# ── Risk Management ──` header comes after that. Insert the new `#12`/`#13` block immediately after `#11`'s last line and before `#7` — do not reorder the existing `#7` block, just insert ahead of it.

- [ ] **Step 2: Insert the new config fields**

```python
    # #12 — ADX trend-strength gate: chỉ tin CHoCH/BOS xác nhận khi ADX (đo trên
    #   LTF, tại đúng nến xác nhận) đủ mạnh — tránh sweep+CHoCH là nhiễu cấu trúc
    #   trong thị trường yếu/sideway. MẶC ĐỊNH TẮT — cần backtest-tuning đo trước
    #   khi bật, theo đúng cách #6 (require_discount_premium) đã làm.
    adx_filter_enabled: bool = False
    adx_period: int = 14
    adx_min_threshold: float = 20.0

    # #13 — ATR-regime gate: chặn khi biến động (ATR, đo trên LTF) co hẹp so với
    #   lịch sử gần (percentile thấp) tại đúng nến xác nhận — dấu hiệu
    #   sideway/thanh khoản mỏng. Không chặn percentile cao (trend mạnh vẫn được
    #   chấp nhận). MẶC ĐỊNH TẮT — cần backtest-tuning đo trước khi bật.
    atr_regime_filter_enabled: bool = False
    atr_period: int = 14
    atr_regime_lookback: int = 100
    atr_regime_min_percentile: float = 25.0
```

- [ ] **Step 3: Sanity-check the config loads with new defaults**

Run:
```bash
python -c "from config import get_config; c = get_config('gold'); print(c.adx_filter_enabled, c.adx_period, c.adx_min_threshold, c.atr_regime_filter_enabled, c.atr_period, c.atr_regime_lookback, c.atr_regime_min_percentile)"
```
Expected: `False 14 20.0 False 14 100 25.0`

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "Add adx_filter_enabled/atr_regime_filter_enabled config fields (off by default)"
```

---

### Task 3: `strategy.py` — wire the two gates into `analyze()`

**Files:**
- Modify: `d:\gold-smc-bot\strategy.py:1-21` (imports), `strategy.py:55-67` (insert after step 4, before existing step 4b)

- [ ] **Step 1: Add the import**

At the top of `strategy.py`, alongside the existing `smc.*`/`risk` imports:

```python
from indicators import adx, atr_percentile
```

- [ ] **Step 2: Insert the two gate steps**

Insert immediately after the existing step-4 block (the `if confirm is None: return None` at `strategy.py:55-56`) and before the existing step 4b comment (`# ── 4b. #9 Trần tuổi CẢ CHUỖI...`, `strategy.py:58`):

```python
    # ── 4a. #12 ADX trend-strength gate (tùy chọn) ──────
    if cfg.adx_filter_enabled:
        adx_series = adx(ltf_df, cfg.adx_period)
        adx_at_confirm = adx_series.iloc[confirm.index]
        if pd.isna(adx_at_confirm) or adx_at_confirm < cfg.adx_min_threshold:
            log.debug("ADX tại điểm confirm quá yếu — trend không đủ lực, bỏ.")
            return None

    # ── 4a2. #13 ATR-regime gate (tùy chọn) — chặn khi biến động co hẹp ──
    if cfg.atr_regime_filter_enabled:
        pct_series = atr_percentile(ltf_df, cfg.atr_period, cfg.atr_regime_lookback)
        pct_at_confirm = pct_series.iloc[confirm.index]
        if pd.isna(pct_at_confirm) or pct_at_confirm < cfg.atr_regime_min_percentile:
            log.debug("ATR percentile tại điểm confirm quá thấp — biến động co hẹp/chop, bỏ.")
            return None
```

(`pd` is already imported at the top of `strategy.py` — no new import needed for `pd.isna`.)

- [ ] **Step 3: Sanity-check both flags off leaves behavior unchanged**

Run a quick backtest with both flags at their defaults (`False`) and confirm it matches the last recorded baseline trade count for XAUUSDm:

```bash
python backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
```

Expected: trade count / PF / winrate match whatever `backtest.py` last reported for this symbol before this change (spot-check against `backtest_trades.csv`'s row count or the console summary — should be byte-for-byte identical since both new gates are `False` by default and therefore no-ops).

- [ ] **Step 4: Sanity-check each flag individually does not crash and changes trade count**

```bash
python -c "
from config import get_config
from backtest import load_from_mt5, SYMBOL_INFO
cfg = get_config('gold')
cfg.adx_filter_enabled = True
print('ADX filter enabled, no crash on config mutation — running full backtest next is the real check')
"
```

Then run each of these as full backtest invocations (temporarily edit `XAUUSD` in `config.py` to set `adx_filter_enabled=True`, run, revert; repeat for `atr_regime_filter_enabled=True`) — OR, simpler, use `backtest.py`'s existing CLI override pattern if one exists (check `backtest.py --help` for a generic `--set key=value` flag; if none exists, do the temporary edit-run-revert by hand, do NOT commit the temporary edit):

```bash
python backtest.py --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
```

Expected: runs to completion with no exception/traceback, and the trade count differs from the baseline in Step 3 (fewer trades, since both gates are strictly more restrictive) — for either flag on. This confirms the gates are live and fail-safe (no crash on NaN warmup rows), NOT a claim about which setting is better — full A/B comparison (PF/winrate/CAGR, H1/H2, all 3 symbols) is separate `backtest-tuning` work, out of scope for this implementation task per the spec.

Revert any temporary `config.py` edits made for this check before proceeding (`git diff config.py` must be empty relative to Task 2's commit).

- [ ] **Step 5: Commit**

```bash
git add strategy.py
git commit -m "Wire adx_filter_enabled/atr_regime_filter_enabled into strategy.analyze() as steps 4a/4a2"
```

---

### Task 4: Delete the stale `rsi-momentum-filter` branch

**Context:** User explicitly authorized this during brainstorming (chose "Xoá bỏ nhánh RSI, thay bằng ADX") — the branch has a single unmerged, never-backtested commit (`f113e91`) whose purpose (momentum confluence gate) is now superseded by the ADX gate just implemented.

- [ ] **Step 1: Confirm the branch's state and whether it's checked out in a worktree**

```bash
git branch -a
git worktree list
git log rsi-momentum-filter -1
```

Expected: `rsi-momentum-filter` listed (not the current branch `*`), single commit `f113e91` as its tip. `git worktree list` is expected to show it checked out at `.worktrees/rsi-momentum-filter` (a `+` prefix on the branch in `git branch -a` confirms this) — `git branch -D` will refuse to delete a branch that's checked out in any worktree, so that worktree must be removed first.

- [ ] **Step 2: Remove the linked worktree (only if Step 1 shows one)**

```bash
git status  # run inside .worktrees/rsi-momentum-filter first, or trust `git worktree list` output — confirm it's clean before removing
git worktree remove .worktrees/rsi-momentum-filter
```

Expected: command completes with no output (success) if the worktree is clean. If it reports uncommitted changes, STOP and ask the user before using `--force` — do not discard work without checking what it is.

- [ ] **Step 3: Delete the local branch**

```bash
git branch -D rsi-momentum-filter
```

Expected: `Deleted branch rsi-momentum-filter (was f113e91).`

(No remote deletion needed — confirm first with `git branch -a` that no `origin/rsi-momentum-filter` exists; if one does, stop and ask the user before running `git push origin --delete rsi-momentum-filter`, since deleting a remote branch is a shared-state change outside this plan's authorization scope.)

---

### Task 5: Update spec/plan cross-references (optional housekeeping)

- [ ] **Step 1:** No `CLAUDE.md` changes needed — existing optional filters (`#6`, `#9`, `#10`, `#11`) are documented only as `config.py` comments, not in `CLAUDE.md`; this plan follows the same convention, no doc update required.

---

## Follow-up (explicitly out of scope for this plan, per spec)

Running the full `backtest-tuning` A/B validation (baseline vs `adx_filter_enabled=True` vs `atr_regime_filter_enabled=True` vs both, full-2y + H1/H2, all 3 symbols) and deciding whether to flip either default to `True` is separate follow-up work using the `backtest-tuning` skill — not part of this implementation plan.
