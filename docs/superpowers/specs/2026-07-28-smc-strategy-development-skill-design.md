# Skill: smc-strategy-development — Design

Date: 2026-07-28

## Purpose

Provide a rigid checklist for changes to `strategy.py` or `smc/*.py` in the
gold-smc-bot project, so that (a) the three invariants that keep live and
backtest signal-identical are never silently broken, and (b) any new SMC
primitive (e.g. breaker block, mitigation block) follows the existing
dataclass/module convention and is deliberately wired into the entry
sequence rather than silently inserted.

This is the second of several planned skills for this repo — the first,
`backtest-tuning`, is the mandatory validation step this skill hands off to
at the end. Later planned: live/demo operation checklist, adding a new
symbol/broker.

## Location & activation

- `.claude/skills/smc-strategy-development/SKILL.md` (project-scoped)
- Type: rigid checklist — steps followed in order, one TodoWrite item per
  step.
- Triggers on: any edit to `strategy.py` or any file under `smc/` (structure,
  liquidity, order_blocks, fvg, or a new module).
- Does **not** trigger on: pure `config.py` edits with no `strategy.py`/
  `smc/*` change — that's `backtest-tuning`'s territory.

## Structure: two parts

### Part A — Protecting invariants (always applies, even for small edits)

**Before editing:** read `strategy.py` (194 lines) to determine which step
of the real entry sequence the change touches. The sequence, grounded in the
actual numbered comments in the code (not the simplified 3-step summary in
`CLAUDE.md`'s docstring header), is:

1. **(#8)** HTF trend (`current_trend_htf`) — neutral → no trade; also gated
   by a max-trend-age check (`cfg.htf_trend_max_age_bars`, documented as
   `#8` in `smc/structure.py`'s `current_trend_htf` docstring) that forces
   `neutral` when the last BOS/CHoCH is stale.
2. Liquidity sweep against trend (`detect_sweeps`), gated by `require_sweep`.
3. CHoCH/BOS confirmation after the sweep, matching trend direction.
4. **(#9)** Max setup-age cap — sweep→confirm chain must still be "fresh"
   (`cfg.max_setup_age_bars`).
5. Entry zone: OB or FVG born from the confirming event
   (`_pick_entry_zone`, `cfg.entry_mode`).
6. **(#4)** Limit placed at the zone edge (not market at close) — only
   valid if price hasn't already run through the zone.
7. **(#6)** Discount/premium filter via equilibrium (50% of recent swing
   range), gated by `cfg.require_discount_premium`.
8. **(#5)** SL anchored to the real sweep wick extreme (`last_sweep.extreme`),
   not the touched pool level; TP from nearest liquidity pool or fixed R:R
   fallback, capped by `cfg.max_rr`.
9. Min-SL-distance filter, R:R ≥ `cfg.min_rr` check, position sizing.

**Three invariants that must never break:**

1. **No-lookahead.** `analyze()` only ever receives closed bars. Live drops
   the forming bar via `.iloc[:-1]` (`main.py:266`); backtest slices
   `ltf.iloc[max(0, i+1-cfg.ltf_bars):i+1]` and `htf[htf.index<=now]`
   (`backtest.py:183-184`). Any change to scanning windows inside
   `smc/*.py` must preserve this — no primitive may look at a bar beyond
   what its caller sliced.
2. **`analyze()` is the single source of truth for signals.** Live and
   backtest must both call the same `strategy.analyze()` — never fork
   signal logic between the two paths.
3. **`.index` on every SMC dataclass is relative to the slice passed in for
   that call, not absolute across full history.** Confirmed in `FVG.index`,
   `OrderBlock.index`, `StructureEvent.index` — backtest re-slices the
   DataFrame every bar, so these indices only have meaning compared against
   each other *within the same `analyze()` call*. Comparing an `index` from
   one call against a stored value from a previous call is a bug.
   **This also applies across timeframes within a single call**: `strategy.py`
   derives `htf_swings`/`htf_pools` from `htf_df` (e.g. `strategy.py:112`,
   `:126`) whose `.index` values live in the HTF slice's own coordinate
   space — never comparable to the LTF `swings`/`events`/`obs`/`fvgs`
   indices used elsewhere in the same `analyze()` call, even though both
   were computed in that one call.

**After editing:** invoke the `backtest-tuning` skill (via the `Skill` tool,
not just a prose reminder) to validate the change via a real before/after
backtest. The task is not done until that skill's keep/revert decision has
been reached — do not report "done" on the code change alone. Concrete
terminal actions:
- **Keep**: report the change as done, including the keep/revert rationale
  from `backtest-tuning`'s output.
- **Revert-leaning or overfit warning**: do not auto-revert the edit. Report
  the concern to the user with the comparison numbers and let them decide
  whether to revert, adjust the approach and re-loop through Part A, or
  accept the tradeoff explicitly.

### Part B — Adding a new SMC primitive

Applies when introducing an entirely new primitive (e.g. breaker block,
mitigation block) — not for small edits to existing logic (those only need
Part A). The boundary is about **invalidation semantics, not the dataclass
identity**: if the new detection logic reuses an existing dataclass
unchanged (same fields, same invalidation/aging rule) and is just a new
scanning heuristic, that's Part A only. If it introduces a new dataclass, OR
reuses an existing one but with materially different invalidation semantics
(e.g. a "mitigation block" that inverts an `OrderBlock`'s validity condition
instead of reusing `order_blocks.py`'s test-and-invalidate logic as-is),
that's Part A + Part B.

**The existing convention**, verified directly against `smc/fvg.py` and
`smc/order_blocks.py`:

```python
@dataclass
class NewPrimitive:
    index: int             # position in the df/slice passed in (invariant 3)
    direction: str          # "bullish" | "bearish"
    top: float
    bottom: float
    time: pd.Timestamp
    invalidated: bool = False   # name varies by domain: mitigated/filled/...
    # extra domain-specific fields are expected and fine — e.g. OrderBlock
    # also carries `origin_event: str` ("BOS" | "CHOCH"); don't over-fit to
    # exactly these six fields.

def find_new_primitives(df: pd.DataFrame, ..., max_age_bars: int = 100) -> list[NewPrimitive]:
    items: list[NewPrimitive] = []
    # Extract only the OHLC columns you actually need as numpy arrays, not
    # DataFrame.iloc loops — order_blocks.py needs all four (o, h, l, c);
    # fvg.py only needs h, l, c. Don't copy-paste an unused column.
    h, l, c = (df[k].values for k in ("high", "low", "close"))
    n = len(df)
    for ...:
        items.append(NewPrimitive(...))
    # Mark invalidated (price tested/pierced through) or aged out
    # (n - item.index > max_age_bars), same pattern as fvg.py/order_blocks.py.
    for it in items:
        ...
    return [it for it in items if not it.invalidated]
```

**Integration steps into `strategy.analyze()`:**

1. Import the new module at the top of `strategy.py`.
2. Add any related tuning parameters to `TradingConfig` (`config.py`) — per
   symbol if gold/EURUSD should behave differently.
3. Decide explicitly which of the 9 entry-sequence steps above this
   primitive replaces or augments (most often step 5, entry-zone selection)
   — don't silently add a new filter condition without saying so in
   `strategy.py`'s module docstring.
4. Update `strategy.py`'s module docstring (currently a 3-step summary) to
   reflect the new primitive's role.

## Edge cases / out of scope

- **Pure `config.py` edits** with no `strategy.py`/`smc/*` change: this
  skill doesn't trigger — use `backtest-tuning` directly.
- **Adding a field to an existing dataclass** (not a wholly new primitive):
  apply Part A only, skip Part B.
- **Ambiguous integration point** (unclear which entry-sequence step a new
  primitive replaces/augments): stop and ask the user — don't decide
  unilaterally where it slots in.
- Out of scope for this skill entirely: live/demo operation checklist,
  adding a new symbol/broker to `CONFIGS` — separate skills.
