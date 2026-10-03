# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An automated trading bot for the owner's own Exness MT5 account (XAUUSDm, EURUSDm, GBPUSDm), monitored over Telegram. Code comments and log messages are in Vietnamese; keep that convention when editing.

**The project is mid-rebuild.** The old SMC (H4→M15) and M5 scalp strategies were removed (recoverable from git tag `legacy-v1`) because their edge came from tuning on a single 2-year bull-market window. Read `docs/adr/0001-rebuild-strategy-layer-validation-first.md` before any strategy work. It fixes the Acceptance Gate, the Bake-off Candidates (C1 H4 Donchian trend, C2 session opening-range breakout, C4 stripped SMC), and the build order. Use the vocabulary in `GLOSSARY.md` (Edge, Out-of-sample, Final Holdout, Candidate, Complexity Budget, Shared Parameter Set, Kill-switch...).

Build order (ADR 0001): ~~1. tag + remove legacy~~ → 2. Dukascopy data pipeline + Cost Stress + gap fills → 3. walk-forward Acceptance Gate harness → 4. Candidates C1, C2, C4 → 5. Bake-off + Final Holdout (once) → 6. Telegram inbound commands + Kill-switch → 7. Forward Test on demo.

## Commands

```bash
# Install (no requirements file — install manually)
pip install MetaTrader5 pandas numpy certifi
```

There is currently no runnable live loop or backtest. Both are rebuilt in steps 2–3 and 6 above. There is no test suite, linter, or build step yet.

Packages cross-import each other, so run everything with `python -m` from the repo root.

## Platform constraints

- The `MetaTrader5` Python library runs **only on Windows** with an installed, logged-in MT5 terminal ("Algo Trading" enabled). `execution/mt5_client.py` guards the import (`MT5_AVAILABLE`) so other modules import on any OS.
- This Exness server runs **GMT+0** (measured 2026-07-23; don't assume for other brokers). Vietnam time = server + 7h.
- Telegram credentials come **only** from env vars `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID`. Never put secrets in `config/config.py`.

## What's kept (Infrastructure)

- **`config/config.py`**: `TradingConfig` holds only symbol/broker facts (price scale, measured Exness costs, magic number), risk limits, Telegram and execution settings. One instance per symbol in `CONFIGS`; `get_config(name)` resolves symbol/alias. **No strategy parameters live here.** Candidates own theirs (≤ 4, shared across symbols).
- **`risk/risk.py`**: `TradePlan`, `calc_lot_size`, `validate_rr`, `RiskGuard` (daily loss + heat), and the shared exit engine (`PositionState`, `manage_step` for bars, `manage_tick` for live, `trade_cost` with Wednesday triple swap). Backtest and live must both use this one engine.
- **`execution/mt5_client.py`**: thin MT5 wrapper (rates, tick, market/pending orders, `modify_sl`, `close_partial`). Only touches positions with the bot's `magic_number`.
- **`execution/journal.py`**: per-symbol CSV trade journal (`trades_<symbol>.csv`).
- **`execution/notifier.py`**: Telegram send-only notifier (inbound commands come in step 6).
- **`strategy/indicators.py`**: ATR, ADX, ATR percentile.

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
