# Gold SMC Bot

An automated trading bot that trades its owner's own MT5 account (Exness) and reports to Telegram for monitoring. Being rebuilt around a stricter idea of what counts as a proven strategy.

## Language

### Validation

**Edge**:
A strategy's positive expected value **net of costs** (spread, swap, commission), shown on data the strategy was not tuned on.
_Avoid_: "good backtest", "profitable config"

**In-sample**:
Historical data used to design or tune a strategy. Results on it are evidence of fit, not of edge.
_Avoid_: training data, backtest period

**Out-of-sample**:
Historical data the strategy (including its parameters) never saw during design or tuning. Only these results count toward the Acceptance Gate.
_Avoid_: test set, validation period, H2

**Walk-forward Window**:
One 3-year in-sample slice followed by the 1-year out-of-sample slice right after it. In each window the parameters with the best in-sample t-stat of trade R (all symbols pooled) are carried into the out-of-sample year. The strategy is judged on the chain of these out-of-sample slices.

**Acceptance Gate**:
The fixed bar a strategy must clear before it may trade real money: out-of-sample PF ≥ 1.25, max drawdown ≤ 15% at the 1%-per-trade risk ceiling, profitable in ≥ 70% of Walk-forward Windows, ≥ 200 out-of-sample trades (all symbols pooled), and then a pass on the Final Holdout. All figures are net of Cost Stress. Win rate and return are reported, not gated.
_Avoid_: target, KPI

**Final Holdout**:
The most recent 12 months of history, kept unseen until a Candidate has passed the walk-forward part of the Acceptance Gate. It is evaluated exactly once, with the parameters chosen on the 3 years just before it. It passes at PF ≥ 1.0 and max drawdown ≤ 15% at 1% risk, net of Cost Stress: it checks that the Edge is still alive, since 12 months is too small a sample to measure it. A failure there rejects the Candidate; it is never re-tuned against it.
_Avoid_: test period, forward test

**Candidate**:
A Strategy under evaluation that has not yet cleared the Acceptance Gate. Most Candidates are expected to fail.

**Bake-off**:
Running several Candidates through the same Acceptance Gate on the same data and keeping only those that pass. Zero passes is a valid result.

**Complexity Budget**:
The cap on a Strategy's tunable parameters (at most 4). A filter stays only if it improves out-of-sample results; nothing ships as a default-off flag.

**Shared Parameter Set**:
One set of Strategy parameters used unchanged on every symbol, with price distances expressed in ATR units so symbols are comparable.
_Avoid_: per-symbol tuning

**Cost Stress**:
Charging 1.5× the current Exness spread and swap across all history. Every Acceptance Gate figure is measured under Cost Stress.

**Forward Test**:
At least 8 weeks and 30 trades on a demo account after a Candidate clears the Acceptance Gate. It checks that live fills match a backtest replay (average slippage < 0.3R) and that the Infrastructure doesn't fail. It does not check profitability.
_Avoid_: paper trading, dry run (dry run means logging signals with no orders at all)

### System

**Strategy**:
The replaceable decision logic that turns closed bars into trade intents. The part being rebuilt.
_Avoid_: bot, system, config

**Infrastructure**:
The strategy-agnostic parts kept from the old system: broker client, Telegram notifier, cost model, trade journal, and exit-management engine.
_Avoid_: framework, core

**Notifier**:
Telegram output used for monitoring the bot and stopping it. It is not a signal service for manual traders.
_Avoid_: signal channel, alerts service

**Portfolio**:
Every Strategy that has passed the Acceptance Gate, running together, each with an equal share of one risk budget. Risk per trade is set so the out-of-sample max drawdown stays ≤ 15%, never above 1%, and is halved for the first 3 live months.

**Kill-switch**:
An automatic halt on new entries when live drawdown reaches 1.5× the out-of-sample max drawdown or the rolling 50-trade PF falls below 0.9. It resumes only on a manual command.
_Avoid_: circuit breaker, daily loss limit (that is the separate per-day RiskGuard)
