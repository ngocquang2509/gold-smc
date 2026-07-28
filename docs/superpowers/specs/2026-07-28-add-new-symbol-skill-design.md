# Skill: add-new-symbol — Design

Date: 2026-07-28

## Purpose

Provide a rigid, linear checklist for adding a new symbol/broker to
gold-smc-bot, so that the config, registry, offline-backtest support, and
real trading costs are all wired up correctly — following the exact pattern
already established for `XAUUSD`/`EURUSD` in `config.py` — and a first
baseline backtest is run before the task is considered done.

This is the third of the planned skills for this repo, after
`backtest-tuning` and `smc-strategy-development` (both already built at
`.claude/skills/`). This skill hands off to `backtest-tuning` at the end,
but only for its Step 1+2 (baseline + H1/H2 split) — there is no "before"
state to compare against for a brand-new symbol, so the full keep/revert
comparison loop doesn't apply here.

## Location & activation

- `.claude/skills/add-new-symbol/SKILL.md` (project-scoped)
- Type: rigid, linear checklist (no branching parts, unlike
  `smc-strategy-development`'s Part A/B split) — the process is inherently
  sequential.
- Triggers on: requests to add a new symbol or broker to the bot.

## Ground truth from the codebase (verified, do not re-derive differently)

- `config.py` (245 lines): `TradingConfig` is the schema dataclass (defaults
  = gold's tuned values). Each symbol gets its own instance
  (`XAUUSD = TradingConfig(...)`, `EURUSD = TradingConfig(...)`) overriding
  only what differs, registered in `CONFIGS = {"XAUUSDm": XAUUSD, "EURUSDm": EURUSD}`
  and `_ALIASES` (friendly CLI names like `gold`/`eurusd`). `get_config(name)`
  resolves alias/symbol and returns `replace(CONFIGS[key])` — a fresh copy.
- Per-symbol overrides that matter for price scale: `price_digits`,
  `eq_tolerance`, `fvg_min_size_points`, `sl_buffer_points`,
  `min_sl_distance_points` — EURUSD's block (`config.py:171-217`) shows the
  concrete before/after for a 5-digit forex pair vs. gold's 2-digit scale.
  `magic_number` must be unique per symbol (`XAUUSD`=20260723,
  `EURUSD`=20260724) — it's how `main.py`/`mt5_client.py` scope which live
  positions belong to which symbol's bot instance.
- `sessions` are in **MT5 server time**, not local time (documented in
  `CLAUDE.md`: this account's server is GMT+0, gold trades VN 15:00-00:00,
  EURUSD VN 20:00-01:00 — a different broker may run a different server
  offset; don't assume, probe it).
- `backtest.py:31-34` defines `SYMBOL_INFO_DEFAULT` (gold's contract info);
  `backtest.py:39-43` has a separate `SYMBOL_INFO` dict (`contract_size`,
  `volume_min/step/max`, `point`, `digits`) keyed by symbol name, used only
  for **CSV-offline backtesting** (`backtest.py:296`,
  `SYMBOL_INFO.get(cfg.symbol)`). Live/MT5-sourced runs get this data
  automatically from the broker via `mt5_client.get_symbol_info()`
  (`mt5_client.py:73-76`) — no manual entry needed for that path. The
  in-code comment at `backtest.py:36-37` warns that a wrong `contract_size`
  corrupts CAGR/max-DD (absolute PnL scale), so this must be looked up, not
  guessed, whenever CSV-offline backtesting will be used for the new symbol.
- Real trading costs (`spread_points`, `commission_per_lot`,
  `swap_long_per_lot`, `swap_short_per_lot`) are measured from the broker,
  not estimated — both `XAUUSD` and `EURUSD` blocks carry a comment stating
  the measurement date and source (e.g. "chi phí THẬT đo từ MT5 Exness ngày
  2026-07-23"). New symbols must follow the same convention: look up
  spread/swap via MT5 Market Watch → symbol → Properties, and record the
  measurement date in a comment.
- `main.py` is fully generic over `cfg` — grepped for symbol-specific
  branches and found none; it only ever references `cfg.symbol`,
  `cfg.magic_number`, etc. Adding a new symbol requires **no changes to
  `main.py` or `mt5_client.py`** — only `config.py` and, if CSV-offline
  backtesting is needed, `backtest.py`'s `SYMBOL_INFO`.

## Workflow

1. **Gather broker/symbol facts.** Exact symbol name as it appears in MT5
   Market Watch (may carry a broker-specific suffix like `m` or `.m` —
   right-click → "Show All" if it's not visible). Price digits, contract
   size, volume min/step/max, point size — via MT5 Symbol Properties, or
   `mt5_client.get_symbol_info()` if a live connection is available.

2. **Create a new `TradingConfig` instance** in `config.py`, following only
   the **structural pattern** of the `EURUSD` block (`config.py:171-217`) —
   NOT a copy-paste of its contents. The `EURUSD` block mixes two different
   kinds of overrides: (a) price-scale fields that are mechanical
   consequences of the new symbol's price unit (`price_digits`,
   `eq_tolerance`, `fvg_min_size_points`, `sl_buffer_points`,
   `min_sl_distance_points`), and (b) EURUSD-specific *tuned* values from its
   own backtest history (`min_rr=2.5`, `tp_rr=3.0`, `entry_expiry_bars=48`,
   `htf_trend_max_age_bars=40`, `weekend_guard_hours=3.0`,
   `risk_per_trade_pct=2.0`, and their dated rationale comments). Only carry
   over category (a) plus a **magic_number not already used** by any
   existing symbol in `CONFIGS`, and `sessions` matching the new symbol's
   liquid trading hours in **server time** (verify the server's UTC offset
   for this broker — don't assume it matches the Exness GMT+0 already
   documented for the existing symbols). Leave every tuning parameter
   (min_rr, tp_rr, entry_expiry_bars, etc.) at `TradingConfig`'s schema
   default — those get tuned later via `backtest-tuning`, not guessed now
   from an unrelated symbol's history.

3. **Register in `CONFIGS` and `_ALIASES`** (`config.py:221-229`): add the
   new symbol key and one or more friendly CLI aliases. Check both the
   `magic_number` (against `CONFIGS`) and the chosen alias string(s)
   (against `_ALIASES`'s existing keys: `gold`, `xau`, `xauusd`, `vang`,
   `eur`, `eurusd`) for collisions before writing — a duplicate alias
   silently shadows or gets shadowed by an existing one.

4. **Add a `SYMBOL_INFO` entry in `backtest.py`** (`backtest.py:39-42`) —
   only required if CSV-offline backtesting will be used for this symbol;
   live/MT5-sourced backtests and live trading get this from the broker
   automatically.

5. **Measure real trading costs**: spread (median during the intended
   trading session) and overnight swap (buy/sell) via MT5 Market Watch →
   symbol → Properties. **Swap as shown in MT5's Properties dialog is
   usually reported in points, not currency** — `XAUUSD`'s own comment
   documents this exact conversion: `swap_long_per_lot=-49.04, # swap_long
   thật (POINTS -490.4 × $0.10/point)`. Convert points to USD/lot using the
   symbol's tick value before writing `swap_long_per_lot`/
   `swap_short_per_lot` — do not paste the raw points figure directly, that
   silently corrupts cost modeling by roughly the tick-value ratio. Record
   the measurement date in a comment, matching the existing convention in
   the `XAUUSD`/`EURUSD` blocks.

6. **First baseline.** Run Step 1 + Step 2 of the `backtest-tuning` skill
   (baseline full-2y run + H1/H2 split) to get the first PF/winrate/CAGR
   numbers for the new symbol, then offer Step 7 of that same skill
   (opt-in persistence to a memory file, e.g. a new
   `<symbol>-baseline.md`) since a first baseline is exactly the kind of
   result worth recording — follow `backtest-tuning`'s own opt-in framing,
   don't persist without asking. There is no before/after comparison or
   keep/revert decision here — nothing preceded this baseline. Do not run
   `backtest-tuning`'s Steps 3-6 (apply-change / re-run / compare /
   keep-revert).

7. **Update `backtest-tuning/SKILL.md`'s Step 1 wording.** That skill's Step
   1 currently says "Confirm the symbol with the user if not already
   stated: `XAUUSDm`/`gold` or `EURUSDm`/`eurusd`" — a hardcoded two-symbol
   list. Once a new symbol is registered, add it to that list (or
   generalize the wording to reference `config.py`'s `CONFIGS` instead of
   naming symbols directly) so `backtest-tuning`'s own checklist doesn't go
   stale as more symbols are added.

## Edge cases / out of scope

- **Symbol not visible in Market Watch**: enable "Show All" before trying to
  read its properties.
- **Wrong `contract_size`**: corrupts CAGR/max-DD (absolute PnL scale) per
  the existing warning comment in `backtest.py` — must be looked up from the
  broker, never guessed or copied from another symbol.
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
  `main.py`/`mt5_client.py` (verified unnecessary — both are fully generic
  over `cfg`).
