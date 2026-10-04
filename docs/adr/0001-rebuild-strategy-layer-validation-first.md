# Rebuild the Strategy layer validation-first; keep the Infrastructure

The SMC and M5-scalp strategies were tuned on a single 2-year window (Jun 2024–Jul 2026, gold +76%), and their edge kept shrinking once the backtest mechanics were made honest or the period was split. Live results matched: the scalp stream lost as its own backtest predicted, and SMC traded too rarely to judge. We decided to replace only the Strategy layer and keep the strategy-agnostic Infrastructure (MT5 client, Notifier, cost model, journal, exit engine), because that layer wasn't causing the poor results.

The new Strategy is picked by a Bake-off of simple Candidates (Complexity Budget ≤ 4 parameters) against a fixed Acceptance Gate. That gate uses walk-forward windows over 8–10+ years of Dukascopy history, cross-checked against Exness, plus a 12-month Final Holdout evaluated once. One Shared Parameter Set is used across all symbols, in place of per-symbol tuning.

## Considered Options

- **Full rewrite (new repo/platform)**: rejected. It costs weeks, and the Infrastructure isn't the cause of the losses.
- **Simplify SMC and re-tune**: rejected as the default. It repeats the tune-until-it-passes loop on the same short regime. SMC may still enter the Bake-off as a Candidate under the same gate.
- **Per-symbol parameters**: rejected. It triples the number of fitted parameters, and the earlier EURUSD edge disappeared under honest testing.

## Consequences

- "No Candidate passed" is a valid outcome. The bot stays in dry run, and a second Bake-off must use new Candidate ideas; re-tuning failed Candidates on the same data is not allowed.
- The Exness cross-check covers Jul 2024 → Sep 2025 only. Its "good enough" thresholds were fixed on 2026-10-03 before any cross-check or gate result was seen (`datafeed/crosscheck.py` `THRESHOLDS`). Both TFs: coverage ≥ 98% of Exness bars, |median close diff| ≤ 0.10 ATR. M15: p95 |close diff| ≤ 0.50 ATR, return corr ≥ 0.95, bar-direction agreement ≥ 90%. H4: p95 ≤ 0.25 ATR, corr ≥ 0.98, direction ≥ 95%. A fail means investigating the pipeline (timezone, price scale, missing days), never loosening the thresholds. The Final Holdout is Oct 2025 → Sep 2026 and is evaluated exactly once. Its pass bar (PF ≥ 1.0, DD ≤ 15% at 1% risk, costs ×1.5) was fixed on 2026-10-03 before any Candidate result existed. A git-tracked marker in `holdout/` is written before results are computed, so an aborted run still counts as used.
- Backtests fill stops at the gap open price over weekends. There is no news filter: no historical calendar data, and it would cost a parameter.
- The Kill-switch requires inbound Telegram commands (`/status`, `/pause`, `/resume`, owner chat only, no order placement), so the Notifier gains a receive side.
- Build order: tag + remove legacy → data pipeline and cost stress → walk-forward gate harness → Candidates C1 (H4 Donchian trend), C2 (session opening-range breakout), C4 (stripped SMC) → Bake-off + Final Holdout → Telegram control + Kill-switch → Forward Test. The gate is built before any Candidate exists, so it can't be shaped to fit one.
- The old SMC and scalp code is removed from `main` and kept only under the git tag `legacy-v1`.

## Outcomes

- **2026-10-04, data validated.** Dukascopy M1 for 2014-01-01 → 2026-09-30 is complete for XAUUSD, EURUSD and GBPUSD (3,991/3,991 trading days each). The Exness cross-check passed every threshold on M15 and H4 for all three symbols (Exness Standard demo, Jul 2024 → Sep 2025). The tightest margin was GBPUSD M15 bar-direction agreement, 95.3% against ≥ 90%.
- **2026-10-04, Bake-off #1: 0/3 passed** (`reports/bakeoff-20261004-114359.json`, gitignored; numbers recorded here). Walk-forward OOS 2017 → Sep 2025, costs ×1.5:

  | Candidate | OOS PF | Max DD @ 1% | Profitable windows | OOS trades |
  |---|---|---|---|---|
  | C1 H4 Donchian | 0.84 | 45.4% | 2/9 | 465 |
  | C2 opening-range breakout | 0.92 | 99.0% | 2/9 | 6,781 |
  | C4 stripped SMC | 0.87 | 63.7% | 2/9 | 802 |

  The in-sample t-stat of the selected parameter set was mostly negative in every window, so no grid point had an edge to select. The Final Holdout was **not** used (no marker in `holdout/`) and stays available for future Candidates. Per the Consequences above, the bot stays in dry run, C1/C2/C4 are closed (no re-tuning, filters, or symbol subsets on this data), and a second Bake-off needs new Candidate ideas fixed before they are tested.
