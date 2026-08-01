# Folder restructure: group by technical role

## Goal

Mechanical refactor only — no logic changes. Move existing modules into role-based
folders and fix imports so the codebase is organized instead of flat at repo root.

## Target layout

```
config/
  __init__.py
  config.py          (moved as-is)
  scalp_config.py    (moved as-is)

strategy/
  __init__.py
  strategy.py        (moved as-is)
  scalp_strategy.py  (moved as-is)
  indicators.py      (moved as-is)
  smc/               (moved as-is, subpackage unchanged internally)
    __init__.py
    structure.py
    liquidity.py
    order_blocks.py
    fvg.py

risk/
  __init__.py
  risk.py            (moved as-is)

backtest/
  __init__.py
  backtest.py        (moved as-is)
  scalp_backtest.py  (moved as-is)

execution/
  __init__.py
  main.py            (moved as-is)
  scalp_main.py      (moved as-is)
  mt5_client.py      (moved as-is)
  journal.py         (moved as-is)
  notifier.py        (moved as-is)
  news.py            (moved as-is)
```

Stays at repo root: `README.md`, `CLAUDE.md`, `.gitignore`, `.env`, `data/` (input
CSVs), `docs/`, all output artifacts (`bot.log`, `scalp_bot.log`, `trades_*.csv`,
`scalp_trades_*.csv`, `backtest_trades.csv`, `backtest_equity.csv`,
`scalp_backtest_trades.csv`, `scalp_backtest_equity.csv`) — these are written via
relative paths resolved against cwd, and cwd stays the repo root regardless of where
the invoked script physically lives.

## Import changes

Every intra-repo `from X import Y` / `import X` reference to a moved module gets
rewritten to the new dotted path, e.g.:

- `from config import get_config` → `from config.config import get_config`
- `from strategy import analyze` → `from strategy.strategy import analyze`
- `from smc.structure import ...` → `from strategy.smc.structure import ...`
- `from risk import TradePlan, ...` → `from risk.risk import TradePlan, ...`
- `from mt5_client import MT5Client` → `from execution.mt5_client import MT5Client`
- `from journal import TradeJournal` → `from execution.journal import TradeJournal`
- `from notifier import TelegramNotifier` → `from execution.notifier import TelegramNotifier`
- `from news import in_news_blackout` → `from execution.news import in_news_blackout`
- `from indicators import adx, atr_percentile` → `from strategy.indicators import adx, atr_percentile`
- `from scalp_config import ...` → `from config.scalp_config import ...`
- `from scalp_strategy import ...` → `from strategy.scalp_strategy import ...`

`smc/liquidity.py` and `smc/order_blocks.py` use relative imports (`from .structure
import ...`) — these are unaffected since `smc/` moves as an intact subpackage under
`strategy/`.

Each new folder gets an empty `__init__.py` (no re-exports) so it's a plain
importable package — no behavior added.

## Running the tools after restructure

Because packages cross-import each other (e.g. `backtest/backtest.py` imports from
`config/`, `strategy/`, `risk/`), scripts must be invoked as modules from repo root
so Python resolves the top-level packages correctly:

```bash
python -m execution.main --symbol XAUUSDm
python -m execution.scalp_main --symbol XAUUSDm
python -m backtest.backtest --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
python -m backtest.scalp_backtest --from-mt5 --symbol XAUUSDm --years 2 --balance 10000
```

`--csv-ltf data/... --csv-htf data/...` flags in `backtest.backtest` keep working
unchanged (still relative to cwd = repo root).

## Docs to update

- `CLAUDE.md`: all commands (`python main.py ...` → `python -m execution.main ...`,
  etc.) and the "Architecture" section's file path references (e.g. `` `config.py` ``
  → `` `config/config.py` ``).
- `README.md`: any run commands / file paths it documents (includes a project
  structure tree diagram).
- `.claude/skills/add-new-symbol/SKILL.md`: hardcoded line references
  (`config.py:171-217`, `config.py:221-224`, `config.py:226-229`,
  `backtest.py:39-43`, `backtest.py:31-34`, `backtest.py:36-37`) and mentions of
  `main.py`/`mt5_client.py` need updating to new paths (and re-verified line
  numbers, since moving files doesn't change line numbers within a file but the
  path prefix does).
- `.claude/skills/backtest-tuning/SKILL.md`: run command
  `python backtest.py --from-mt5 --symbol <sym> --years 2` →
  `python -m backtest.backtest --from-mt5 --symbol <sym> --years 2`, plus line
  references `backtest.py:215`, `backtest.py:230-242`.
- `.claude/skills/smc-strategy-development/SKILL.md`: line references
  `main.py:266`, `backtest.py:183-184`, and its frontmatter `description`
  ("editing strategy.py or any file under smc/") should reflect the new
  `strategy/strategy.py` / `strategy/smc/` paths.

## Verification

No test suite exists. Validation is:
1. `python -m execution.main --symbol XAUUSDm` starts and reaches the same
   "waiting for next bar" log point as before the move (dry_run, so no live orders).
2. `python -m backtest.backtest --csv-ltf data/xauusd_m15.csv --csv-htf data/xauusd_h4.csv --symbol XAUUSDm`
   completes and produces `backtest_trades.csv`/`backtest_equity.csv` with the same
   row counts/results as a pre-refactor run on the same inputs (sanity check that
   analyze() logic wasn't perturbed).
3. Repeat step 2 for `python -m backtest.scalp_backtest --csv-m5 ... --symbol XAUUSDm`.
4. Grep the repo for any remaining flat-style imports (`from config import`, `from
   strategy import`, `from smc.` / `from smc import`, `from risk import`, `from
   mt5_client import`, `from journal import`, `from notifier import`, `from news
   import`, `from indicators import`, `from scalp_config import`, `from
   scalp_strategy import`) to confirm none were missed.
