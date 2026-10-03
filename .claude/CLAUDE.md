# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Gold (XAUUSD) trading bot for MetaTrader 5 implementing Smart Money Concepts (SMC) on a multi-timeframe basis (H4 trend → M15 entry), with fixed 1%-per-trade risk. Code comments and log messages are in Vietnamese; keep that convention when editing.

## Commands

Each symbol has its own independent config in `config/config.py`, selected with `--symbol`
(`XAUUSDm`/`EURUSDm`/`GBPUSDm`, aliases `gold`/`eurusd`/`gbpusd`). Default is
`XAUUSDm`. Tuning one symbol never touches the other.

`execution/main.py` (live/demo loop) can also run **multiple symbols in one process** via
`--symbols a,b,c` (comma-separated, aliases accepted) — a single sequential
single-thread loop processes each symbol's own state (risk guard, journal,
cooldown) independently every poll tick. `--symbol` (singular) still works for
one symbol, unchanged. `backtest/backtest.py` remains single-symbol only (`--symbol`) —
multi-symbol is a live/demo-loop-only capability.

The project is organized by technical role — `config/`, `strategy/` (includes the
`smc/` primitives subpackage), `risk/`, `backtest/`, `execution/` — each an
importable package. Because these packages cross-import each other, everything is
run with `python -m` from the repo root, not as a bare script path.

```bash
# Install (no requirements file — install manually)
pip install MetaTrader5 pandas numpy certifi

# Live/demo loop (defaults to dry_run — logs signals, places no orders)
python -m execution.main --symbol XAUUSDm                  # 1 symbol (gold)
python -m execution.main --symbols EURUSDm,GBPUSDm         # nhiều symbol, 1 process

# Backtest from MT5 (Windows only) — 2 years
python -m backtest.backtest --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
python -m backtest.backtest --from-mt5 --symbol EURUSDm --years 2 --balance 10000

# Backtest from CSV (columns: time,open,high,low,close; time as ISO or epoch seconds)
python -m backtest.backtest --csv-ltf data/xauusd_m15.csv --csv-htf data/xauusd_h4.csv --symbol XAUUSDm
```

There is no test suite, linter, or build step. Backtesting **is** the validation workflow — run `backtest/backtest.py` (via `python -m backtest.backtest`) to check a strategy or config change end-to-end.

Backtest models **trading costs** (spread round-trip, overnight swap with Wednesday triple-swap, optional commission) per-symbol from `config/config.py` (`spread_points`, `commission_per_lot`, `swap_long/short_per_lot`); costs are charged per fill (partials included, pro-rata by lot) so PF/winrate/CAGR are net-of-cost. Add `--no-costs` to see gross. Defaults are conservative Standard-account estimates — tune to your broker.

### Scalping M5 stream (independent, separate process)

A fully independent scalping strategy — single-timeframe M5, EMA trend-pullback + RSI +
ATR SL/TP — lives in `config/scalp_config.py`/`strategy/scalp_strategy.py`/
`backtest/scalp_backtest.py`/`execution/scalp_main.py`. It shares NOTHING with the SMC
bot above (own magic numbers, own journal files `scalp_trades_<symbol>.csv`, own risk
%). See `docs/superpowers/specs/2026-07-31-scalp-m5-design.md` for the full design.

```bash
# Backtest
python -m backtest.scalp_backtest --from-mt5 --symbol XAUUSDm --years 2 --balance 10000

# Live/demo (dry_run=True by default in config/scalp_config.py)
python -m execution.scalp_main --symbol XAUUSDm
python -m execution.scalp_main --symbols EURUSDm,GBPUSDm
```

Can run alongside `execution.main` in a separate process on the same MT5 terminal — see the
spec's "Rủi ro cần xác minh" section before relying on this in live trading.
Untuned baseline (measured 2026-07-31, see `config/scalp_config.py` header) is **net-losing on
all 3 symbols** (PF 0.69–0.84) — deep parameter tuning is a separate follow-up, not yet
done.

## Platform constraints

- The `MetaTrader5` Python library runs **only on Windows** with an installed, logged-in MT5 terminal ("Algo Trading" enabled). `execution/mt5_client.py` guards the import (`MT5_AVAILABLE`) so the SMC/strategy modules can still be imported and backtested from CSV on any OS.
- Broker symbol names vary (XAUUSD / GOLD / XAUUSDm...). Set the broker's name in the relevant per-symbol config in `config/config.py`, or register a new symbol config (see below).
- `sessions` in `config/config.py` are in **MT5 server time**, not local time. Measured 2026-07-23: this Exness account's server runs **GMT+0** (many brokers are GMT+2/+3 — don't assume; probe it). So in **Vietnam time (UTC+7) = server + 7h**: gold session 08:00–17:00 → **VN 15:00–00:00**; EURUSD 13:00–18:00 → **VN 20:00–01:00**. Backtest data comes from the same server so tuning is consistent regardless. `execution/main.py` uses `server_time()` for the live gate, so the machine's own timezone is irrelevant — only keep it powered during those VN windows.

