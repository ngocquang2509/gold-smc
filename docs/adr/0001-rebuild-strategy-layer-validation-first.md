# Rebuild the Strategy layer validation-first; keep the Infrastructure

The SMC and M5-scalp strategies were tuned on a single 2-year window (Jun 2024–Jul 2026, gold +76%), and their edge kept shrinking once the backtest mechanics were made honest or the period was split. Live results matched: the scalp stream lost as its own backtest predicted, and SMC traded too rarely to judge. We decided to replace only the Strategy layer and keep the strategy-agnostic Infrastructure (MT5 client, Notifier, cost model, journal, exit engine), because that layer wasn't causing the poor results.

The new Strategy is picked by a Bake-off of simple Candidates (Complexity Budget ≤ 4 parameters) against a fixed Acceptance Gate. That gate uses walk-forward windows over 8–10+ years of Dukascopy history, cross-checked against Exness, plus a 12-month Final Holdout evaluated once. One Shared Parameter Set is used across all symbols, in place of per-symbol tuning.

## Considered Options

- **Full rewrite (new repo/platform)**: rejected. It costs weeks, and the Infrastructure isn't the cause of the losses.
- **Simplify SMC and re-tune**: rejected as the default. It repeats the tune-until-it-passes loop on the same short regime. SMC may still enter the Bake-off as a Candidate under the same gate.
- **Per-symbol parameters**: rejected. It triples the number of fitted parameters, and the earlier EURUSD edge disappeared under honest testing.

## Consequences

- "No Candidate passed" is a valid outcome. The bot stays in dry run, and a second Bake-off must use new Candidate ideas; re-tuning failed Candidates on the same data is not allowed.
- The Exness cross-check covers Jul 2024 → Sep 2025 only. The Final Holdout is Oct 2025 → Sep 2026 and is evaluated exactly once. Its pass bar (PF ≥ 1.0, DD ≤ 15% at 1% risk, costs ×1.5) was fixed on 2026-10-03 before any Candidate result existed. A git-tracked marker in `holdout/` is written before results are computed, so an aborted run still counts as used.
- Backtests fill stops at the gap open price over weekends. There is no news filter: no historical calendar data, and it would cost a parameter.
- The Kill-switch requires inbound Telegram commands (`/status`, `/pause`, `/resume`, owner chat only, no order placement), so the Notifier gains a receive side.
- Build order: tag + remove legacy → data pipeline and cost stress → walk-forward gate harness → Candidates C1 (H4 Donchian trend), C2 (session opening-range breakout), C4 (stripped SMC) → Bake-off + Final Holdout → Telegram control + Kill-switch → Forward Test. The gate is built before any Candidate exists, so it can't be shaped to fit one.
- The old SMC and scalp code is removed from `main` and kept only under the git tag `legacy-v1`.
