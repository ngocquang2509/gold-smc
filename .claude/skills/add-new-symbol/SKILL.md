---
name: add-new-symbol
description: Use when adding a new trading symbol or broker to gold-smc-bot. Rigid linear checklist — gather broker facts, create a TradingConfig instance (structural pattern only, not a copy of another symbol's tuned values), register it, optionally wire up CSV-offline backtest support, measure real trading costs, and run a first baseline via the backtest-tuning skill. Does not cover deep parameter tuning beyond that first baseline (repeated backtest-tuning invocations) or any strategy/SMC logic change.
---

# Add New Symbol

A linear, sequential checklist for adding a new symbol/broker to the bot.
Unlike `smc-strategy-development`, this has no branching parts — the
process is inherently one thing after another.

Create one TodoWrite item per step and follow them in order.

## Step 1: Gather broker/symbol facts

Exact symbol name as it appears in MT5 Market Watch — may carry a
broker-specific suffix like `m` or `.m` (right-click → "Show All" if it's
not visible). Collect: price digits, contract size, volume min/step/max,
point size — via MT5 Symbol Properties, or `execution/mt5_client.get_symbol_info()`
if a live connection is available.

## Step 2: Create a new `TradingConfig` instance

Add a new instance to `config/config.py`, following only the **structural
pattern** of the `EURUSD` block — NOT a copy-paste of its contents (line
numbers drift as the file is tuned; search for `EURUSD = TradingConfig(`).
That block mixes two different kinds of overrides:

- **(a) Structural price-scale fields** — mechanical consequences of the new
  symbol's price unit: `price_digits`, `eq_tolerance`,
  `fvg_min_size_points`, `sl_buffer_points`, `min_sl_distance_points`.
- **(b) EURUSD-specific *tuned* values** from its own backtest history:
  `min_rr=2.5`, `tp_rr=3.0`, `entry_expiry_bars=48`,
  `htf_trend_max_age_bars=40`, `weekend_guard_hours=3.0`,
  `risk_per_trade_pct=2.0`, and their dated rationale comments.

Only carry over category (a), plus:
- A **magic_number not already used** by any existing symbol in `CONFIGS`.
- `sessions` matching the new symbol's liquid trading hours in **server
  time** — verify this broker's server UTC offset; don't assume it matches
  the Exness GMT+0 already documented for the existing symbols.

Leave every tuning parameter (`min_rr`, `tp_rr`, `entry_expiry_bars`, etc.)
at `TradingConfig`'s schema default. Those get tuned later via
`backtest-tuning`, not guessed now from an unrelated symbol's history.

## Step 3: Register in `CONFIGS` and `_ALIASES`

Add the new symbol key to `CONFIGS` and one or more friendly CLI aliases to
`_ALIASES` (both in `config/config.py`, near the bottom of the file). Check
both the **magic_number** (against `CONFIGS`'s existing values) and the
chosen **alias string(s)** (against `_ALIASES`'s existing keys: `gold`, `xau`,
`xauusd`, `vang`, `eur`, `eurusd`) for collisions before writing — a
duplicate alias silently shadows an existing symbol's shortcut, and a
duplicate magic_number would cross-contaminate live position management
between symbols.

## Step 4: Add a `SYMBOL_INFO` entry in `backtest/backtest.py` (only if needed)

Only required if CSV-offline backtesting (`--csv-ltf`/`--csv-htf`) will be
used for this symbol — live/MT5-sourced backtests (`--from-mt5`) and live
trading get this data automatically from the broker via
`execution/mt5_client.get_symbol_info()`. Add an entry to the `SYMBOL_INFO` dict
(sibling to `SYMBOL_INFO_DEFAULT` just above it) with `contract_size`,
`volume_min`, `volume_step`, `volume_max`, `point`, `digits` from Step 1's
gathered facts. A wrong `contract_size` corrupts CAGR/max-DD (absolute PnL
scale, per that dict's own warning comment) — it must be looked up, never
guessed or copied from another symbol.

## Step 5: Measure real trading costs

Spread (median during the intended trading session) and overnight swap
(buy/sell) via MT5 Market Watch → symbol → Properties.

**Swap as shown in MT5's Properties dialog is usually reported in points,
not currency** — `XAUUSD`'s own comment documents this exact conversion:
`swap_long_per_lot=-49.04, # swap_long thật (POINTS -490.4 × $0.10/point)`.
Convert points to USD/lot using the symbol's tick value before writing
`swap_long_per_lot`/`swap_short_per_lot` — do not paste the raw points
figure directly, that silently corrupts cost modeling by roughly the
tick-value ratio.

Record the measurement date in a comment, matching the existing convention
in the `XAUUSD`/`EURUSD` blocks (e.g. "chi phí THẬT đo từ MT5 [broker] ngày
YYYY-MM-DD").

## Step 6: First baseline

Run Step 1 + Step 2 of the `backtest-tuning` skill (baseline full-2y run +
H1/H2 split) to get the first PF/winrate/CAGR numbers for the new symbol.
Then offer Step 7 of that same skill (opt-in persistence to a memory file,
e.g. a new `<symbol>-baseline.md`) since a first baseline is exactly the
kind of result worth recording — follow `backtest-tuning`'s own opt-in
framing, don't persist without asking.

There is no before/after comparison or keep/revert decision here — nothing
preceded this baseline. Do not run `backtest-tuning`'s Steps 3-6
(apply-change / re-run / compare / keep-revert) — only its Step 1, 2, and
optionally 7.

## Step 7: Update `backtest-tuning/SKILL.md`'s Step 1 wording

That skill's Step 1 lists the current symbol registry inline (as of this
writing: `XAUUSDm`/`gold`, `EURUSDm`/`eurusd`, `GBPUSDm`/`gbpusd`) as a
hardcoded convenience list. Now that a new symbol is registered, add it to
that list (or generalize the wording to reference `config/config.py`'s
`CONFIGS` instead of naming symbols directly) so `backtest-tuning`'s own
checklist doesn't mislead future invocations into thinking the registry is
smaller than it actually is.

## Edge cases / out of scope

- **Symbol not visible in Market Watch**: enable "Show All" before trying to
  read its properties.
- **Wrong `contract_size`**: corrupts CAGR/max-DD (absolute PnL scale) per
  the existing warning comment in `backtest/backtest.py` — must be looked up
  from the broker, never guessed or copied from another symbol.
- **Duplicate `magic_number`**: would cause live position management to
  cross-contaminate between symbols — check uniqueness against `CONFIGS`
  before writing.
- **Duplicate CLI alias**: check the new alias(es) against `_ALIASES`'s
  existing keys before writing — a collision silently shadows an existing
  symbol's shortcut.
- **Swap entered in raw points instead of converted currency**: MT5's
  Properties dialog reports swap in points; paste the tick-value-converted
  USD/lot figure into `swap_long_per_lot`/`swap_short_per_lot`, not the raw
  points number.
- **Copy-pasting EURUSD's tuned parameters** (`min_rr`, `tp_rr`,
  `entry_expiry_bars`, `htf_trend_max_age_bars`, `weekend_guard_hours`,
  `risk_per_trade_pct`, etc.) into a new symbol's config: these are
  EURUSD-specific tuning results, not structural/price-scale requirements —
  leave them at schema defaults and tune later via `backtest-tuning`.
- **Session hours in local time instead of server time**: `sessions` must be
  in MT5 server time; a different broker may run a different UTC offset
  than the Exness GMT+0 already documented for this repo's existing
  symbols — probe it, don't assume.
- Out of scope: deep parameter tuning beyond the first baseline (that's
  repeated invocations of `backtest-tuning`), and any change to
  `execution/main.py`/`execution/mt5_client.py` (verified unnecessary — both
  are fully generic over `cfg`).
