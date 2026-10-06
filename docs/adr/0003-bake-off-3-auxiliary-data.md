# Bake-off #3: auxiliary data from a dollar basket and US yields, with a hard stop

Bake-offs #1 and #2 passed 0/6 Candidates built only on each symbol's own price bars (ADR 0001, ADR 0002). On 2026-10-06 we decided to run one more Bake-off, but only on information those bars cannot carry: a self-built US dollar basket and US Treasury yields. Both can be timestamped to when they were really known (research: `docs/research/bake-off-3-data-sources.md`). The Acceptance Gate, Final Holdout pass bar, walk-forward procedure and Cost Stress are unchanged. **Trial Count carried in: 6.**

## Hard stop

**If Bake-off #3 passes 0 Candidates, the project ends for these symbols (XAUUSDm, EURUSDm, GBPUSDm) on this broker.** There is no Bake-off #4 unless the symbols or the broker change. This is fixed before any infrastructure or Candidate exists, so a failed Bake-off #3 can't turn into "one more idea" tuned on the same out-of-sample years.

## Scope: two auxiliary sources

**USD5 dollar basket.** An equal-weight geometric index of USD against EUR, JPY, GBP, CAD and CHF, computed from Dukascopy M1 bid bars (USDJPY, USDCAD and USDCHF added to the downloader): `USD5 = Π (USD per unit of currency)^(-1/5)`, so it rises when the dollar strengthens. The weights are fixed by rule (equal), not tuned.
- Known at the close of the bar it is computed on: no publication lag, no revisions.
- Not ICE DXY: no ICE weights, no ICE name (ICE restricts use of its formulation).
- Known limitation: EUR and GBP are 1/5 of the basket each, so for EURUSD/GBPUSD the basket partly contains the traded pair itself.

**US yields** `DGS2`, `DGS10` (nominal) and `DFII10` (10y TIPS real yield), Federal Reserve H.15.
- Values are **ALFRED first releases** (the value as first published, not today's revised history), fetched through the FRED API with a free key read only from env `FRED_API_KEY`.
- Each value for day D is treated as **known from the next US business day, 16:15 ET** (the H.15 release time, converted to UTC with DST), even though Treasury may post earlier.

Deferred, not in this Bake-off: the economic calendar (no free consensus, so no surprise measure), CFTC positioning (weekly, irregular publication inside the Final Holdout), ECB/BoE yields, and the Fed broad dollar index (reconstructed before 2019).

## Infrastructure, built and tested before any Candidate

- **As-known-at store.** Rows `(series, observation_date, value, available_at_utc, source)`, cached locally. USD5 rows are available at their bar close.
- **Alignment by bar close.** The harness joins each series onto the Candidate's bars so that row i only sees values with `available_at_utc ≤ close(i)`. Missing publications stay missing (no forward fill that pretends a value was released).
- **Holdout guard.** Auxiliary values observed after 2025-10-01 are hidden unless `include_holdout=True`, exactly as `load_bars()` does for prices.
- **Contract extension.** A Candidate declares the series it needs (`aux=[...]`) and receives `signals(bars, aux, **params)`. Candidates without `aux`, including the six retired ones, are called exactly as before. `assert_causal` truncates `aux` together with `bars`.
- **Live parity.** `execution/live.py` refreshes the store on each source's schedule and builds `aux` with the same function as the backtest. *Deferred on 2026-10-06:* the live aux provider is built only if a Candidate passes the Final Holdout, before its Forward Test. Until then the live loop refuses any Candidate that declares `aux`.
- **Tests first:**
  - a value is invisible one second before `available_at`;
  - DST transitions on both sides;
  - no forward fill across a missing value;
  - the holdout is hidden;
  - the retired Candidates' trade lists are byte-identical.

## Candidates

To be specified in this ADR (spec, grid, cost estimate) and committed before any walk-forward run, under the same rules as ADR 0002: ≤ 4 parameters, one Shared Parameter Set across all three symbols, symmetric long/short, timeframe H1 or above, cost ≤ ~0.1R/trade on paper, smoke test before the full run.

## Considered Options

- **Wide scope (add calendar, COT, ECB/BoE)**: rejected for now. These sources carry weaker information, and they need hand-built exception tables exactly around the Final Holdout.
- **Treasury XML feed instead of ALFRED**: rejected. It is keyless, but it serves revised history and its posting time is unverified.
- **ICE DXY weights**: rejected on licensing.
- **Per-symbol "dollar excluding own currency" baskets**: rejected. Three different series per Candidate drift toward per-symbol design.
