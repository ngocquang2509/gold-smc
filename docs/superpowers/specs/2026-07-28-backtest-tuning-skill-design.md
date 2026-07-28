# Skill: backtest-tuning — Design

Date: 2026-07-28

## Purpose

Provide a rigid, repeatable checklist for tuning `config.py` parameters or
`strategy.py`/`smc/*` logic in the gold-smc-bot project, so that every change
is validated against a real backtest before/after comparison, and overfitting
across time is caught rather than missed.

This is the first of several planned skills for this repo (others: SMC
strategy development, live/demo operation checklist, adding a new
symbol/broker) — scoped separately per the brainstorming decomposition.

## Location & activation

- `.claude/skills/backtest-tuning/SKILL.md` (project-scoped)
- Type: rigid checklist (same category as `systematic-debugging`/TDD) — steps
  are followed in order, one TodoWrite item per step.
- Triggers on: tuning `config.py`, changing `strategy.py`/`smc/*`, requests to
  backtest, compare PF/winrate/CAGR, or validate a strategy change.

## Workflow

1. **Baseline, full period.** Confirm symbol (`XAUUSDm`/`EURUSDm`, aliases
   `gold`/`eurusd`). Run (works identically whether sourced from
   `--from-mt5` or `--csv-ltf`/`--csv-htf` — same `_report()` code path
   either way):
   ```
   python backtest.py --from-mt5 --symbol <sym> --years 2
   ```
   (net-of-cost by default, since `--no-costs` is unset). Record PF,
   winrate, CAGR, max drawdown, trade count from stdout. (Total PnL and
   avg win/loss are also printed but are out of scope for the comparison
   table in step 5 — track only those 5 metrics.)

2. **Baseline, half-period split.** `backtest.py` has no date-range flag —
   only `--years N` for "most recent N years", which cannot isolate an
   arbitrary historical window. Instead of re-running against MT5, split
   post-hoc from the files `backtest.py` already writes after step 1:
   `backtest_trades.csv` and `backtest_equity.csv`.

   - Find the midpoint timestamp from `backtest_equity.csv`'s `time`
     column range (min/max), since equity rows are appended once per LTF
     bar and this column spans the full tested window.
   - `backtest_trades.csv` has one row per *fill* (a trade with partial
     closes produces multiple rows) — `_report()` groups
     `tdf.groupby(["entry_time", "entry", "direction"])` and sums `pnl`
     per group to get one logical trade before computing PF/winrate/count
     (see `_report()` in `backtest.py`). **Reproduce that same groupby
     first**, then assign each grouped logical trade to H1/H2 by whether
     its `entry_time` falls before/after the midpoint, then compute
     PF/winrate/trade count per half from the grouped (not raw-row) data.
     Skipping the groupby and treating raw CSV rows as trades will
     misclassify partial-close legs as separate losing/winning trades and
     produce wrong PF/winrate.

3. **Apply the change.** Edit `config.py` (per-symbol instance) or
   `strategy.py`/`smc/*` as intended.

4. **Re-run, full period + split.** Repeat steps 1–2 against the changed
   code. Run this close in wall-clock time to step 1 (same session,
   ideally minutes apart) — `--years` resolves the window from
   `datetime.now()` at run time, so a large gap between the baseline and
   after-change runs shifts both 2-year windows and their H1/H2 midpoints,
   making the before/after comparison slightly inexact at the edges.

5. **Comparison table.** Present before/after for: PF, winrate, CAGR, max
   drawdown, trade count — for full-period, H1, and H2.

6. **Keep/revert decision.** PF is the primary metric. As a default
   heuristic (adjust if the user pushes back in a given session):
   - **Revert-leaning** if full-period PF drops by more than **0.10**
     absolute, or by more than **10% relative**.
   - **Overfit warning** if H2 PF falls below **70% of H1 PF** (or H2
     flips unprofitable while H1 is profitable) — surface this explicitly
     as a risk even if full-period PF improved, rather than silently
     recommending "keep."
   - Otherwise, keep.

   If either half has **fewer than 20 (grouped) trades**, flag that
   half's PF/winrate as statistically low-confidence and avoid a firm
   keep/revert claim based on it alone — surface the ambiguity to the
   user instead of deciding for them.

7. **Persist results (opt-in).** Ask the user whether to record the outcome
   in the memory system (e.g. update `backtest-tuning-2y.md` or
   `eurusd-baseline.md` under the memory directory). Never overwrite an
   existing memory file without asking first.

## Edge cases

- **MT5 unreachable** (non-Windows, terminal not logged in): stop and
  suggest `--csv-ltf`/`--csv-htf` with pre-exported data instead of
  fabricating results.
- **Low trade count in a half-period**: covered in step 6 — warn, don't
  conclude.

## Out of scope (deferred to later skills)

- SMC strategy development guidance (entry-sequence correctness, no-lookahead
  invariants) — separate skill.
- Live/demo operation checklist (dry_run, session gating, risk guard) —
  separate skill.
- Adding a new symbol/broker to `CONFIGS` — separate skill.
