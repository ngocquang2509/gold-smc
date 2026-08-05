# Walk-forward validation for backtest-tuning

**Date**: 2026-08-05
**Status**: Approved by user, pending spec review

## Problem

The project is entering a phase of heavy iterative backtesting (many config/strategy
tuning passes in a row). The existing `backtest-tuning` skill catches overfitting with
a single H1/H2 (two-half) split of the 2-year backtest window. Two halves is coarse:
a parameter change can look great in aggregate and in each half individually, while
actually being driven by a lucky 2-3 month stretch inside one half. With tuning volume
about to increase, the team wants a finer-grained, still-cheap check that raises
confidence that a kept change is a real, durable edge rather than a fit to a specific
slice of history.

## Goals

- Detect parameter/strategy changes whose apparent improvement is concentrated in a
  narrow slice of history, not spread across the tested period.
- Keep the check cheap: one MT5/CSV backtest run per side (before/after), no repeated
  data fetches, no automated parameter search.
- Make the check fast to run repeatedly by hand, since it will be invoked many times
  per tuning session going forward — a reusable script instead of ad hoc inline Python.
- Preserve the existing full-period PF/winrate/CAGR/max-DD comparison and its
  keep/revert heuristic; add window-level consistency as an additional signal, not a
  replacement for the primary PF check.

## Non-goals

- True walk-forward *optimization* (auto re-fitting parameters per window via grid/
  random search). Tuning in this project is manual/discretionary; this design only
  adds finer-grained *out-of-sample consistency checking* of a manually chosen change,
  reusing the term "walk-forward" loosely for that.
- Changes to `backtest/backtest.py`'s simulation engine, `strategy/`, or `config/`.
  This is purely a validation-tooling change.
- Parameter sensitivity analysis (±X% perturbation) — mentioned during brainstorming
  as a related idea but out of scope for this design; can be a follow-up.

## Design

### 1. New script: `backtest/walk_forward_report.py`

