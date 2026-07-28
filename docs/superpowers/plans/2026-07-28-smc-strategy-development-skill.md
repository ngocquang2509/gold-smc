# SMC Strategy Development Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author `.claude/skills/smc-strategy-development/SKILL.md`, a two-part rigid checklist that (A) protects the invariants that keep `strategy.py`/`smc/*.py` changes safe and mandates a `backtest-tuning` handoff afterward, and (B) gives a verified template for adding an entirely new SMC primitive.

**Architecture:** A single Markdown skill file with YAML frontmatter followed by Part A (entry-sequence map + 3 invariants + after-editing handoff) and Part B (dataclass/module convention template for new primitives), plus edge cases. No new Python modules — this only documents how to work safely in the existing `strategy.py`/`smc/` code.

**Tech Stack:** Markdown (skill file). No code is created; all code snippets in the plan are illustrative templates already grounded in and verified against `smc/fvg.py` and `smc/order_blocks.py`.

**Reference:** Spec at `docs/superpowers/specs/2026-07-28-smc-strategy-development-skill-design.md`. This repo is not a git repository — **do not run `git init`** or any git commands; skip all commit steps and just save files directly, consistent with how `backtest-tuning` (the sibling skill already built at `.claude/skills/backtest-tuning/SKILL.md`) was implemented.

---

## Ground truth (verified against the real codebase — do not re-derive differently)

