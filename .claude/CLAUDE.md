# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An automated trading bot for the owner's own Exness MT5 account (XAUUSDm, EURUSDm, GBPUSDm), monitored over Telegram. Code comments and log messages are in Vietnamese; keep that convention when editing.

**The project is mid-rebuild.** The old SMC (H4→M15) and M5 scalp strategies were removed (recoverable from git tag `legacy-v1`) because their edge came from tuning on a single 2-year bull-market window. Read `docs/adr/0001-rebuild-strategy-layer-validation-first.md` before any strategy work. It fixes the Acceptance Gate, the Bake-off Candidates (C1 H4 Donchian trend, C2 session opening-range breakout, C4 stripped SMC), and the build order. Use the vocabulary in `GLOSSARY.md` (Edge, Out-of-sample, Final Holdout, Candidate, Complexity Budget, Shared Parameter Set, Kill-switch...).

Build order (ADR 0001): ~~1. tag + remove legacy~~ → ~~2. Dukascopy data pipeline + Cost Stress + gap fills~~ → ~~3. walk-forward Acceptance Gate harness~~ → 4. Candidates C1, C2, C4 → 5. Bake-off + Final Holdout (**Bake-off #1 on 2026-10-04: 0/3 passed**, Final Holdout unspent; see ADR 0001 Outcomes. C1/C2/C4 are retired. **Bake-off #2** (`a_zfade`, `b_tsmom`, `c_intramom`, ADR 0002) on 2026-10-05: **0/3 passed**, all retired; Trial Count 6, 0 passes. The bot stays in dry run. **Bake-off #3** (ADR 0003, 2026-10-06): auxiliary data only (USD5 dollar basket from Dukascopy USDJPY/USDCAD/USDCHF + EURUSD/GBPUSD, and ALFRED first-release `DGS2`/`DGS10`/`DFII10` known D+1 16:15 ET); infrastructure (as-known-at store, `signals(bars, aux)`) is built before any Candidate; **hard stop if it passes 0**) → 6. Telegram inbound commands + Kill-switch (components done; wired in by the step-7 live loop) → 7. Forward Test on demo (live loop `execution/live.py` done; waits for a holdout passer).

## Commands

```bash
# Install (no requirements file — install manually)
pip install MetaTrader5 pandas numpy certifi
```

```bash
# Data (Dukascopy M1 → cache; ~1,300 files/hour because the server throttles; resumable)
python -m datafeed.dukascopy --symbol XAUUSDm
python -m datafeed.bars --symbol XAUUSDm          # build M15/H1/H4/D1 cache
python -m datafeed.crosscheck --symbol XAUUSDm    # vs Exness MT5, Jul 2024–Sep 2025 only

# Harness self-check — run after ANY change to backtest/, risk.manage_step, config costs
python -m backtest.selftest

# Walk-forward + Acceptance Gate for a Candidate in strategy/candidates/<name>.py (exports CANDIDATE)
python -m backtest.walkforward --candidate c1_donchian

# Bake-off: every Candidate through the gate, table by name (no ranking); never runs the holdout
python -m backtest.bakeoff

# Final Holdout: preflight only; --confirm spends it (ONCE per Candidate, writes holdout/<name>.json, commit it)
python -m backtest.holdout --candidate c1_donchian
```

```bash
# Live loop (step 7): only Strategies that passed the Final Holdout; params come from holdout/<name>.json
python -m execution.live --strategy c1_donchian             # DRY: logs signals, places nothing
python -m execution.live --strategy c1_donchian --execute   # places orders; refuses non-demo accounts
```

`backtest/selftest.py` is the test suite (no pytest dependency).

Packages cross-import each other, so run everything with `python -m` from the repo root.

## Platform constraints

- The `MetaTrader5` Python library runs **only on Windows** with an installed, logged-in MT5 terminal ("Algo Trading" enabled). `execution/mt5_client.py` guards the import (`MT5_AVAILABLE`) so other modules import on any OS.
- This Exness server runs **GMT+0** (measured 2026-07-23; don't assume for other brokers). Vietnam time = server + 7h.
- Telegram credentials come **only** from env vars `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID`. Never put secrets in `config/config.py`.

## What's kept (Infrastructure)

- **`config/config.py`**: `TradingConfig` holds only symbol/broker facts (price scale, measured Exness costs, magic number), risk limits, Telegram and execution settings. One instance per symbol in `CONFIGS`; `get_config(name)` resolves symbol/alias. **No strategy parameters live here.** Candidates own theirs (≤ 4, shared across symbols).
- **`risk/risk.py`**: `TradePlan`, `calc_lot_size`, `validate_rr`, `RiskGuard` (daily loss + heat), and the shared exit engine (`PositionState`, `manage_step` for bars, `manage_tick` for live, `trade_cost` with Wednesday triple swap). Backtest and live must both use this one engine.
- **`execution/mt5_client.py`**: thin MT5 wrapper (rates, tick, market/pending orders, `modify_sl`, `close_partial`). Only touches positions with the bot's `magic_number`.
- **`execution/journal.py`**: per-symbol CSV trade journal (`trades_<symbol>.csv`), with a `strategy` column and `closed_r(strategy)` → [(close time, R)] for the Kill-switch.
- **`execution/notifier.py`**: Telegram send-side notifier (`notify_alert` for Kill-switch trips, HTML-escaped).
- **`execution/telegram_control.py`**: inbound `/status`, `/pause [name]`, `/resume [name]`. Owner private chat only (others ignored without reply), commands older than 5 min dropped, offset persisted, non-blocking `poll_once()` for the live loop. No order placement by design.
- **`risk/killswitch.py`**: per-Strategy Kill-switch (DD ≥ 1.5× OOS max DD or 50-trade PF < 0.9, in R at 1% risk). Halt state persisted (atomic JSON under `state/`). `/resume` re-arms a halted Strategy from scratch; a merely paused one keeps its baseline. Limits come from `holdout/<name>.json` via `limits_from_holdout` (refuses Strategies that didn't pass the holdout).
- **`execution/live.py`**: step-7 live loop. `LiveRunner` calls the same `CANDIDATE.signals()` on closed bars and mirrors `backtest/engine.py` semantics (act after bar i closes; 1 position + 1 pending per slot; OCO sibling cancelled on fill; trail/flat after bar close; pending expiry open(j)+(expiry+1)·TF; entries > 15 min late are skipped). The broker is the source of truth (re-read every tick, per-slot magic `slot_magic(base, name)`); local state under `state/`. Kill-switch, RiskGuard, Telegram control wired in. `MT5Broker` is a thin adapter; selftest drives the runner with a fake broker.
- **`strategy/indicators.py`**: ATR, ADX, ATR percentile, `sign_flips` (last non-zero sign).
- **`datafeed/auxdata.py`** (ADR 0003): auxiliary series store `(observation, value, available_at, source)`, aligned by bar close; USD5 basket from Dukascopy, ALFRED first-release yields (`python -m datafeed.auxdata --fetch DFII10`, env `FRED_API_KEY`). Candidates declaring `aux` get `signals(bars, aux)` via `Candidate.compute()`; live refuses them until a live aux provider exists.

## Research harness

- **`datafeed/`**: `dukascopy.py` downloads M1 (single keep-alive connection, paced, since the server throttles). `bars.py` resamples to M15/H1/H4/D1, and **`load_bars()` hides the Final Holdout (bars ending after 2025-10-01) unless `include_holdout=True`.** `crosscheck.py` compares against Exness.
- **`strategy/candidate.py`**: the `Candidate` contract. `signals(bars, **params)` is vectorised and must be causal (row i uses bars ≤ i). The engine acts on it from bar i+1. The constructor enforces the ≤ 4-parameter Complexity Budget.
- **`backtest/engine.py`**: bar replay in R units (risk 1 unit per trade). Conservative fills: SL before TP, gap past SL fills at open, stop entries gap to open, no TP on a pending-order fill bar. Optional `oco_price/oco_sl/oco_tp` columns add an opposite-side pending leg; the first fill cancels the other. Optional `max_bars` is a time stop: close at the open of bar fill+max_bars (counted from the fill, also for pendings; result `TIME`); `execution/live.py` mirrors it by counting closed bars. Exits go through `risk.manage_step`.
- **`strategy/candidates/`**: `c1_donchian` (H4 channel breakout, ATR stop + ratcheting ATR trail), `c2_orb` (M15 London/NY 08:00-local opening range, OCO stop bracket, flat 16:00 New York), `c4_smc` (M15 sweep → CHoCH → limit retest of the broken swing). Each has synthetic checks in `backtest/selftest.py`. Bake-off #2 (ADR 0002): `a_zfade` (H4 z-score fade back to the SMA, `max_bars` time stop), `b_tsmom` (D1 sign of the L-day return, Sunday stub bars excluded), `c_intramom` (H1, London-morning move → NY session, flat 16:00 New York). Bake-off #3 (ADR 0003, take `aux`): `d_usdtrend` (H4, USD5 trend z → sell all on strong USD), `e_usdresid` (H4, fade the residual not explained by USD5), `f_realyield` (D1, DFII10 change z → sell all on rising real yield).
- **`backtest/bakeoff.py`**: runs every non-retired `strategy/candidates/*` through `walkforward.run` (`Candidate.retired` marks closed ones: C1/C2/C4 after Bake-off #1; the holdout refuses them; `--candidates` can still name them). Keeps every passer and never picks a "best" one. An errored Candidate means the Bake-off is NOT concluded (an error is not a fail). It only suggests the holdout command for passers.
- **`backtest/holdout.py`**: the only sanctioned use of `include_holdout=True`. Refuses unless the walk-forward passes, the code paths are committed, the holdout data is complete, and no marker exists. The marker in `holdout/` (git-tracked, never delete or edit it) is created before results are computed. Pass bar: PF ≥ 1.0, DD ≤ 15%.
- **`backtest/walkforward.py`**: causality check (`assert_causal`) and data-coverage check, then grid × symbols simulated once, rolling 3y→1y windows with t-stat parameter selection, stitched OOS, Acceptance Gate. The gate constants are fixed: **don't change them to let a Candidate pass.**

## Critical conventions

- **No lookahead / no repaint.** Signals are computed only on closed bars. Live and backtest must call the same Strategy function; never fork signal logic between them.
- **Never re-tune a Candidate against the Final Holdout** (Oct 2025 → Sep 2026) or after it fails the gate. See ADR 0001.
- **`dry_run` defaults to `True`.** Do not flip it as a side effect of other changes. Nothing trades real money until a Candidate passes the Acceptance Gate and the Forward Test.
- `magic_number` scopes which positions the bot manages; do not remove that filter.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for ngocquang2509/gold-smc (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
