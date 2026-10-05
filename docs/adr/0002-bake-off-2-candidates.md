# Bake-off #2 Candidates, fixed before testing

Bake-off #1 passed 0/3: no grid point of C1 (H4 breakout), C2 (M15 opening-range breakout) or C4 (M15 sweep → retest) had in-sample Edge after Cost Stress. On 2026-10-05, before any Bake-off #2 result existed, we fixed three new Candidates from mechanisms Bake-off #1 did not test: short-horizon mean reversion, slow time-series momentum and intraday session momentum. Their specs and grids are below. The Acceptance Gate, the Final Holdout pass bar and the walk-forward procedure are unchanged from ADR 0001.

## Batch rules

- **H1 floor.** M15 setups paid ~0.2R/trade under Cost Stress in Bake-off #1. Every spec states its cost per trade in R before testing; a design above ~0.1R on paper is rejected without being run.
- **Symmetric long/short on every symbol.** No direction bias per symbol (e.g. long-only gold): that is post-hoc selection, like the rejected "C1 on gold only".
- **Retired Candidates are skipped.** C1/C2/C4 carry `retired` and the Bake-off no longer runs them by default; the Final Holdout refuses them.
- **Trial count.** Bake-off #1 = 3, Bake-off #2 = 3, **total 6** Candidates tested on the same 2017 → Sep 2025 out-of-sample years. A walk-forward pass must be read against this count; the one-shot Final Holdout is what separates luck from Edge.
- **Smoke test before the full run.** Data cut at 2022-08-31, printing only trade counts, exit-type mix and the causality check, never PF/R/gate. A mechanical bug may be fixed (and logged here); a Candidate that is merely short of trades is not redesigned.

## Infrastructure changes made before any Bake-off #2 result

- **`max_bars` time stop** (engine + live): close at the open of bar fill+max_bars, counted from the fill. With it off, C1/C2/C4 trade lists were byte-identical (6e84f61).
- **Swap night count fixed** (b70d0f5). The model charged every calendar midnight plus the Wednesday triple, so weekends were paid twice (one week = 9 nights instead of 7) and the triple fell on Tue→Wed. Now only rollovers ending Mon–Fri are charged, ×3 for the one ending Wednesday (MT5 `swap_rollover3days` = Wednesday for all three symbols). **Bake-off #1 ran with the overcharge.** C1/C2/C4 are not re-run: their in-sample t-stats were negative, which swap cannot explain.

## Candidates

Costs are stressed (×1.5), as a share of ATR, using 2016 and 2024 median ATR as the range.

### A: `a_zfade`, H4 mean reversion

- `z = (close − SMA(n)) / ATR(14)`. Buy at the next open on the **first** bar where z ≤ −k (the bar before had z > −k), sell on the first bar where z ≥ +k. It is an event, not a level: after a stop or time stop it does not re-enter while the price is still stretched.
- Stop `m × ATR(14)` from the signal close, no TP. `flat` on the first close that crosses back over the SMA. Time stop `max_bars`.
- No trend filter (it would cost the fourth parameter, and the usual choice is borrowed from tuned literature).
- **Grid (72):** `n ∈ {10, 20, 40}`, `k ∈ {1.5, 2.0, 2.5}`, `m ∈ {2, 3}`, `max_bars ∈ {6, 12}`.
- **Cost:** spread 0.01–0.03R. Swap for a gold long 0.02–0.06R per night; worst case (2016, m = 2, held through the Wednesday triple) ≈ 0.23R. Shorts on gold and EURUSD pay no swap.

### B: `b_tsmom`, D1 time-series momentum

- Signal is a standing state: `sign(close / close[L] − 1)`, evaluated on every D1 close. Enter at the next open whenever flat, so it re-enters after a stop if the sign still holds.
- Stop `m × ATR(20)`, no TP. `flat` when the sign flips.
- **Sunday stub bars** (≈ 2 h each, from the 00:00 UTC resample) are excluded from `L`, the sign and ATR. No entry or flip is emitted on them. The engine still manages stops through them.
- **Grid (9):** `L ∈ {60, 120, 250}` trading days, `m ∈ {3, 4, 6}`.
- **Cost:** spread < 0.01R. Swap for a gold long 0.03–0.11R **per week** held (0.18–0.33 D1 ATR/week ÷ m): a 3-month gold long pays ~0.4–1.3R. EURUSD long 0.01–0.04R/week; GBPUSD both sides < 0.01R/week.
- **Known risk:** flips plus stop re-entries may give fewer than 200 out-of-sample trades. If so it fails the gate on trade count and is not redesigned.

### C: `c_intramom`, H1 intraday session momentum

- Morning move = close of the H1 bar ending 08:00 New York − open of the H1 bar starting 08:00 London. DST-aware; the window is 4 h instead of 5 h in the weeks when US and UK DST don't line up. The hours are fixed by market structure and are not gridded.
- If |morning move| ≥ `k × D1 ATR(14)` (built from completed prior days), enter in its direction at the 08:00 New York open. At most one trade per day. No trade if either anchor bar is missing (holidays, Dec 25 / Jan 1).
- Stop `m × H1 ATR(14)`, no TP. `flat` at 16:00 New York.
- **Grid (6):** `k ∈ {0, 0.25, 0.5}`, `m ∈ {2, 3}`.
- **Cost:** spread 0.02–0.065R; no swap (closes before the 00:00 server rollover).
- **Overlap:** same session-flow family as the retired C2. The trigger is different (no range break; one direction decided at a fixed time), so it counts as a separate trial.

## Considered Options

- **Volatility-compression breakout (NR7 / low ATR percentile):** rejected. It is too close to the C1/C2 breakout family that just failed.
- **Fixed-direction seasonality** (e.g. gold drifts up in session X): rejected, because it breaks the symmetry rule.
- **Weekly evaluation for B:** rejected. Fewer trades, in a Candidate already at risk on trade count.
