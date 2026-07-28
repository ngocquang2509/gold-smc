# Add New Symbol Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author `.claude/skills/add-new-symbol/SKILL.md`, a rigid linear checklist for adding a new trading symbol/broker to gold-smc-bot: config + registry + optional CSV-offline support + real cost measurement + a first baseline via the sibling `backtest-tuning` skill.

**Architecture:** A single Markdown skill file with YAML frontmatter followed by 7 sequential workflow steps and an edge-cases section. No new Python modules — this only documents how to safely extend `config.py`/`backtest.py`'s existing per-symbol registry pattern.

**Tech Stack:** Markdown (skill file). No code is created; all guidance is grounded in and verified against the real `config.py`, `backtest.py`, and `mt5_client.py`.

**Reference:** Spec at `docs/superpowers/specs/2026-07-28-add-new-symbol-skill-design.md`. This repo is not a git repository — **do not run `git init`** or any git commands; skip all commit steps and just save files directly, consistent with the two sibling skills already built (`.claude/skills/backtest-tuning/SKILL.md`, `.claude/skills/smc-strategy-development/SKILL.md`).

---

## Ground truth (verified against the real codebase — do not re-derive differently)

- `config.py` (245 lines): `TradingConfig` schema dataclass; per-symbol instances `XAUUSD`/`EURUSD` registered in `CONFIGS` (`config.py:221-224`) and `_ALIASES` (`config.py:226-229`, existing keys: `gold`, `xau`, `xauusd`, `vang`, `eur`, `eurusd`). `get_config(name)` (`config.py:232-240`) resolves alias/symbol and returns `replace(CONFIGS[key])` — a fresh copy.
- `EURUSD`'s block (`config.py:171-217`) mixes **structural price-scale overrides** (`price_digits`, `eq_tolerance`, `fvg_min_size_points`, `sl_buffer_points`, `min_sl_distance_points`) with **EURUSD-specific tuned values** (`min_rr=2.5`, `tp_rr=3.0`, `entry_expiry_bars=48`, `htf_trend_max_age_bars=40`, `weekend_guard_hours=3.0`, `risk_per_trade_pct=2.0`) that must NOT be copied into a new symbol's config — those are tuning results, not structural requirements.
- `magic_number`: `XAUUSD`=20260723, `EURUSD`=20260724 — must be unique per symbol, scopes which live positions `main.py`/`mt5_client.py` manage.
- `sessions` are in MT5 **server time**, not local time (per `CLAUDE.md`: this Exness account's server is GMT+0 — a different broker may differ, don't assume).
- `backtest.py:31-34` defines `SYMBOL_INFO_DEFAULT`; `backtest.py:39-43` defines the `SYMBOL_INFO` dict (`contract_size`, `volume_min/step/max`, `point`, `digits`), used only on the **CSV-offline path** (`backtest.py:296`, `SYMBOL_INFO.get(cfg.symbol)`). The `--from-mt5` path gets this automatically from the broker via `mt5_client.get_symbol_info()` (`mt5_client.py:73-76`) — no manual entry needed there. `backtest.py:36-37` warns a wrong `contract_size` corrupts CAGR/max-DD.
- Swap values in `config.py` are USD/lot, but **MT5's Symbol Properties dialog reports swap in points**, not currency — `XAUUSD`'s own comment documents the conversion: `swap_long_per_lot=-49.04, # swap_long thật (POINTS -490.4 × $0.10/point)`. A new symbol's swap must go through the same points→currency conversion via tick value before being written.
- `main.py` has zero symbol-specific branching (verified: only references `cfg.symbol`, `cfg.magic_number`, etc.) — adding a symbol needs no changes there or in `mt5_client.py`.
- Sibling skill `.claude/skills/backtest-tuning/SKILL.md` has independently-invokable `## Step 1: Baseline, full period`, `## Step 2: Baseline, half-period split`, and `## Step 7: Persist results (opt-in)` sections. Its Step 1 currently hardcodes: `Confirm the symbol with the user if not already stated: XAUUSDm/gold or EURUSDm/eurusd` — this line needs updating (or generalizing to reference `CONFIGS`) whenever a symbol is actually added, so it doesn't go stale.

---

### Task 1: Skill scaffold + frontmatter + purpose section

**Files:**
- Create: `.claude/skills/add-new-symbol/SKILL.md`

- [ ] **Step 1: Create the directory and file with frontmatter**

```bash
ls .claude/skills/
```

Write `.claude/skills/add-new-symbol/SKILL.md` with:

