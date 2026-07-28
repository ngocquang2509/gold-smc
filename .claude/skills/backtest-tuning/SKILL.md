---
name: backtest-tuning
description: Use when tuning config.py parameters or changing strategy.py/smc/* logic in gold-smc-bot, or when asked to backtest, compare PF/winrate/CAGR, or validate a strategy change. Rigid checklist — run baseline backtest, apply change, re-run, compare full-period and half-period (H1/H2) metrics before deciding to keep or revert.
---

# Backtest Tuning

Validates any change to `config.py`, `strategy.py`, or `smc/*` against a real
before/after backtest, including a half-period (H1/H2) split to catch
overfitting that a full-period-only comparison would miss.

Create one TodoWrite item per step below and follow them in order — do not
skip the H1/H2 split even if the full-period result looks good.

## Step 1: Baseline, full period

Confirm the symbol with the user if not already stated: `XAUUSDm`/`gold` or
`EURUSDm`/`eurusd`. Run:

```bash
python backtest.py --from-mt5 --symbol <sym> --years 2
```

This works identically if data comes from `--csv-ltf`/`--csv-htf` instead —
same report code path either way. Costs are applied by default (net-of-cost);
pass `--no-costs` only if the user explicitly wants gross numbers, and say so
in the comparison table if you do.

Record from stdout: **PF, winrate ("Winrate ròng"), CAGR, max drawdown, trade
count ("Số lệnh (logic)")**. Total PnL and avg win/loss are also printed but
are out of scope for the comparison table in Step 5 — don't track them.

## Step 2: Baseline, half-period split

`backtest.py` has no date-range flag — only `--years N` ("most recent N
years"), which can't isolate an arbitrary historical window on its own. Don't
re-run against MT5 for this. Instead, split post-hoc from the two files
`backtest.py` just wrote: `backtest_trades.csv` and `backtest_equity.csv`.

**The trades CSV has one row per fill, not per logical trade** — a trade with
partial closes produces multiple rows sharing the same `entry_time`/`entry`/
`direction`. You must reproduce `backtest.py`'s own grouping
(`_report()`, `backtest.py:215`) before computing anything, or partial-close
legs get miscounted as separate wins/losses and PF/winrate come out wrong:

```python
import pandas as pd

trades = pd.read_csv("backtest_trades.csv", parse_dates=["entry_time"])
equity = pd.read_csv("backtest_equity.csv", parse_dates=["time"])

# 1. Group fills into logical trades — mirrors backtest.py:215 exactly.
g = trades.groupby(["entry_time", "entry", "direction"]).agg(net=("pnl", "sum")).reset_index()

# 2. Midpoint of the tested window, from the equity curve's time span.
midpoint = equity["time"].min() + (equity["time"].max() - equity["time"].min()) / 2

# 3. Assign each grouped logical trade to a half by its entry_time.
h1 = g[g.entry_time < midpoint]
h2 = g[g.entry_time >= midpoint]

def pf_wr(half):
    n = len(half)
    wins = half[half.net > 0.01]
    losses = half[half.net < -0.01]
    wr = len(wins) / n * 100 if n else 0
    gp, gl = wins.net.sum(), -losses.net.sum()
    pf = gp / gl if gl > 0 else float("inf")
    return n, wr, pf

n1, wr1, pf1 = pf_wr(h1)
n2, wr2, pf2 = pf_wr(h2)

# 4. CAGR + max-DD per half — mirrors backtest.py:230-242, windowed to the
#    half's equity rows instead of the full curve. No re-simulation needed.
def cagr_dd(eq_half, start_bal, end_bal):
    if eq_half.empty:
        return 0.0, 0.0
    peak, max_dd = start_bal, 0.0
    for bal in eq_half["balance"]:
        peak = max(peak, bal)
        max_dd = max(max_dd, (peak - bal) / peak * 100)
    days = (eq_half["time"].iloc[-1] - eq_half["time"].iloc[0]).days
    yrs = days / 365.25
    cagr = ((end_bal / start_bal) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    return cagr, max_dd

eq1 = equity[equity.time < midpoint]
eq2 = equity[equity.time >= midpoint]
cagr1, dd1 = cagr_dd(eq1, eq1["balance"].iloc[0], eq1["balance"].iloc[-1])
cagr2, dd2 = cagr_dd(eq2, eq2["balance"].iloc[0], eq2["balance"].iloc[-1])
```

Run this inline (e.g. via a short Python one-off, not a new checked-in
script) after Step 1's backtest run. **Sanity check before trusting the
split**: `len(g) == n1 + n2` and the PF computed from `g` in full (no split)
should match the "Profit factor" printed by `backtest.py` in Step 1 — if it
doesn't, the CSVs are stale from a previous run; re-run Step 1.

PF/winrate/trade-count, and now CAGR/max-DD, are all tracked per half — fill
every cell in Step 5's table, none are N/A.

## Step 3: Apply the change

Edit `config.py` (the relevant per-symbol instance) or `strategy.py`/`smc/*`
as intended for this tuning session.

## Step 4: Re-run, full period + split

Repeat Step 1 and Step 2 against the changed code. Do this in the same
session, close in wall-clock time to Step 1 — `--years` resolves its window
from `datetime.now()` at run time, so a large gap between the baseline and
after-change runs shifts both 2-year windows (and their H1/H2 midpoints)
slightly, making the before/after comparison marginally inexact at the edges.
Minutes to low hours apart is fine over a 2-year window; don't let a tuning
session span days between the two runs without noting the drift.

## Step 5: Comparison table

Present a table, before vs. after, for:

| Metric | Full (before) | Full (after) | H1 (before) | H1 (after) | H2 (before) | H2 (after) |
|---|---|---|---|---|---|---|
| PF | | | | | | |
| Winrate | | | | | | |
| Trade count | | | | | | |
| CAGR | | | | | | |
| Max DD | | | | | | |

## Step 6: Keep/revert decision

PF is the primary metric. Default heuristic (a starting point — say so
explicitly if you deviate in a given session):

- **Revert-leaning** if full-period PF drops by more than **0.10 absolute**
  or more than **10% relative** vs. before.
- **Overfit warning** if H2 PF falls below **70% of H1 PF** (or H2 flips
  unprofitable while H1 stays profitable) — flag this explicitly even when
  full-period PF improved, since that's exactly the pattern that means the
  full-period gain is likely concentrated in older data and won't hold going
  forward. Don't silently recommend "keep" when this fires.
- Otherwise, keep.

**Low-confidence override:** if either half has **fewer than 20 grouped
trades** (post-groupby count from Step 2, not raw CSV rows), say so and do
not issue a firm keep/revert claim based on that half alone — surface the
ambiguity to the user rather than deciding for them.

## Step 7: Persist results (opt-in)

Ask the user whether to record the outcome in the memory system (e.g. update
`backtest-tuning-2y.md` or the relevant per-symbol memory file). Never
overwrite an existing memory file without asking first.

## Edge cases

- **MT5 unreachable** (non-Windows, or terminal not logged in): stop and
  suggest `--csv-ltf`/`--csv-htf` with pre-exported data instead of
  fabricating results. The Step 2 split logic is identical either way.
- **Stale CSVs**: if the sanity check in Step 2 fails (`len(g) != n1 + n2`,
  or full PF from `g` doesn't match Step 1's printed PF), the CSVs are from
  an earlier run — re-run Step 1 before trusting the split.
- **Low trade count in a half-period**: covered in Step 6 — warn, don't
  conclude.

## Out of scope

This skill only covers config/strategy tuning validation. It does not cover:
SMC strategy development guidance (entry-sequence correctness, no-lookahead
invariants), the live/demo operation checklist (dry_run, session gating, risk
guard), or adding a new symbol/broker to `CONFIGS` — those are separate
skills.
