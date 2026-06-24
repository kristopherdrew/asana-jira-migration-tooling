---
marp: true
theme: default
paginate: true
title: "Vibe Coding, Safely"
---

<!--
Speaker notes appear in these HTML comment blocks. They render in Marp's
presenter view (and never on the slide). One block per slide.
-->

# Vibe Coding, Safely

### Building an Asana → Jira migration toolkit with Claude Code

A repeatable methodology for AI-assisted development that doesn't sacrifice rigor.

<!--
Opening line: "I built a real, high-stakes tool mostly by 'vibe coding' with
Claude — but it's production-grade because the AI worked inside guardrails I
set up front. This talk is the system, not just the story."
Audience is mixed, so I'll keep code light and lean on the methodology.
-->

---

## The problem

Migrate an organization from **Asana → Jira**:

- Users & workspace memberships, teams, projects
- Tasks (with hierarchy/subtasks), comments, attachments
- Custom fields, owners, project status

**High blast radius:** the tool *writes to live Jira*. A bad run creates or
corrupts real issues. "Move fast and break things" is not an option.

<!--
Emphasize the stakes for the non-technical half: this isn't a toy script. It
creates real work items in a live system. That stakes framing is *why* the
guardrails matter — speed alone would be dangerous here.
-->

---

## The thesis

> AI speed **with** engineering discipline.

Five pillars that made fast AI-assisted coding safe:

1. **CLAUDE.md** — the standards contract
2. **Reusable helpers** — decisions made once
3. **Tests** — guardrails that catch regressions
4. **Runtime guardrails** — safe against live systems
5. **Interactive CLI** — accessible to non-engineers

<!--
This is the spine of the talk. Each pillar gets a slide. The meta-point: none
of these are exotic — they're ordinary engineering discipline, but applied
*deliberately* so the AI inherits them on every turn instead of guessing.
-->

---

## Pillar 1 — CLAUDE.md as the contract

The rules Claude inherited on **every** turn (`CLAUDE.md` → `AGENTS.md`):

- `python3`, never bare `python`; CPython 3.11+
- **Standard library only** — no `requests`, no `pytest`
- Tests mirror `src/` two levels deep; **≥90% coverage**
- Conventional Commits; scope = the `src/` folder touched
- **Never commit without explicit approval**

```python
# src/lib/http_client.py — stdlib only, by contract
import json, ssl, time
from urllib import error, parse, request
```

**Methodology:** write the rules down so the AI inherits your standards
automatically — you stop re-explaining them.

<!--
Key insight for the audience: the CLAUDE.md is a contract the model reads every
single turn. Saying "stdlib only" once in chat is forgotten; putting it in
CLAUDE.md makes it binding. The http_client.py imports prove it held — no
third-party HTTP library anywhere, just urllib.
-->

---

## Pillar 1, cont. — Claude writes & maintains CLAUDE.md

You don't hand-author it — `CLAUDE.md` is just a text file Claude manages:

- **Create:** run **`/init`** → Claude scans the codebase and drafts it for you
- **Or ask:** *"Create a CLAUDE.md with our standards: python3, stdlib only…"*
- **Update:** *"Add a rule that every command needs a `--dry-run` flag"*
- **Capture corrections:** after fixing Claude, say *"add that to CLAUDE.md so
  it sticks"* — next session it already knows
- **`/memory`** views/edits loaded rules; **`~/.claude/CLAUDE.md`** holds your
  personal, machine-wide rules

**Methodology:** the rules file is *living* — let Claude grow it as the project
teaches you what you care about.

<!--
This is the slide that answers "but who writes the rules file?" Answer: mostly
Claude. /init bootstraps it from the existing code. Then it grows by asking in
plain English — it's just a markdown file Claude edits like any other. The
killer habit: when you correct Claude's behavior, immediately ask it to record
the correction in CLAUDE.md, so the lesson persists across sessions. Mention
this project keeps its whole CLI command catalog in CLAUDE.md so docs and tools
never drift. Note: ~/.claude/CLAUDE.md is personal and never committed.
-->

---

## Pillar 2 — Reusable helpers streamlined decisions

`src/lib/` = a force multiplier. **13 shared modules**, **181 `src.lib.*`
imports** across **46 scripts**.

| Module | Job |
|---|---|
| `config.py` | Load credentials w/ fallback chain |
| `http_client.py` | Asana client + 429 retry |
| `jira_client.py` | Jira REST client + retries |
| `database.py` | SQLite upsert/load layer |
| `team_picker.py` | Interactive team selection |
| `adf.py` | Text → Atlassian rich-text |
| `logging_utils.py` | Standard logging setup |

`get_tasks.py` and `create_team_project_jira_issues.py` are *compositions* of
these primitives — not net-new surface area.

<!--
The decision-streamlining angle: once "how do we talk to Asana / handle rate
limits / write to the DB" is solved in one module, every new script reuses that
answer. New scripts become small, low-risk wiring jobs. Fewer decisions, fewer
bugs, consistent behavior. This is the antidote to "AI generates a slightly
different version of the same thing in every file."
-->

---

## Pillar 3 — Tests as guardrails

**48 unit + 2 integration tests**, mirroring the `src/` layout.

Edge cases that protect AI-written code:

- **Rate-limit metadata** parsing — `http_client.test.py`
- **Pagination** of Jira field metadata — `jira_client.test.py`
- **Replace/delete semantics** for comments — `database_task_extras.test.py`
- **Custom-field format coercion** (option → `{value}`, array, number, ADF) —
  `create_team_project_jira_issues.test.py`

**Methodology:** a regression test per bug fix turns *"looks right"* into
*"proven right."* Run the suite after every change.

<!--
For the technical crowd: the tests use recording-stub clients to assert both
what was *sent* and what was *parsed* — so a refactor that silently changes a
request payload fails the test. For everyone: this is how you trust code you
didn't hand-write line by line. The AI can move fast precisely because the tests
catch it when it's wrong.
-->

---

## Pillar 4 — Runtime guardrails for a live system

Safety features that make it safe to point at **live Jira**:

- `--dry-run` — print the exact payloads, change nothing
- `--yes` confirmation gate — "About to create N issues. Continue?"
- **429 requeue** — rate-limited creates retry up to `MAX_RATE_LIMIT_ROUNDS`
- **Assignee fallback** — unassignable user? drop assignee, keep going
- `--clear-first` **two-pass** — clear all, then set, so no stale value survives
- `--reset` + persisted mappings — **safe to run twice** (idempotent)
- `validate_scope_args()` — can't accidentally target the whole workspace

**Methodology:** design for failure and reversibility *before* scale.

<!--
This is the pillar that resonates most with leadership: it's operational safety,
not just code quality. Dry-run + confirm = look before you leap. Requeue +
fallback = a hiccup doesn't tank the whole migration. Idempotency = a re-run
heals instead of duplicating. These are the things that let you actually press
"go" on a live migration with confidence.
-->

---

## Pillar 5 — An interactive CLI made it accessible

Not just flags — guided, forgiving interaction:

- `team_picker.py` — paginated + name-search team selection
- `--map-fields` — interactive Jira field mapping, **saveable** for next time
- `interactive_cli.py` — required / default / yes-no prompts
- Confirmation gates everywhere a write happens

**Methodology:** lower the barrier so a non-engineer can run the tool safely —
the CLI explains itself and refuses to do the wrong thing silently.

<!--
Accessibility is a safety feature too. If the only way to run the tool is to
hand-craft 12 correct flags, people will get it wrong. Interactive pickers and
prompts mean the tool guides the operator and confirms before damage. The
saveable field map is a nice touch — interactive the first time, scriptable
after.
-->

---

## Decisions to NOT build (restraint as a feature)

- **Removed the "note shuttle"** when it was no longer needed — `3322a93`
- **Stdlib-only** meant *declining* convenient third-party libraries
- **CSV-first**, direct REST API only when the workflow justified it

The feature arc — earned, not front-loaded:

```
CSV export → REST creation (62bc708) → field mapping (5599c9a)
           → resilience: requeue + assignee fallback (b965761)
           → two-pass atomicity (2616b09)
```

<!--
The "what we chose NOT to do" story is underrated. Every dependency you don't
add and every prototype you delete is less to maintain and less to break. The
git arc shows we added complexity only when reality demanded it — we didn't
build the resilient REST creator on day one; we built CSV, then grew into it.
That restraint is a deliberate methodology choice, not laziness.
-->

---

## Knowledge organization = a feedback loop

- Split a monolithic README into **`docs/` topic files** — `0f397ca`
  (`sync-commands.md`, `reports.md`, `jira-integration.md`)
- A detailed **CLI Script Catalog** lives in `CLAUDE.md`

These docs aren't decoration — **Claude reads them back** every session.

> Good docs literally make the *next* AI generation better.

<!--
Reframe documentation for the audience: in AI-assisted development, docs are
context the model re-consumes. A well-maintained CLAUDE.md / docs tree means
each new request starts from an accurate map instead of guesses. Docs become a
compounding asset, not a chore.
-->

---

## The repeatable playbook

A checklist anyone can copy:

1. **Write `CLAUDE.md` first** — encode your standards as a contract
2. **Build helpers before scripts** — solve each decision once
3. **Test every fix** — regression test per bug, run the suite always
4. **Dry-run + confirm before writes** — look before you leap
5. **Make it interactive** — guide the operator, refuse silent mistakes
6. **Prune what you don't need** — restraint over accumulation

<!--
This is the payoff slide — the "how-to" the whole talk builds toward. If people
remember one slide, make it this one. Each item maps back to a pillar. Frame it
as: "this is portable to any AI-assisted project, not just this one."
-->

---

## Results & takeaways

- A working Asana → Jira toolkit: **46 scripts** on **13 reusable modules**,
  guarded by **50 tests**
- Safe against a live system: dry-run, confirmation, retries, idempotency
- Built fast — *and* maintainable, documented, tested

> **AI speed need not mean lower quality — when guardrails are first-class.**

**Honest limits:** stdlib-only trades convenience for control; coverage targets
need discipline to uphold; a human still approves every commit.

<!--
Close on the headline, then the honesty slide content keeps it credible for
engineers: we made real trade-offs. stdlib-only meant more code in places a
library would've shortened. The human-in-the-loop commit rule is a deliberate
trust boundary, not a limitation we regret. End: "vibe coding isn't reckless —
done inside guardrails, it's just fast engineering."
-->