```markdown
---
name: add-new-symbol
description: Use when adding a new trading symbol or broker to gold-smc-bot. Rigid linear checklist — gather broker facts, create a TradingConfig instance (structural pattern only, not a copy of another symbol's tuned values), register it, optionally wire up CSV-offline backtest support, measure real trading costs, and run a first baseline via the backtest-tuning skill. Does not cover deep parameter tuning beyond that first baseline (repeated backtest-tuning invocations) or any strategy/SMC logic change.
---

# Add New Symbol

A linear, sequential checklist for adding a new symbol/broker to the bot.
Unlike `smc-strategy-development`, this has no branching parts — the
process is inherently one thing after another.

Create one TodoWrite item per step and follow them in order.
```

- [ ] **Step 2: Verify the frontmatter format matches the sibling skills**

Compare against `.claude/skills/backtest-tuning/SKILL.md` and
`.claude/skills/smc-strategy-development/SKILL.md` — confirm the
`---`-delimited block with `name`/`description` keys, immediately followed
by an H1 matching the display name.

No commit — not a git repo. Just confirm the file is saved correctly.

---

### Task 2: Steps 1-2 (gather broker facts, create the config instance)

**Files:**
- Modify: `.claude/skills/add-new-symbol/SKILL.md`

- [ ] **Step 1: Append Step 1 — gather broker/symbol facts**

```markdown
## Step 1: Gather broker/symbol facts

Exact symbol name as it appears in MT5 Market Watch — may carry a
broker-specific suffix like `m` or `.m` (right-click → "Show All" if it's
not visible). Collect: price digits, contract size, volume min/step/max,
point size — via MT5 Symbol Properties, or `mt5_client.get_symbol_info()` if
a live connection is available.
```

- [ ] **Step 2: Append Step 2 — create the TradingConfig instance**

```markdown
## Step 2: Create a new `TradingConfig` instance

Add a new instance to `config.py`, following only the **structural pattern**
of the `EURUSD` block (`config.py:171-217`) — NOT a copy-paste of its
contents. That block mixes two different kinds of overrides:

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
```

- [ ] **Step 3: Save and re-read to confirm both sections were appended correctly.**

---

### Task 3: Steps 3-4 (registry + optional CSV-offline support)

**Files:**
- Modify: `.claude/skills/add-new-symbol/SKILL.md`

- [ ] **Step 1: Append Step 3 — register in CONFIGS/_ALIASES**

```markdown
## Step 3: Register in `CONFIGS` and `_ALIASES`

Add the new symbol key to `CONFIGS` (`config.py:221-224`) and one or more
friendly CLI aliases to `_ALIASES` (`config.py:226-229`). Check both the
**magic_number** (against `CONFIGS`'s existing values) and the chosen
**alias string(s)** (against `_ALIASES`'s existing keys: `gold`, `xau`,
`xauusd`, `vang`, `eur`, `eurusd`) for collisions before writing — a
duplicate alias silently shadows an existing symbol's shortcut, and a
duplicate magic_number would cross-contaminate live position management
between symbols.
```

- [ ] **Step 2: Append Step 4 — SYMBOL_INFO for CSV-offline mode**

```markdown
## Step 4: Add a `SYMBOL_INFO` entry in `backtest.py` (only if needed)

Only required if CSV-offline backtesting (`--csv-ltf`/`--csv-htf`) will be
used for this symbol — live/MT5-sourced backtests (`--from-mt5`) and live
trading get this data automatically from the broker via
`mt5_client.get_symbol_info()`. Add an entry to the `SYMBOL_INFO` dict
(`backtest.py:39-43`, sibling to `SYMBOL_INFO_DEFAULT` at `backtest.py:31-34`)
with `contract_size`, `volume_min`, `volume_step`, `volume_max`, `point`,
`digits` from Step 1's gathered facts. A wrong `contract_size` corrupts
CAGR/max-DD (absolute PnL scale, per `backtest.py:36-37`'s own warning) — it
must be looked up, never guessed or copied from another symbol.
```

- [ ] **Step 3: Save and re-read to confirm both sections were appended correctly.**

---

### Task 4: Steps 5-7 (real costs, first baseline, sibling skill update)

**Files:**
- Modify: `.claude/skills/add-new-symbol/SKILL.md`

- [ ] **Step 1: Append Step 5 — measure real trading costs**

```markdown
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
```

- [ ] **Step 2: Append Step 6 — first baseline via backtest-tuning**

```markdown
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
```

- [ ] **Step 3: Append Step 7 — update the sibling skill's stale wording**