A standalone, read-only reporting script — it does not run a backtest itself, it
post-processes the CSV outputs `backtest/backtest.py` already writes
(`backtest_trades.csv`, `backtest_equity.csv`). This mirrors how the H1/H2 split
works today (post-hoc split of one run's output), just generalized from 2 slices to N.

**Why post-hoc splitting instead of re-running per window**: a single 2-year backtest
already contains the full trade history needed; splitting by timestamp after the fact
is free, whereas re-running the backtest N times (once per window) would be wasteful
and risks the "moving `--years` window" drift problem the current skill already
documents for the before/after runs.

**CLI**:

```bash
python -m backtest.walk_forward_report [options]
```

| Flag | Default | Meaning |
|---|---|---|
| `--trades` | `backtest_trades.csv` | Trade fills CSV (current run) |
| `--equity` | `backtest_equity.csv` | Equity curve CSV (current run) |
| `--windows` | `6` | Number of equal-duration windows to split the tested period into |
| `--min-trades` | `8` | Below this grouped-trade count, a window is flagged low-confidence and excluded from the consistency vote (Step 6 criterion) |
| `--baseline-trades` | (none) | Optional: a second trades CSV from a prior ("before") run, to diff against `--trades` |
| `--baseline-equity` | (none) | Optional: paired equity CSV for `--baseline-trades` |

**Single-run mode** (no `--baseline-*` given): prints one table, one row per window,
columns: window date range, trade count (grouped/logical), winrate, PF, CAGR, max DD.
Windows below `--min-trades` are marked `(low-confidence)` in the row.

**Compare mode** (`--baseline-trades`/`--baseline-equity` given): prints the same
per-window table twice (before/after) plus:
- The existing full-period comparison (PF/winrate/trades/CAGR/max-DD, before vs after)
  — this replaces (not duplicates) what the skill currently computes by hand from the
  printed backtest report; the script recomputes it directly from the CSVs so there's
  one source of truth.
- A **consistency line**: `Windows with PF > 1: <before>/<N eligible> → <after>/<N eligible>`
  (eligible = not low-confidence).
- Applies the Step 6 decision rule (see below) and prints one of: `KEEP`,
  `REVERT-LEANING`, or `OVERFIT WARNING` with the specific trigger reasons spelled out
  (e.g. "full PF improved 1.25→1.34 but winning-window count dropped 5/6→3/6").

**Window boundaries**: computed from the equity curve's min/max timestamp, divided
into `--windows` equal-duration slices (same midpoint-style logic the current H1/H2
code uses, generalized to N cut points). A logical trade (grouped by
`entry_time`/`entry`/`direction`, same grouping `_report()` already uses) is assigned
to the window containing its `entry_time`.

**Sanity check** (carried over from the current skill's Step 2): after grouping, the
script verifies `sum(window trade counts) == total grouped trades` and that the
full-period PF recomputed from the CSV matches what `backtest.py` printed at run time
within floating-point tolerance; if not, it errors out asking the user to re-run the
backtest (stale CSV), instead of silently reporting on stale data.

**Output files**: none — stdout only, like the rest of the backtest reporting. No new
CSVs are written by this script.

### 2. `backtest-tuning` skill changes

Restructure the middle steps around the new script:

- **Step 1 (baseline, full period)** — unchanged: run
  `python -m backtest.backtest --from-mt5 --symbol <sym> --years 2`.
- **New Step 2 (preserve baseline outputs)** — rename/copy the two CSVs the run just
  wrote: `backtest_trades.csv` → `backtest_trades_baseline.csv`,
  `backtest_equity.csv` → `backtest_equity_baseline.csv`. (Replaces the old Step 2's
  inline H1/H2 Python entirely — no more hand-written pandas snippet per session.)
- **Step 3 (apply the change)** — unchanged.
- **Step 4 (re-run)** — unchanged: re-run Step 1's command; this overwrites
  `backtest_trades.csv`/`backtest_equity.csv` with the "after" run.
- **New Step 5 (walk-forward comparison)** — run:
  ```bash
  python -m backtest.walk_forward_report \
    --baseline-trades backtest_trades_baseline.csv \
    --baseline-equity backtest_equity_baseline.csv
  ```
  Paste/summarize its table and verdict instead of hand-building the comparison table
  the old Step 5 asked for.
- **Step 6 (keep/revert decision)** — decision rule updated (see below); largely
  driven by the script's printed verdict now, but the human/agent still makes the
  final call using the same judgment the skill already asks for (e.g. flagging when
  the script's automatic verdict should be second-guessed).
- **Step 7 (persist results)** — unchanged.

Old Step 5's comparison table (Full/H1/H2 as columns) is replaced by the script's
window-rows table — cleaner for 6 rows than 6 extra columns would be.

### 3. Step 6 decision rule (generalized from H1/H2 to N windows)

Kept exactly as scoped in brainstorming — combine the existing full-period rule with a
new consistency signal, neither alone overriding the other automatically:

- **Revert-leaning** if full-period PF drops by more than 0.10 absolute or more than
  10% relative vs. before (unchanged from today).
- **Overfit warning** — new, generalized from the old "H2 PF < 70% of H1 PF" rule —
  fires when the count of eligible windows with PF > 1 *decreases* after the change
  (e.g. 5/6 → 3/6), shown explicitly even when full-period PF improved. This is not an
  auto-revert; it's a flag surfaced to the user per the existing "don't silently
  recommend keep when this fires" principle.
- **Low-confidence override** — unchanged in spirit: windows below `--min-trades` (8)
  are excluded from the numerator/denominator of the consistency count and the script
  says so explicitly; if fewer than half the windows are eligible, the script prints a
  note that the consistency signal itself is low-confidence this run.
- Otherwise: **keep**.

This is the "combined" option chosen during brainstorming — the stricter "≥N/6 windows
required or auto-revert" alternative was explicitly rejected in favor of surfacing the
signal for a human/agent decision, consistent with how the skill already treats the
low-trade-count case.

### 4. Out of scope reaffirmed

Same "Out of scope" section the skill already has (SMC strategy-development guidance,
live/demo checklist, adding a new symbol) is unaffected by this change.

## Testing / validation of this change itself

Since this is a tooling change to the validation process, not to strategy code, there
is no backtest-tuning cycle to run on it. Validation is:
- Run `walk_forward_report.py` standalone against the existing checked-in
  `backtest_trades.csv`/`backtest_equity.csv` in the repo root and confirm the
  per-window table and sanity check work end-to-end.
- Run it in compare mode using two copies of the same file as both "baseline" and
  "current" and confirm it reports zero deltas and a clean `KEEP` verdict (degenerate
  case sanity check).
- Manually verify one window's PF/winrate by hand against a `pandas` one-liner, same
  spot-check style the current skill uses to trust the H1/H2 split.
