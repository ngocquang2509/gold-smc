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

## Candidates (fixed 2026-10-06, before any test)

Same rules as ADR 0002: ≤ 4 parameters, one Shared Parameter Set across all three symbols, symmetric long/short, timeframe H1 or above, cost ≤ ~0.1R/trade on paper, smoke test before the full run. In addition:
- **The auxiliary data is the primary signal.** No Candidate is a retired one with an auxiliary filter added; that is the "added filters" path ADR 0001 closes.
- **Direction convention.** All three symbols are quoted against USD, so a USD-bullish reading means **sell** every symbol and a USD-bearish reading means **buy**. "US real yield up" counts as USD-bullish. There are no per-symbol signs.
- **Sign changes** use the last non-zero sign, so + → 0 → − still counts as one flip.
- **Trial Count: 6 + 3 = 9.**

### D: `d_usdtrend`, H4 dollar trend
- `z = log(USD5ᵢ / USD5ᵢ₋L) / (σ₁ · √L)`, where σ₁ is the standard deviation of 1-bar USD5 log returns over the last 500 bars (fixed).
- Level entry: while flat, z ≥ +k (and > 0) → sell at the next open; z ≤ −k (and < 0) → buy.
- `flat` when z changes sign: the band k gates entries only (hysteresis). Stop `m × ATR(20)` of the traded symbol. No TP.
- **Grid (18):** `L ∈ {30, 60, 120}`, `k ∈ {0, 0.5, 1.0}`, `m ∈ {3, 5}`.
- **Cost:** spread ≤ 0.02R. Gold longs pay swap of ~0.09–0.27R per week held.

### E: `e_usdresid`, H4 residual mean reversion
- Residual per bar `εᵢ = r_symᵢ − βᵢ · r_USD5ᵢ` (log returns), with βᵢ from a rolling OLS over the 500 bars before i (fixed). `z = Σε over the last n bars / (σ_ε · √n)`, with σ_ε over the last 500 bars.
- Event entry: the first bar with z ≤ −k → buy at the next open; the first with z ≥ +k → sell.
- `flat` when z crosses 0; time stop `max_bars`; stop `m × ATR(14)`; no TP.
- **Grid (36):** `n ∈ {6, 12, 24}`, `k ∈ {1.5, 2.0, 2.5}`, `m ∈ {2, 3}`, `max_bars ∈ {6, 12}`.
- **Cost:** spread 0.01–0.03R.
- **Overlap:** the same fade family as the retired `a_zfade`, but on the part of the move that USD5 doesn't explain. EUR and GBP are 1/5 of USD5 each, which mutes their residual slightly.

### F: `f_realyield`, D1 real-yield direction
- Change over L D1 bars of `DFII10` (as known at each bar close), Sunday stub bars excluded as in `b_tsmom`: `z = Δ / σ_L`, with σ_L the standard deviation of L-bar changes over the previous 250 bars (fixed). Yields publish around 20:15–21:15 UTC, so the next 00:00 UTC open is the first possible entry.
- Level entry: z ≥ +k (and > 0; real yield up = USD-bullish) → sell; z ≤ −k (and < 0) → buy.
- `flat` when z changes sign. Stop `m × ATR(20)`. No TP.
- **Grid (12):** `L ∈ {5, 20, 60}`, `k ∈ {0, 0.5}`, `m ∈ {3, 4}`.
- **Cost:** spread < 0.01R; gold-long swap is the known handicap.
- **Known risk:** it may fall short of 200 out-of-sample trades; that is reported as a fail and not redesigned.
- Only `DFII10` is fetched; no Candidate uses `DGS2`/`DGS10`.

**Rejected hypothesis:** a curve-slope (10y − 2y) regime. Its sign for gold versus FX is ambiguous, which invites post-hoc reading.

## Considered Options

- **Wide scope (add calendar, COT, ECB/BoE)**: rejected for now. These sources carry weaker information, and they need hand-built exception tables exactly around the Final Holdout.
- **Treasury XML feed instead of ALFRED**: rejected. It is keyless, but it serves revised history and its posting time is unverified.
- **ICE DXY weights**: rejected on licensing.
- **Per-symbol "dollar excluding own currency" baskets**: rejected. Three different series per Candidate drift toward per-symbol design.