```markdown
## Step 7: Update `backtest-tuning/SKILL.md`'s Step 1 wording

That skill's Step 1 currently says "Confirm the symbol with the user if not
already stated: `XAUUSDm`/`gold` or `EURUSDm`/`eurusd`" — a hardcoded
two-symbol list. Now that a new symbol is registered, add it to that list
(or generalize the wording to reference `config.py`'s `CONFIGS` instead of
naming symbols directly) so `backtest-tuning`'s own checklist doesn't
mislead future invocations into thinking only two symbols exist.
```

- [ ] **Step 4: Save and re-read to confirm all three sections flow correctly after Task 3's Step 3 (the SYMBOL_INFO section).**

---

### Task 5: Edge cases + out-of-scope section

**Files:**
- Modify: `.claude/skills/add-new-symbol/SKILL.md`

- [ ] **Step 1: Append edge cases and scope notes**

```markdown
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
```

- [ ] **Step 2: Read the full assembled `SKILL.md` top to bottom once** to confirm section order: frontmatter → intro → Step 1 → 2 → 3 → 4 → 5 → 6 → 7 → Edge cases, with no duplicated headers from earlier tasks.

---

### Task 6: Validate the skill's factual claims against the real codebase

This is a documentation artifact, not code — the "test" here is grepping the
actual source files for every quoted line/snippet in the assembled
`SKILL.md` and confirming each one is still accurate (files can drift after
the spec/plan were written). No new symbol is actually being added right
now, so there's nothing to run through the skill end-to-end — this task is
read-only verification only.

**Files:**
- None created — read-only verification against `config.py`, `backtest.py`,
  `mt5_client.py`, `main.py`, and the sibling `.claude/skills/backtest-tuning/SKILL.md`.

- [ ] **Step 1: Confirm the config.py structure claims**

```bash
grep -n "^CONFIGS = \|^_ALIASES = \|^XAUUSD = \|^EURUSD = \|def get_config" config.py
```

Confirm `CONFIGS`/`_ALIASES` line numbers and the `EURUSD` block's start
line still roughly match what the skill quotes (`config.py:171`, `:221`,
`:226`, `:232`).

- [ ] **Step 2: Confirm the magic_number and price-scale field values**

```bash
grep -n "magic_number=20260723\|magic_number=20260724" config.py
grep -n "price_digits=\|eq_tolerance=\|fvg_min_size_points=\|sl_buffer_points=\|min_sl_distance_points=" config.py
```

The first grep asserts the actual magic-number *values*, not just that the
field name is present — if either had been silently renumbered, this would
catch it. Confirm both values still hold, and that the five price-scale
field names quoted in Step 2 of the skill still exist in both blocks.

- [ ] **Step 3: Confirm the swap-conversion example verbatim**

```bash
grep -n "swap_long_per_lot=-49.04\|POINTS" config.py
```

Confirm the exact `XAUUSD` comment the skill quotes ("swap_long thật (POINTS
-490.4 × $0.10/point)") is still present and unchanged.

- [ ] **Step 4: Confirm the SYMBOL_INFO / CSV-offline-only claim**

```bash
grep -n "SYMBOL_INFO_DEFAULT\|^SYMBOL_INFO = \|SYMBOL_INFO.get" backtest.py
grep -n "def get_symbol_info" mt5_client.py
```

Confirm `SYMBOL_INFO_DEFAULT` and `SYMBOL_INFO` are still at their claimed
line ranges, `SYMBOL_INFO.get(cfg.symbol)` is still only reached on the CSV
path, and `mt5_client.py` still auto-populates this data on the MT5 path.

- [ ] **Step 5: Confirm main.py is still symbol-agnostic**

```bash
grep -n "XAUUSD\|EURUSD" main.py
```

Confirm any hits are only doc-comment/CLI-default string references, not
conditional branches on symbol identity.

- [ ] **Step 6: Confirm the sibling skill's Step 1 wording is still as quoted**

```bash
grep -n "Confirm the symbol with the user" .claude/skills/backtest-tuning/SKILL.md
```

Confirm the exact sentence quoted in this skill's Step 7 is still present in
`backtest-tuning/SKILL.md` (so the instruction to update it remains
accurate).

- [ ] **Step 7: If any grep contradicts the skill file's claims (line numbers
  shifted, values changed, wording changed), fix the skill content directly
  at `d:/gold-smc-bot/.claude/skills/add-new-symbol/SKILL.md`** — the skill
  must reflect the codebase as it stands now. No commit needed (not a git
  repo).

---

## Done criteria

- `.claude/skills/add-new-symbol/SKILL.md` exists with all 7 workflow steps
  and the edge-cases section.
- Task 6's grep-based verification has been run at least once against the
  current state of `config.py`/`backtest.py`/`mt5_client.py`/`main.py`/
  `backtest-tuning/SKILL.md`, and any drift found was corrected in the
  skill file.
