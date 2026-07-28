# Backtest-Tuning Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author `.claude/skills/backtest-tuning/SKILL.md`, a rigid checklist skill that validates any `config.py`/`strategy.py`/`smc/*` change in gold-smc-bot via a before/after backtest comparison, including a post-hoc H1/H2 overfitting check.

**Architecture:** A single Markdown skill file with YAML frontmatter (name + trigger description) followed by a numbered checklist matching the 7 steps in the spec. No new Python modules are created — the skill only documents how to drive the existing `backtest.py` CLI and how to post-process its `backtest_trades.csv`/`backtest_equity.csv` outputs with an inline pandas snippet (reproducing `_report()`'s groupby exactly, since that's the part a future reader is most likely to get wrong).

**Tech Stack:** Markdown (skill file), Python/pandas (inline snippet documented in the skill, executed ad hoc — not a new source file), existing `backtest.py`.

**Reference:** Spec at `docs/superpowers/specs/2026-07-28-backtest-tuning-skill-design.md`. This repo is not a git repository (confirmed via `git status` → "not a git repository") — **do not run `git init` or any git commands**; skip all commit steps and just save files directly.

---

## Ground truth from backtest.py (verified, do not re-derive differently)

`backtest.py:210-267` (`_report()`) is the exact logic the skill's pandas snippet must mirror:

```python
tdf = pd.DataFrame(trades)
g = tdf.groupby(["entry_time", "entry", "direction"]).agg(net=("pnl", "sum")).reset_index()
n = len(g)
wins = g[g.net > 0.01]
losses = g[g.net < -0.01]
net_wr = len(wins) / n * 100 if n else 0
gross_profit = wins.net.sum()
gross_loss = -losses.net.sum()
pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
```

- `backtest_trades.csv` columns include `entry_time`, `entry`, `direction`, `pnl` (one row per fill; partial closes create multiple rows sharing the same `entry_time`/`entry`/`direction`).
- `backtest_equity.csv` has a `time` column, one row per LTF bar, spanning the full tested window — use `pd.to_datetime(equity_df.time).min()`/`.max()` to get the midpoint for the H1/H2 split.
- CAGR uses `equity[0]["time"]`/`equity[-1]["time"]` and `((end_bal/start_bal)**(1/yrs) - 1)*100`; max-DD uses a running peak vs. `balance` (`backtest.py:230-233`). **Both are computable per half** by windowing the `equity` rows to each half — same loop/formula as `_report()`, just re-initialized at the half's first row (`start_bal`/`end_bal` = balance at the half's first/last equity row, peak re-initialized there too). No re-simulation needed. Spec Step 5 lists CAGR/max-DD as H1/H2 columns, so compute them — don't leave them N/A.

---

### Task 1: Skill scaffold + frontmatter + purpose section

**Files:**
- Create: `.claude/skills/backtest-tuning/SKILL.md`

- [ ] **Step 1: Create the directory and file with frontmatter**

Check the directory doesn't already exist, then create it:

```bash
ls .claude/skills/ 2>/dev/null
```

Write `.claude/skills/backtest-tuning/SKILL.md` with:

```markdown
---
name: backtest-tuning
description: Use when tuning config.py parameters or changing strategy.py/smc/* logic in gold-smc-bot, or when asked to backtest, compare PF/winrate/CAGR, or validate a strategy change. Rigid checklist — run baseline backtest, apply change, re-run, compare full-period and half-period (H1/H2) metrics before deciding to keep or revert.
---

# Backtest Tuning

Validates any change to `config.py`, `strategy.py`, or `smc/*` against a real
before/after backtest, including a half-period (H1/H2) split to catch
overfitting that a full-period-only comparison would miss.

Create one TodoWrite item per step below and follow them in order — do not
skip the H1/H2 split even if the full-period result looks good.
```

- [ ] **Step 2: Verify the file is valid Markdown with parseable YAML frontmatter**

Read the file back and confirm the frontmatter block is delimited by `---`
lines and `name`/`description` keys are present (matches the format used by
every other skill under `.claude/skills/` or the plugin skills dir — check
one existing skill's frontmatter for comparison, e.g. any `SKILL.md` under
`C:\Users\OS\.claude\plugins\cache\claude-plugins-official\superpowers\*\skills\` —
match any version directory, don't hardcode a specific version number since
the plugin cache updates over time).

No commit — not a git repo. Just confirm the file is saved correctly.

---

### Task 2: Steps 1–2 of the checklist (baseline full-period + H1/H2 split)

**Files:**
- Modify: `.claude/skills/backtest-tuning/SKILL.md`

- [ ] **Step 1: Append the "Baseline, full period" section**

```markdown
## Step 1: Baseline, full period

Confirm the symbol with the user if not already stated: `XAUUSDm`/`gold` or
`EURUSDm`/`eurusd`. Run:

\`\`\`bash
python backtest.py --from-mt5 --symbol <sym> --years 2
\`\`\`

This works identically if data comes from `--csv-ltf`/`--csv-htf` instead —
same report code path either way. Costs are applied by default (net-of-cost);
pass `--no-costs` only if the user explicitly wants gross numbers, and say so
in the comparison table if you do.

Record from stdout: **PF, winrate ("Winrate ròng"), CAGR, max drawdown, trade
count ("Số lệnh (logic)")**. Total PnL and avg win/loss are also printed but
are out of scope for the comparison table in Step 5 — don't track them.
```

- [ ] **Step 2: Append the "Baseline, half-period split" section**

```markdown
## Step 2: Baseline, half-period split

`backtest.py` has no date-range flag — only `--years N` ("most recent N
years"), which can't isolate an arbitrary historical window on its own. Don't
re-run against MT5 for this. Instead, split post-hoc from the two files
`backtest.py` just wrote: `backtest_trades.csv` and `backtest_equity.csv`.

**The trades CSV has one row per fill, not per logical trade** — a trade with
partial closes produces multiple rows sharing the same `entry_time`/`entry`/
`direction`. You must reproduce `backtest.py`'s own grouping
(`_report()`, `backtest.py:215`) before computing anything, or partial-close
legs get miscounted as separate wins/losses and PF/winrate come out wrong:

\`\`\`python
import pandas as pd

trades = pd.read_csv("backtest_trades.csv", parse_dates=["entry_time"])
equity = pd.read_csv("backtest_equity.csv", parse_dates=["time"])

# 1. Group fills into logical trades — mirrors backtest.py:215 exactly.
g = trades.groupby(["entry_time", "entry", "direction"]).agg(net=("pnl", "sum")).reset_index()

# 2. Midpoint of the tested window, from the equity curve's time span.
midpoint = equity["time"].min() + (equity["time"].max() - equity["time"].min()) / 2

# 3. Assign each grouped logical trade to a half by its entry_time.
h1 = g[g.entry_time < midpoint]
h2 = g[g.entry_time >= midpoint]

def pf_wr(half):
    n = len(half)
    wins = half[half.net > 0.01]
    losses = half[half.net < -0.01]
    wr = len(wins) / n * 100 if n else 0
    gp, gl = wins.net.sum(), -losses.net.sum()
    pf = gp / gl if gl > 0 else float("inf")
    return n, wr, pf

n1, wr1, pf1 = pf_wr(h1)
n2, wr2, pf2 = pf_wr(h2)

# 4. CAGR + max-DD per half — mirrors backtest.py:230-242, windowed to the
#    half's equity rows instead of the full curve. No re-simulation needed.
def cagr_dd(eq_half, start_bal, end_bal):
    if eq_half.empty:
        return 0.0, 0.0
    peak, max_dd = start_bal, 0.0
    for bal in eq_half["balance"]:
        peak = max(peak, bal)
        max_dd = max(max_dd, (peak - bal) / peak * 100)
    days = (eq_half["time"].iloc[-1] - eq_half["time"].iloc[0]).days
    yrs = days / 365.25
    cagr = ((end_bal / start_bal) ** (1 / yrs) - 1) * 100 if yrs > 0 else 0.0
    return cagr, max_dd

eq1 = equity[equity.time < midpoint]
eq2 = equity[equity.time >= midpoint]
cagr1, dd1 = cagr_dd(eq1, eq1["balance"].iloc[0], eq1["balance"].iloc[-1])
cagr2, dd2 = cagr_dd(eq2, eq2["balance"].iloc[0], eq2["balance"].iloc[-1])
```

Run this inline (e.g. via a short Python one-off, not a new checked-in
script) after Step 1's backtest run. **Sanity check before trusting the
split**: `len(g) == n1 + n2` and the PF computed from `g` in full (no split)
should match the "Profit factor" printed by `backtest.py` in Step 1 — if it
doesn't, the CSVs are stale from a previous run; re-run Step 1.

PF/winrate/trade-count, and now CAGR/max-DD, are all tracked per half — fill
every cell in Step 5's table, none are N/A.
```

- [ ] **Step 3: Save and re-read the file to confirm both sections were appended correctly** (no truncation, code fences balanced).

---

### Task 3: Steps 3–5 of the checklist (apply change, re-run, comparison table)

**Files:**
- Modify: `.claude/skills/backtest-tuning/SKILL.md`

- [ ] **Step 1: Append "Apply the change", "Re-run", and "Comparison table" sections**

```markdown
## Step 3: Apply the change

Edit `config.py` (the relevant per-symbol instance) or `strategy.py`/`smc/*`
as intended for this tuning session.

## Step 4: Re-run, full period + split

Repeat Step 1 and Step 2 against the changed code. Do this in the same
session, close in wall-clock time to Step 1 — `--years` resolves its window
from `datetime.now()` at run time, so a large gap between the baseline and
after-change runs shifts both 2-year windows (and their H1/H2 midpoints)
slightly, making the before/after comparison marginally inexact at the edges.
Minutes to low hours apart is fine over a 2-year window; don't let a tuning
session span days between the two runs without noting the drift.

## Step 5: Comparison table

Present a table, before vs. after, for:

| Metric | Full (before) | Full (after) | H1 (before) | H1 (after) | H2 (before) | H2 (after) |
|---|---|---|---|---|---|---|
| PF | | | | | | |
| Winrate | | | | | | |
| Trade count | | | | | | |
| CAGR | | | | | | |
| Max DD | | | | | | |
```

- [ ] **Step 2: Save and re-read to confirm the table renders as valid Markdown** (pipe alignment doesn't need to be perfect, but column count per row must match the header).

---

### Task 4: Step 6–7 of the checklist (keep/revert decision, persist to memory)

**Files:**
- Modify: `.claude/skills/backtest-tuning/SKILL.md`

- [ ] **Step 1: Append the "Keep/revert decision" section**

```markdown
## Step 6: Keep/revert decision

PF is the primary metric. Default heuristic (a starting point — say so
explicitly if you deviate in a given session):

- **Revert-leaning** if full-period PF drops by more than **0.10 absolute**
  or more than **10% relative** vs. before.
- **Overfit warning** if H2 PF falls below **70% of H1 PF** (or H2 flips
  unprofitable while H1 stays profitable) — flag this explicitly even when
  full-period PF improved, since that's exactly the pattern that means the
  full-period gain is likely concentrated in older data and won't hold going
  forward. Don't silently recommend "keep" when this fires.
- Otherwise, keep.

**Low-confidence override:** if either half has **fewer than 20 grouped
trades** (post-groupby count from Step 2, not raw CSV rows), say so and do
not issue a firm keep/revert claim based on that half alone — surface the
ambiguity to the user rather than deciding for them.

## Step 7: Persist results (opt-in)

Ask the user whether to record the outcome in the memory system (e.g. update
`backtest-tuning-2y.md` or the relevant per-symbol memory file). Never
overwrite an existing memory file without asking first.
```

- [ ] **Step 2: Save and re-read to confirm the section is complete.**

---

### Task 5: Edge cases + out-of-scope section

**Files:**
- Modify: `.claude/skills/backtest-tuning/SKILL.md`

- [ ] **Step 1: Append edge cases and scope notes**

```markdown
## Edge cases

- **MT5 unreachable** (non-Windows, or terminal not logged in): stop and
  suggest `--csv-ltf`/`--csv-htf` with pre-exported data instead of
  fabricating results. The Step 2 split logic is identical either way.
- **Stale CSVs**: if the sanity check in Step 2 fails (`len(g) != n1 + n2`,
  or full PF from `g` doesn't match Step 1's printed PF), the CSVs are from
  an earlier run — re-run Step 1 before trusting the split.
- **Low trade count in a half-period**: covered in Step 6 — warn, don't
  conclude.

## Out of scope

This skill only covers config/strategy tuning validation. It does not cover:
SMC strategy development guidance (entry-sequence correctness, no-lookahead
invariants), the live/demo operation checklist (dry_run, session gating, risk
guard), or adding a new symbol/broker to `CONFIGS` — those are separate
skills.
```

- [ ] **Step 2: Read the full assembled `SKILL.md` top to bottom once** to confirm section order matches: frontmatter → intro → Step 1 → Step 2 → Step 3 → Step 4 → Step 5 → Step 6 → Step 7 → Edge cases → Out of scope, with no duplicated headers from earlier tasks.

---

### Task 6: Validate the skill against a real run (dry run, not a unit test)

This is a documentation artifact, not code — the equivalent of "tests" here
is actually executing the workflow once end-to-end against this repo's real
data and confirming the instructions are accurate and produce sane numbers.
This also validates the "Ground truth from backtest.py" section above wasn't
misremembered.

**Files:**
- None created — this task only runs commands and inline Python to verify
  Task 1–5's content is executable as written. If MT5 isn't reachable in
  this environment, use existing sample data instead (check `data/` for any
  `--csv-ltf`/`--csv-htf` inputs mentioned in `CLAUDE.md`); if neither is
  available, skip this task and note it explicitly rather than faking output.

- [ ] **Step 1: Run Step 1 of the skill for one symbol**

```bash
python backtest.py --from-mt5 --symbol XAUUSDm --years 2
```

Confirm it prints PF/winrate/CAGR/max-DD/trade-count and writes
`backtest_trades.csv`/`backtest_equity.csv` in the working directory.

- [ ] **Step 2: Run the Step 2 pandas snippet from the skill verbatim**

Execute the exact code block written into `.claude/skills/backtest-tuning/SKILL.md`
Step 2 (copy it out, don't retype). Confirm:
- `len(g) == n1 + n2`
- PF computed from `g` (ungrouped by half) equals the "Profit factor" printed
  in Step 1's stdout (within float rounding).

If either check fails, the snippet in the skill has a bug — fix the skill
content (not just this one-off run) and re-verify.

- [ ] **Step 3: Confirm the H1/H2 numbers are plausible**

Print `n1, wr1, pf1, n2, wr2, pf2, cagr1, dd1, cagr2, dd2` and eyeball that
neither half has zero trades (if it does, the 2-year window may be too short
for this symbol's current config — note this as a real edge case candidate,
but it doesn't block the skill itself). Also confirm `cagr1`/`cagr2` and
`dd1`/`dd2` are finite, sane numbers (not NaN/inf) — this validates the
windowed CAGR/max-DD addition to the Step 2 snippet actually works on real
data, not just in theory.

- [ ] **Step 4: Note any discrepancy found between the skill's documented
  behavior and this real run, and fix the skill file if anything was wrong.**

No commit — save the corrected `SKILL.md` directly (not a git repo).

---

## Done criteria

- `.claude/skills/backtest-tuning/SKILL.md` exists with all 7 workflow steps,
  edge cases, and out-of-scope section.
- The Step 2 pandas snippet has been executed at least once against real
  `backtest.py` output for one symbol and passed its own sanity checks
  (Task 6), or Task 6 was explicitly skipped with a stated reason (e.g. MT5
  unreachable and no CSV data available).