## Architecture

Data flows one direction: raw OHLC bars → SMC primitives → strategy decision → `TradePlan` → execution (live) or simulated fill (backtest). The same `strategy.analyze()` is the single source of truth for signals in **both** live and backtest paths — never fork signal logic between them.

- **`config/config.py`** — one `TradingConfig` dataclass (the schema) plus one **independent config instance per symbol** (`XAUUSD`, `EURUSD`) registered in `CONFIGS`. `get_config(name)` resolves a symbol/alias to a fresh copy; `execution/main.py`/`backtest/backtest.py` pass the chosen `cfg` down. Gold values are the dataclass defaults; `EURUSD` overrides the price-scale params (`price_digits`, `eq_tolerance`, `fvg_min_size_points`, `sl_buffer_points`, `min_sl_distance_points`) and has its own `magic_number`. To add a symbol: add an instance + a `CONFIGS` entry. Nothing else hardcodes strategy numbers.
- **`strategy/smc/`** — pure, stateless analysis over a pandas OHLC DataFrame. Each module returns dataclass lists:
  - `structure.py` — `find_swings`, `detect_structure` (BOS/CHoCH events + current trend), `current_trend_htf`. Foundation everything else references.
  - `liquidity.py` — liquidity pools from swings, `detect_sweeps` (stop hunts), `nearest_target_pool` (liquidity-based TP).
  - `order_blocks.py` — order blocks from structure events, optionally requiring a following imbalance (FVG).
  - `fvg.py` — fair value gaps (3-candle imbalance).
- **`strategy/strategy.py`** — `analyze(htf_df, ltf_df, cfg, balance, symbol_info) → TradePlan | None`. Orchestrates the SMC modules into the entry sequence (see below). Returns `None` when any condition fails.
- **`risk/risk.py`** — `calc_lot_size` (fixed-% position sizing for 100oz contracts), `validate_rr`, `TradePlan` dataclass, and `RiskGuard` (daily-loss + portfolio-heat limits).
- **`execution/mt5_client.py`** — thin MT5 wrapper: rates, tick, symbol info, market order, `modify_sl`, `close_partial`. Only touches bot-owned positions (filtered by `magic_number`).
- **`execution/main.py`** — live loop: session gate → new-closed-bar gate → `analyze` → order. Also `manage_open_positions` (breakeven at 1R, partial close at 1.5R) runs every poll.
- **`backtest/backtest.py`** — bar-by-bar replay that slices data up to each bar and calls `analyze` identically to live; simulates SL/TP/BE/partial fills.

## Entry sequence (the core algorithm)

`strategy.analyze` only produces a signal when, in order:
1. **H4 trend** is bullish or bearish (`neutral` → no trade).
2. A **liquidity sweep against the trend** exists (sellside sweep before buy / buyside sweep before sell) — gated by `require_sweep`.
3. A **CHoCH/BOS in the trend direction occurs *after* that sweep** (confirmation).
4. Price is **retesting an OB or FVG** born from that confirmation (`entry_mode` selects which zones qualify).
5. **SL** sits beyond the OB/sweep level + `sl_buffer_points`; **TP** is the nearest opposing liquidity pool, falling back to `tp_rr` fixed R:R if that doesn't meet `min_rr`.
6. **R:R ≥ `min_rr`** and computed lot > 0, else no trade.

## Critical conventions

- **No lookahead / no repaint.** Live and backtest both drop the still-forming bar (`iloc[:-1]` in `execution/main.py`, slice up to `i` in `backtest/backtest.py`). Analysis runs only on closed bars, and only once per newly closed LTF bar. Preserve this whenever touching the loops or SMC scanning windows.
- **`dry_run` defaults to `True`.** Do not flip it as a side effect of other changes. The intended progression is backtest → dry run → demo → live.
- SMC dataclasses carry a positional `index` into the DataFrame; keep index bookkeeping consistent when slicing (backtest passes slices, so an event's `index` is relative to the slice it was found in).
- `magic_number` scopes which positions the bot manages; do not remove that filter.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for ngocquang2509/gold-smc (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-label vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