- `strategy.py` is 194 lines. The entry sequence's numbered inline comments are:
  `#8` (`smc/structure.py`'s `current_trend_htf` docstring, invoked at `strategy.py:29`) — HTF trend + max-trend-age gate;
  `#9` (`strategy.py:58-66`) — max setup-age cap;
  `#4` (`strategy.py:79-92`) — limit at zone edge;
  `#6` (`strategy.py:94-103`) — discount/premium filter;
  `#5` (`strategy.py:105-133`) — SL anchored to sweep wick extreme.
- No-lookahead: `main.py:266` is `analyze(htf_df.iloc[:-1], ltf_df.iloc[:-1], cfg, balance, symbol_info)`. `backtest.py:183-184` is `ltf_slice = ltf.iloc[max(0, i + 1 - cfg.ltf_bars): i + 1]` and `htf_slice = htf[htf.index <= now].iloc[-cfg.htf_bars:]`.
- `.index` on `FVG`, `OrderBlock`, `StructureEvent` is positional within whatever DataFrame/slice was passed to that call — confirmed in `smc/fvg.py:13`, `smc/order_blocks.py:14` (`ob_idx` relative to `df`), `smc/structure.py:20` (loop variable over `range(len(df))`).
- HTF-derived indices (`htf_swings`/`htf_pools`, `strategy.py:112`, `:126`) live in a separate coordinate space from LTF indices — never comparable, even within the same `analyze()` call.
- Convention for new primitives (verified in `smc/fvg.py` and `smc/order_blocks.py`): `@dataclass` with `index`, `direction`, `top`, `bottom`, `time`, plus a domain-named invalidation flag (`filled`/`mitigated`); scanning function extracts only the OHLC columns it needs as numpy arrays (`order_blocks.py:27` uses all four `o,h,l,c`; `fvg.py:24` uses only `h,l,c`); ends with a test-and-invalidate + age-out loop, returning only non-invalidated items.
- Sibling skill `backtest-tuning` already exists at `.claude/skills/backtest-tuning/SKILL.md` and is the mandatory validation step this skill's Part A hands off to.

---

### Task 1: Skill scaffold + frontmatter + purpose section

**Files:**
- Create: `.claude/skills/smc-strategy-development/SKILL.md`

- [ ] **Step 1: Create the directory and file with frontmatter**

```bash
ls .claude/skills/
```

Write `.claude/skills/smc-strategy-development/SKILL.md` with:

```markdown
---
name: smc-strategy-development
description: Use when editing strategy.py or any file under smc/ (structure, liquidity, order_blocks, fvg, or a new module) in gold-smc-bot. Rigid two-part checklist — Part A protects the invariants that keep live/backtest signal-identical and mandates a backtest-tuning validation handoff; Part B is the template for adding an entirely new SMC primitive (e.g. breaker block, mitigation block). Does not trigger on config.py-only edits — that's the backtest-tuning skill.
---

# SMC Strategy Development

Protects the invariants that keep `strategy.py`'s signal logic identical
between live and backtest, and gives a verified template for adding a new
SMC primitive. Two parts:

- **Part A** always applies, even for small edits.
- **Part B** applies only when introducing an entirely new primitive (new
  dataclass, or an existing dataclass reused with materially different
  invalidation semantics) — not for small edits to existing logic.

Create one TodoWrite item per step and follow them in order.
```

- [ ] **Step 2: Verify the frontmatter format matches the ecosystem convention**

Compare against an existing skill's frontmatter, e.g.
`.claude/skills/backtest-tuning/SKILL.md` (already built in this repo) or any
`SKILL.md` under
`C:\Users\OS\.claude\plugins\cache\claude-plugins-official\superpowers\*\skills\`
(match any version directory). Confirm the `---`-delimited block with
`name`/`description` keys, immediately followed by an H1 matching the display
name.

No commit — not a git repo. Just confirm the file is saved correctly.

---

### Task 2: Part A — entry sequence map + three invariants

**Files:**
- Modify: `.claude/skills/smc-strategy-development/SKILL.md`

- [ ] **Step 1: Append the "Part A" intro and entry-sequence map**

```markdown
## Part A: Protecting invariants (always applies)

**Before editing:** read `strategy.py` (194 lines) to determine which step of
the *real* entry sequence the change touches — not the simplified 3-step
summary in its module docstring (lines 1-13), the actual sequence grounded in
the code's own numbered inline comments:

1. **(#8)** HTF trend (`current_trend_htf`) — neutral → no trade; also gated
   by a max-trend-age check (`cfg.htf_trend_max_age_bars`, documented as `#8`
   in `smc/structure.py`'s `current_trend_htf` docstring) that forces
   `neutral` when the last BOS/CHoCH is stale.
2. Liquidity sweep against trend (`detect_sweeps`), gated by `require_sweep`.
3. CHoCH/BOS confirmation after the sweep, matching trend direction.
4. **(#9)** Max setup-age cap — sweep→confirm chain must still be "fresh"
   (`cfg.max_setup_age_bars`, `strategy.py:58-66`).
5. Entry zone: OB or FVG born from the confirming event
   (`_pick_entry_zone`, `cfg.entry_mode`).
6. **(#4)** Limit placed at the zone edge (not market at close, `strategy.py:79-92`)
   — only valid if price hasn't already run through the zone.
7. **(#6)** Discount/premium filter via equilibrium — 50% of recent swing
   range, gated by `cfg.require_discount_premium` (`strategy.py:94-103`).
8. **(#5)** SL anchored to the real sweep wick extreme (`last_sweep.extreme`),
   not the touched pool level (`strategy.py:105-133`); TP from nearest
   liquidity pool or fixed R:R fallback, capped by `cfg.max_rr`.
9. Min-SL-distance filter, R:R ≥ `cfg.min_rr` check, position sizing
   (`strategy.py:136-151`).
```

- [ ] **Step 2: Append the three invariants**

```markdown
**Three invariants that must never break:**

1. **No-lookahead.** `analyze()` only ever receives closed bars. Live drops
   the forming bar via `.iloc[:-1]` (`main.py:266`); backtest slices
   `ltf.iloc[max(0, i + 1 - cfg.ltf_bars): i + 1]` and
   `htf[htf.index <= now].iloc[-cfg.htf_bars:]` (`backtest.py:183-184`). Any
   change to scanning windows inside `smc/*.py` must preserve this — no
   primitive may look at a bar beyond what its caller sliced.
2. **`analyze()` is the single source of truth for signals.** Live and
   backtest must both call the same `strategy.analyze()` — never fork signal
   logic between the two paths.
3. **`.index` on every SMC dataclass is relative to the slice passed in for
   that call, not absolute across full history.** Confirmed in `FVG.index`,
   `OrderBlock.index`, `StructureEvent.index` — backtest re-slices the
   DataFrame every bar, so these indices only have meaning compared against
   each other *within the same `analyze()` call*. Comparing an `index` from
   one call against a stored value from a previous call is a bug. **This also
   applies across timeframes within a single call**: `strategy.py` derives
   `htf_swings`/`htf_pools` from `htf_df` (`strategy.py:112`, `:126`) whose
   `.index` values live in the HTF slice's own coordinate space — never
   comparable to the LTF `swings`/`events`/`obs`/`fvgs` indices used
   elsewhere in the same `analyze()` call, even though both were computed in
   that one call.
```

- [ ] **Step 3: Save and re-read to confirm both sections were appended correctly** (no truncation, all 9 steps present, both invariant items readable).

---

### Task 3: Part A — after-editing handoff to backtest-tuning

**Files:**
- Modify: `.claude/skills/smc-strategy-development/SKILL.md`

- [ ] **Step 1: Append the handoff section**

```markdown
**After editing:** invoke the `backtest-tuning` skill (via the `Skill` tool,
not just a prose reminder) to validate the change via a real before/after
backtest. The task is not done until that skill's keep/revert decision has
been reached — do not report "done" on the code change alone.

Concrete terminal actions based on `backtest-tuning`'s outcome:
- **Keep**: report the change as done, including the keep/revert rationale
  from `backtest-tuning`'s output.
- **Revert-leaning or overfit warning**: do not auto-revert the edit. Report
  the concern to the user with the comparison numbers and let them decide
  whether to revert, adjust the approach and re-loop through Part A, or
  accept the tradeoff explicitly.
```

- [ ] **Step 2: Save and re-read to confirm the section reads correctly following the invariants list from Task 2** (no duplicated "After editing" heading, flows naturally as the close of Part A).

---

### Task 4: Part B — new-primitive convention + template

**Files:**
- Modify: `.claude/skills/smc-strategy-development/SKILL.md`

- [ ] **Step 1: Append the Part B intro and boundary rule**

```markdown
## Part B: Adding a new SMC primitive

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
```

- [ ] **Step 2: Append the verified convention template**

```markdown
**The existing convention**, verified directly against `smc/fvg.py` and
`smc/order_blocks.py`:

\`\`\`python
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
\`\`\`
```

- [ ] **Step 3: Append the integration steps**

```markdown
**Integration steps into `strategy.analyze()`:**

1. Import the new module at the top of `strategy.py`.
2. Add any related tuning parameters to `TradingConfig` (`config.py`) — per
   symbol if gold/EURUSD should behave differently.
3. Decide explicitly which of the 9 entry-sequence steps above this primitive
   replaces or augments (most often step 5, entry-zone selection) — don't
   silently add a new filter condition without saying so in `strategy.py`'s
   module docstring.
4. Update `strategy.py`'s module docstring (currently a 3-step summary) to
   reflect the new primitive's role.
```

- [ ] **Step 4: Save and re-read to confirm code fences are balanced and the section reads as a coherent whole** (intro → boundary rule → template → integration steps).

---

### Task 5: Edge cases + out-of-scope section

**Files:**
- Modify: `.claude/skills/smc-strategy-development/SKILL.md`

- [ ] **Step 1: Append edge cases and scope notes**

```markdown
## Edge cases / out of scope

- **Pure `config.py` edits** with no `strategy.py`/`smc/*` change: this skill
  doesn't trigger — use `backtest-tuning` directly.
- **Adding a field to an existing dataclass** (not a wholly new primitive):
  apply Part A only, skip Part B.
- **Ambiguous integration point** (unclear which entry-sequence step a new
  primitive replaces/augments): stop and ask the user — don't decide
  unilaterally where it slots in.
- Out of scope for this skill entirely: live/demo operation checklist, adding
  a new symbol/broker to `CONFIGS` — separate skills.
```

- [ ] **Step 2: Read the full assembled `SKILL.md` top to bottom once** to confirm section order: frontmatter → intro → Part A (entry sequence → invariants → handoff) → Part B (boundary rule → template → integration steps) → Edge cases, with no duplicated headers from earlier tasks.

---

### Task 6: Validate the skill's factual claims against the real codebase

This is a documentation artifact, not code — the "test" here is grepping the
actual source files for every quoted line/snippet in the assembled
`SKILL.md` and confirming each one is still accurate (files can drift after
the spec/plan were written).

**Files:**
- None created — read-only verification against `strategy.py`, `main.py`,
  `backtest.py`, `smc/fvg.py`, `smc/order_blocks.py`, `smc/structure.py`.

- [ ] **Step 1: Confirm the numbered-comment anchors**

```bash
grep -n "#8\|#9\|#4\|#5\|#6" strategy.py smc/structure.py
```

Confirm each comment number appears at (approximately) the line ranges
quoted in the skill: `#8` in `smc/structure.py` near `current_trend_htf`,
`#9` around `strategy.py:58-66`, `#4` around `strategy.py:79-92`, `#6` around
`strategy.py:94-103`, `#5` around `strategy.py:105-133`.

- [ ] **Step 2: Confirm the no-lookahead quotes verbatim**

```bash
grep -n "iloc\[:-1\]" main.py
grep -n "ltf_slice = ltf.iloc\|htf_slice = htf\[" backtest.py
```

Confirm the exact lines quoted in the skill (`main.py:266`,
`backtest.py:183-184`) still match what's in the skill file.

- [ ] **Step 3: Confirm the dataclass/convention claims**

```bash
grep -n "class FVG\|class OrderBlock\|class StructureEvent" smc/fvg.py smc/order_blocks.py smc/structure.py
grep -n "df\[k\].values for k in" smc/order_blocks.py
grep -n 'df\["high"\].values' smc/fvg.py
```

The second grep only matches `order_blocks.py`'s `(df[k].values for k in (...))`
comprehension form — `fvg.py` extracts columns with a different, simpler
syntax (`h, l, c = df["high"].values, df["low"].values, df["close"].values`),
so it needs its own grep (the third command above) rather than reusing the
same pattern.

Confirm field names (`index`, `direction`, `top`, `bottom`, `time`,
`filled`/`mitigated`, `origin_event`) and the numpy-extraction pattern still
match what Part B's template claims.

- [ ] **Step 4: If any grep contradicts the skill file's claims (line numbers shifted, comment removed, field renamed), fix the skill content directly** — the skill must reflect the codebase as it stands now, not as it stood when the spec was written. No commit needed (not a git repo).

---

## Done criteria

- `.claude/skills/smc-strategy-development/SKILL.md` exists with Part A
  (entry-sequence map, 3 invariants, backtest-tuning handoff), Part B
  (boundary rule, convention template, integration steps), and edge cases.
- Task 6's grep-based verification has been run at least once against the
  current state of `strategy.py`/`smc/*.py`/`main.py`/`backtest.py`, and any
  drift found was corrected in the skill file.
