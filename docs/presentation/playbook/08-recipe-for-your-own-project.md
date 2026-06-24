# A step-by-step recipe for your own project

*The capstone. Everything in this series, distilled into one ordered,
copy-pasteable playbook you can run on a brand-new project of your own — with
example prompts at every step, a worked mini-example on a totally different
project, a first-week timeline, and a one-page checklist.*

> Part 8 of 9 in the Vibe Coding, Safely series

---

Every part before this one taught a piece. This part puts them in order and
hands you the keys.

Up to now the series has built your understanding layer by layer: what vibe
coding actually is ([Part 1](01-what-is-vibe-coding.md)), why speed and safety
aren't opposites ([Part 2](02-speed-inside-guardrails.md)), the thinking you do
*before* any code exists ([Part 3](03-before-you-write-any-code.md)), how to get
your machine set up ([Part 4](04-getting-set-up.md)), the five structural
pillars ([Part 5](05-five-pillars.md)), the daily loop you repeat
([Part 6](06-the-daily-loop.md)), and the discipline of *restraint*
([Part 7](07-deciding-what-not-to-build.md)).

This part is the "now go do it yourself" guide. It's a **ten-step recipe** that
takes a brand-new project from a blank folder to a guarded, tested, live tool —
in the right order. The order is the whole point, so we'll say it up front and
keep coming back to it:

> **Read-only before write. Tests before scale. Guardrails before the big run.**

We'll keep using the running example — the **Asana→Jira migration toolkit** —
because it followed this exact arc in real life. But to prove the recipe
*generalizes*, we'll also walk a completely different hypothetical project
through every step: a tool that **bulk-updates customer records in a CRM**. If
the recipe works for both a Jira migration and a CRM cleanup, it'll work for
yours.

---

## Table of contents

- [How to read this](#how-to-read-this)
- [The recipe at a glance](#the-recipe-at-a-glance)
- [The ten steps, in depth](#the-ten-steps-in-depth)
  - [Step 1 — Name the blast radius](#step-1--name-the-blast-radius)
  - [Step 2 — Create the project and a `CLAUDE.md`](#step-2--create-the-project-and-a-claudemd)
  - [Step 3 — Set up the basics](#step-3--set-up-the-basics)
  - [Step 4 — Build the smallest useful thing first (read-only)](#step-4--build-the-smallest-useful-thing-first-read-only)
  - [Step 5 — Add tests as you go, and run them after every change](#step-5--add-tests-as-you-go-and-run-them-after-every-change)
  - [Step 6 — Factor out helpers the moment you repeat logic](#step-6--factor-out-helpers-the-moment-you-repeat-logic)
  - [Step 7 — Only then add the risky live-write part — with its guardrails](#step-7--only-then-add-the-risky-live-write-part--with-its-guardrails)
  - [Step 8 — Wrap it in a guided interactive menu](#step-8--wrap-it-in-a-guided-interactive-menu)
  - [Step 9 — Prune prototypes and keep `CLAUDE.md` / docs current](#step-9--prune-prototypes-and-keep-claudemd--docs-current)
  - [Step 10 — Commit small and often, always with your approval](#step-10--commit-small-and-often-always-with-your-approval)
- [The ordering principle, restated](#the-ordering-principle-restated)
- [A worked mini-example: a different project entirely](#a-worked-mini-example-a-different-project-entirely)
- [Your first week, day by day](#your-first-week-day-by-day)
- [Common starting mistakes](#common-starting-mistakes)
- [The one-page recipe checklist](#the-one-page-recipe-checklist)
- [Key takeaways](#key-takeaways)
- [Where to next](#where-to-next)

---

## How to read this

This document is **layered**. Each step starts in plain language — what to do
and why it matters — and then goes deeper with example prompts and "what success
looks like." If you're brand new, follow the steps top to bottom and copy the
prompts. If you're an engineer, skim the "what to do" line of each step and dwell
on the deeper notes.

Two conventions, same as the rest of the series:

- Lines you type into the terminal are shown in code blocks; don't type the
  leading `$` if one appears.
- Where a step relies on a concept covered earlier, there's a link to the
  deep-dive part. Each step ends with a **"Go deeper"** pointer so you can dive
  into the relevant pillar exactly when you need it.

---

## The recipe at a glance

Here's the whole thing in one picture. Everything below expands it.

```
  (1) NAME THE BLAST RADIUS
        "The worst this tool can do is ___."   → sets how many guardrails
        │
        ▼
  (2) CREATE PROJECT + CLAUDE.md
        /init drafts the rules · launch `claude` in the folder
        │
        ▼
  (3) SET UP THE BASICS
        terminal · python3 3.11+ · venv · git · secrets OUTSIDE the repo
        │
        ▼
  (4) SMALLEST USEFUL THING — READ-ONLY FIRST
        export to a file · touch nothing live yet
        │
        ▼
  (5) TESTS AS YOU GO ──────────────► run after EVERY change ──┐
        │   `python3 -m unittest discover tests` → OK           │
        ▼                                                       │
  (6) FACTOR OUT HELPERS   the moment you repeat logic          │
        │                                                       │
        ▼                                                       │
  (7) ADD THE RISKY LIVE-WRITE PART  ── + guardrails SAME STEP ─┤
        │   --dry-run · confirm (--yes) · retry · idempotency    │
        ▼                                                       │
  (8) WRAP IN A GUIDED MENU                                     │
        numbered categories · first-launch credentials setup    │
        │                                                       │
        ▼                                                       │
  (9) PRUNE + KEEP DOCS CURRENT                                 │
        delete prototypes · update CLAUDE.md / docs             │
        │                                                       │
        ▼                                                       │
 (10) COMMIT SMALL & OFTEN  ── only with YOUR approval ─────────┘
        every checkpoint is an undo button
```

Steps 1–3 are once-per-project. Steps 4–10 are the cycle you repeat for each
feature — and inside each of them lives the daily loop from
[Part 6](06-the-daily-loop.md).

---

## The ten steps, in depth

### Step 1 — Name the blast radius

**What to do.** Before anything else, write one sentence: *"The worst this tool
can do is ___."* Fill in the blank honestly. That sentence sets how much safety
you'll build.

**Why it matters.** The amount of guardrailing a project needs is not a matter
of taste — it's a function of the worst outcome. A tool that only reads data and
writes a spreadsheet on your own laptop can do almost nothing irreversible; you
need very little ceremony. A tool that *creates thousands of tickets in a
client's live Jira* can do real, public, hard-to-undo damage; it needs every
seatbelt in the box. **Match the safety effort to the blast radius.** Naming it
out loud, in one sentence, is what stops you from over-engineering a harmless
script or — far worse — under-protecting a dangerous one.

In the example project, the honest sentence was: *"The worst this tool can do is
create thousands of wrong work items in a client's live Jira."* That single
admission is *why* the toolkit has dry-runs, confirmation prompts, retries, and
idempotency — none of which would be worth the effort for a read-only reporter.

**Exactly what to ask Claude.** You don't write code here — you think. But Claude
is a useful thinking partner:

> "I'm planning a tool that will read customer records from our CRM and update
> some of their fields in bulk. Help me name the blast radius: what's the worst
> thing this tool could do if it had a bug or I ran it carelessly? List the ways
> it could cause irreversible damage."

> "Given that worst case, which of these guardrails are worth building, and which
> would be overkill: dry-run, confirmation prompt, automatic retry, idempotency,
> a record limit, scope guards?"

**What success looks like.** You can say, in one sentence, the worst outcome —
and you've placed the project on a rough tier: *read-only / writes a local file*
(few guardrails) versus *changes a live, shared system* (many guardrails). You're
not guessing how careful to be; you've decided.

> **Go deeper:** [Part 3 — Before you write any code](03-before-you-write-any-code.md)
> covers the blast-radius tiers and how each tier maps to a defense level.

### Step 2 — Create the project and a `CLAUDE.md`

**What to do.** Make a new project folder, start version control, then create a
`CLAUDE.md` — your written rules for the AI. The fastest way: launch Claude in
the folder and run **`/init`**, which scans the project and drafts the rules file
for you. Then read it and fix anything wrong.

**Why it matters.** `CLAUDE.md` is the file Claude reads **automatically, at the
start of every session** in that project. (It's also recognized under the name
`AGENTS.md`, which can be symlinked to it.) It's your *contract* with the AI:
write your standards once, and the AI inherits them on every turn instead of you
re-explaining them every week. The single most important rule to put in it — and
the one this whole series treats as a non-negotiable trust boundary — is **"never
create a commit without my explicit approval."** That one line is what guarantees
the AI never records something permanent on your behalf.

**Exactly what to ask Claude.** First, the built-in command (type it in the
Claude prompt, not the shell):

```
/init
```

Claude scans your structure, how it's built, how tests run, and the conventions
already in use, and writes a starter `CLAUDE.md`. On a brand-new, nearly-empty
project there isn't much to scan yet, so follow up by *telling* it your
standards in plain English:

> "Create or update `CLAUDE.md` with our standards: Python 3.11+, always invoked
> as `python3` (never bare `python`); standard library only unless we explicitly
> approve a package; tests live next to the code they cover and we aim for 90%+
> coverage; use Conventional Commit messages; and — most important — never create
> a commit unless I explicitly approve it."

As the project grows, keep it alive by *asking*:

> "We just added an export command — add it to a CLI catalog section in
> `CLAUDE.md` with a one-line description of its inputs and outputs."

> "Add a rule to `CLAUDE.md`: every command that writes to a live system must
> support a `--dry-run` flag."

**What success looks like.** A `CLAUDE.md` exists at the top of your project; it
names your language, your "no surprises" rules, and the commit-approval rule.
When you start a fresh Claude session in that folder, it already knows them
without you saying a word. Two handy extras worth knowing: **`/memory`** shows you
every instruction file currently loaded, and a `~/.claude/CLAUDE.md` in your home
folder holds *personal* rules that apply across all your projects.

> **Go deeper:** [Part 5 — Pillar 1](05-five-pillars.md) explains `CLAUDE.md` as
> a contract, and [Part 6](06-the-daily-loop.md) shows the habit of asking Claude
> to *record corrections* in it so lessons stick.

### Step 3 — Set up the basics

**What to do.** Get the foundation in place: the terminal, the right Python, an
isolated workspace (a virtual environment), version control (git), and — this is
the one people botch — a *safe* place for secrets that is **never inside the
project folder.**

**Why it matters.** These are the rails the rest of the recipe runs on. The
virtual environment keeps this project's setup from colliding with the rest of
your machine. Git gives you the undo button that makes moving fast safe. And safe
secret handling is the difference between a private token and a credential leaked
into your git history for the world to find. Secrets are serious: a token that
lands in a commit is a token you must assume is compromised.

**Exactly what to do (and ask).** The mechanics, condensed (the full walkthrough
is Part 4):

```bash
$ python3 --version                                  # need 3.11 or higher
$ python3 -m venv .venv && source .venv/bin/activate # isolated sandbox; prompt shows (.venv)
$ git init                                            # start tracking snapshots
```

For secrets, the pattern is: **store the credentials in a protected file in your
home directory, and keep only a small *pointer* file in the repo** that says
where to find them. The home-dir file is `chmod 600` (readable only by you); the
project never contains the actual token. You can ask Claude to scaffold this:

> "Set up safe secret handling for this project. Credentials must live in a file
> in my home directory with `600` permissions — never in the project folder. Add
> a small pointer file in the repo that names the credentials file's path, and
> make sure both the credentials file and any local data files are in
> `.gitignore` so they can never be committed."

> "Write a short setup note in `CLAUDE.md` explaining where credentials live and
> the order the tool looks for them, so future-me doesn't put a token in the
> repo."

**What success looks like.** `python3 --version` shows 3.11+; your prompt shows
`(.venv)`; `git status` works; your secrets are in a `600`-protected file in your
home directory; and a `.gitignore` guarantees neither the secrets nor local data
files can be committed. Run a quick sanity check — `git status` should *not* list
your credentials file.

> **Go deeper:** [Part 4 — Getting set up](04-getting-set-up.md) is the full,
> click-by-click walkthrough, including the multi-client credentials design and
> the easy-mode credentials setup flow.

### Step 4 — Build the smallest useful thing first (read-only)

**What to do.** Resist the urge to build the exciting, scary feature first. Build
the *smallest thing that's actually useful* — and if at all possible, make it
**read-only**: it reads data and writes a local file, but changes nothing in any
live system.

**Why it matters.** A read-only first step lets you learn the whole shape of the
problem — the data, the APIs, the edge cases — with **zero blast radius.** You
get to make all your beginner mistakes against a spreadsheet on your own disk
instead of against a client's live system. It also produces something *useful on
its own*, so even if you stop there, you've shipped value. This is "earned
complexity": the toolkit didn't begin as a live-Jira creator. It began by
**exporting Asana data to CSV spreadsheets** — read-then-write-*local* — and only
later grew the dangerous parts.

**Exactly what to ask Claude.** Describe inputs and outputs, and *name the blast
radius out loud* as a constraint:

> "I want a command that exports our data to a CSV in an `exports/` folder.
> Input: only our local data — it must NOT touch the live system at all. Output:
> one row per record with these columns: [list them]. Before you write anything,
> give me a plan — which helpers you'd reuse, what files change, and what you'd
> test."

The "must NOT touch the live system" clause is doing real work: it tells Claude
the blast radius and gives you something concrete to verify in review.

**What success looks like.** You can run the command, open the file it produced,
and see correct data — and you're certain nothing live was touched (it never had
the credentials or the code path to do so). You've also learned the data's quirks
cheaply. This is your first real checkpoint to commit (Step 10).

> **Go deeper:** [Part 7 — Deciding what NOT to build](07-deciding-what-not-to-build.md)
> on earning complexity gradually, and [Part 6, Step 1](06-the-daily-loop.md) on
> describing a goal as inputs and outputs.

### Step 5 — Add tests as you go, and run them after every change

**What to do.** Ask Claude to write a **test** alongside every feature — in the
same breath as the feature, not "later." Then run the whole test suite after
*every* change, and treat `OK` as your green light to continue.

**Why it matters.** When you didn't write the code line by line, the test is *how
you know it works* — and keeps working after the next change. Tests are the
automatic safety net that lets you accept fast AI-written code with confidence.
Running them after every change means a mistake gets caught the moment it's
introduced, while it's cheap, instead of three features later when it's buried.
This is the single most important habit in the entire playbook.

The golden rule, worth tattooing somewhere: **every time a bug is found and
fixed, add a test for it.** That turns a one-time fix into a permanent guarantee
the bug never returns.

**Exactly what to ask Claude.** Bundle the test request with the feature, and
push for the *unhappy paths*:

> "Implement that, and alongside it write tests covering a normal record, a
> record with a missing field (so the column comes out blank, not the word
> `None`), and the empty-input case."

> "What edge cases are we not handling here? List them, then write a test for
> each."

And the command you'll run constantly:

```bash
$ python3 -m unittest discover tests
```

You can even let Claude run it for you ("run the tests and report back"), but
know the command yourself — you'll use it dozens of times a day.

**What success looks like.** Every feature arrives with a test; the suite ends in
`OK`; and when a bug shows up, your reflex is "first write a failing test that
pins it, *then* fix it." A red suite is a stop sign — nothing downstream (live
runs, commits) is allowed to proceed past it.

> **Go deeper:** [Part 5 — Pillar 3](05-five-pillars.md) on tests as the safety
> net, and [Part 6, Steps 3–4](06-the-daily-loop.md) on shipping code-with-test
> and the "fix the cause, not the test" discipline.

### Step 6 — Factor out helpers the moment you repeat logic

**What to do.** The *second* time you find yourself solving the same problem —
talking to an API, loading credentials, saving to the database — stop and pull
that logic into one small, well-named shared file (a "helper" or "module"). Then
have both places use it.

**Why it matters.** Left alone, an AI tends to re-solve the same problem slightly
differently in each file — one place handles an error one way, another a
different way — and *inconsistency is where bugs hide.* Solving a hard problem
**once**, in a helper that's tested and trusted, means every future feature
becomes a small, low-risk *composition* of helpers rather than a fresh pile of
risky code. In the example project, a `src/lib/` folder holds the shared
solutions — credential loading, the HTTP client with built-in retry, the Jira
client, database access, the team-picker menu, rich-text conversion — and big
scripts mostly *wire them together* instead of reinventing them.

**Exactly what to ask Claude.** When you notice the repetition, name it:

> "This is the second place we're loading credentials / calling the API / writing
> to the database. Extract that into a single reusable helper in a shared module
> (`src/lib/`), give it a clear name, and use it in both places. Add a test for
> the helper itself."

And to *prevent* future duplication, make it a rule:

> "Add to `CLAUDE.md`: shared logic (credentials, HTTP, database, formatting)
> lives in `src/lib/` and is reused, never re-implemented per script. Before
> writing new low-level code, check whether a helper already exists."

In plan mode, this becomes a thing you can catch *before* code exists: if a plan
says "I'll add a new HTTP client" when one already exists, you reject it in one
sentence.

**What success looks like.** Repeated logic lives in exactly one place, has its
own test, and your scripts read like recipes that combine known-good
ingredients. New features get *smaller and safer* over time instead of larger and
riskier — the sign your helper layer is doing its job.

> **Go deeper:** [Part 5 — Pillar 2](05-five-pillars.md) on reusable helpers, and
> [Part 7](07-deciding-what-not-to-build.md) on why fewer moving parts is itself
> a safety feature.

### Step 7 — Only then add the risky live-write part — with its guardrails

**What to do.** *Now* — and only now, after read-only works, tests are green, and
your helpers exist — add the part that writes to a live system. And add its
guardrails **in the same step**, not as a follow-up: a dry-run, a confirmation
prompt, automatic retry, and idempotency. The feature and its seatbelts ship
together or not at all.

**Why it matters.** This is the step the whole recipe has been protecting. The
moment your tool can change a live, shared system is the moment a bug stops being
your problem and becomes everyone's problem. Tests check the code in a sandbox;
they cannot protect the live run itself. The guardrails do. And they must exist
*before* the first real run, because there's no "undo" on thousands of tickets
created in someone else's Jira. The four to insist on:

- **`--dry-run`** — a mode that prints *exactly* what it would do (every item it
  would create, every field it would set) and **changes nothing.** You inspect it
  like a suspicious auditor, then run for real.
- **A confirmation prompt** (skippable with `--yes`) — before doing anything
  permanent it asks *"About to do N things. Continue? [y/N]"*, defaulting to
  **no.** Nothing happens until you deliberately type `y`.
- **Automatic retry on rate limits** — when the live service says "too many
  requests," the tool waits and retries instead of crashing or losing work.
- **Idempotency** (safe to run twice) — it *remembers what it already did*, so
  re-running after an interruption **heals** the job instead of creating
  duplicates.

The example project added exactly these as it grew: direct REST creation to live
Jira came *after* the CSV export, and it arrived wrapped in dry-run,
confirmation, retry/requeue, an assignee fallback (drop one bad assignment rather
than fail the batch), a `--reset` to safely redo work, and later a two-pass
"clear then set" repair so no stale half-updated data could survive an
interruption.

**Exactly what to ask Claude.** Name the dangerous action, then name the
seatbelts in the *same* prompt:

> "Now add the command that writes the updates to the live system. In the same
> change, add: a `--dry-run` mode that prints exactly what it *would* send and
> changes nothing; a confirmation prompt that shows a count and requires me to
> type `y` (skippable with `--yes`); automatic retry when the service rate-limits
> us; and idempotency — remember what's already done so a re-run skips it instead
> of duplicating. Add tests for the rate-limit retry and for the
> safe-to-run-twice behavior."

> "Also add a `--limit` flag so I can do a tiny real run of a handful of records
> before the whole batch, and a scope guard that refuses contradictory targets
> (e.g., one record AND everything at once)."

**What success looks like.** Running `--dry-run` prints a plan you can audit; a
real run asks before it writes; an interrupted run, re-run, *heals* rather than
duplicates; and a rate limit slows the tool down instead of breaking it. You do a
small `--limit` run first, read its output, and only then scale up. **If your tool
*can't* dry-run the dangerous action, that's the gap to close before you ever run
it live.**

> **Go deeper:** [Part 5 — Pillar 4](05-five-pillars.md) on runtime guardrails,
> [Part 6, Step 5](06-the-daily-loop.md) on the dry-run discipline, and
> [Part 3](03-before-you-write-any-code.md) on why Tier-3 work demands all of
> this.

### Step 8 — Wrap it in a guided interactive menu

**What to do.** Once the commands exist, wrap them in a single friendly,
guided entrypoint — a menu with numbered categories that walks the operator
through each step, instead of requiring them to type a long, perfectly-correct
command.

**Why it matters.** A powerful tool is useless — and *dangerous* — if the only
way to run it is to type twelve exactly-right options. People will get one wrong,
and "wrong" on a live system means bad data. **Accessibility is a safety
feature.** A guided menu that asks questions, offers searchable pickers ("choose
a team by typing part of its name"), and *carries the guardrails from Step 7
right into the flow* (the dry-runs and confirmations are built into the menu's
path) means the easy way to run the tool is also the safe way. The example
project's `scripts/interactive_cli.py` does exactly this: numbered categories
(Initialization, Tasks, Atlassian/Jira, Reports, Credentials) and — crucially — a
**credentials setup flow on first launch** that, if it can't find your tokens,
*asks you* for them and writes the files for you.

**Exactly what to ask Claude.** Plan it first (it touches a lot), then build:

> "Before writing anything, plan an interactive menu (`scripts/interactive_cli.py`)
> that wraps our existing commands. I want numbered categories, a step-by-step
> flow for each command, searchable pickers where I'd otherwise have to paste an
> id, and — on first launch, if it can't find credentials — a setup flow that
> asks me for them and writes the files. The menu must route every live-write
> action through the existing dry-run and confirmation prompts. Which existing
> commands and helpers would it reuse?"

**What success looks like.** A non-expert can run `python3 -m scripts.interactive_cli`,
be greeted (set up credentials on first launch if needed), pick a numbered option,
and be walked safely through to a confirmation prompt — without ever memorizing a
command or pasting an id. The guardrails are inescapable because they're on the
guided path.

> **Go deeper:** [Part 5 — Pillar 5](05-five-pillars.md) on the interactive CLI
> as a safety feature.

### Step 9 — Prune prototypes and keep `CLAUDE.md` / docs current

**What to do.** Periodically *delete* the scaffolding and experiments that have
served their purpose, and keep your `CLAUDE.md` and docs in sync with what the
tool actually does now.

**Why it matters.** Every file, flag, and dependency you keep is something to
maintain, secure, and potentially break. **Default to less.** The example project
deleted a scratch helper (the "note shuttle") once it was no longer needed,
rather than letting it rot — and it declined convenient third-party libraries to
keep its attack surface tiny. Equally important: docs are a **feedback loop**.
*Claude re-reads `CLAUDE.md` and your docs every session*, so stale docs make the
AI's next contribution worse, and accurate docs make it better. Keeping docs
current isn't bureaucracy — it's literally improving your collaborator's context.

**Exactly what to ask Claude.** For pruning:

> "Which files, flags, or experiments in this project are no longer used by
> anything? List them with where (or whether) they're still referenced, so I can
> decide what to delete. Don't delete anything yet."

For keeping docs honest:

> "Review `CLAUDE.md` and the `docs/` against the current commands. Flag anything
> that's drifted — a command that changed, a flag that was added or removed, a
> description that's now wrong — and propose the updates."

> "We just renamed/added/removed that flag — update the CLI catalog in
> `CLAUDE.md` and the matching doc to match."

**What success looks like.** No dead prototypes lying around; `CLAUDE.md` and the
docs describe the tool *as it is today*; and the next session of Claude starts
from accurate context. A good test: hand someone the docs and have them run a
command — if reality matches the page, your feedback loop is healthy.

> **Go deeper:** [Part 7 — Deciding what NOT to build](07-deciding-what-not-to-build.md)
> on restraint and documentation as a feedback loop.

### Step 10 — Commit small and often, always with your approval

**What to do.** Save your progress as **commits** — small, frequent, coherent
snapshots — and *always* approve each one yourself. Never let the AI commit
without your explicit yes.

**Why it matters.** A commit is a saved snapshot you can return to: your **undo
button.** Small, frequent commits give you precise places to roll back to; one
giant end-of-day commit gives you a single blunt undo and a reviewer's nightmare.
And the *human-approval* rule is the trust boundary that runs through this whole
series — the bright line between "the AI suggests" and "the AI records something
permanent on your behalf." Because every commit is a reviewed, test-passing
known-good state, you are *never more than one revert away from safety*, which is
exactly what makes it psychologically safe to move at AI speed.

**Exactly what to ask Claude.** When a change is reviewed and green:

> "This looks good — commit it. Propose a Conventional Commit message scoped to
> the folder we touched, and show it to me before committing."

When something went sideways:

> "This made things worse. Revert to the last commit so we're at a clean state,
> then let's try a different approach."

And the standing rule, already in your `CLAUDE.md` from Step 2:

> "Never create a commit unless I explicitly approve it."

**What success looks like.** Your history is a series of small, human-reviewed,
test-passing checkpoints with clear messages — and the AI has *never* committed
anything you didn't sign off on. When something later breaks, you have a precise,
recent good state to return to.

> **Go deeper:** [Part 6, Step 7](06-the-daily-loop.md) on commits as the trust
> boundary and undo button, and its "when Claude goes wrong" recovery moves.

---

## The ordering principle, restated

If you remember nothing else from this part, remember the order — because the
order is what converts vibe coding from a gamble into a method:

> **Read-only before write.** (Step 4 before Step 7.) Learn the problem with zero
> blast radius before you can damage anything.
>
> **Tests before scale.** (Step 5 woven throughout.) Never run a big job on code
> that isn't proven by tests and a dry-run.
>
> **Guardrails before the big run.** (Step 7's seatbelts ship *with* the feature.)
> The dry-run, confirmation, retry, and idempotency exist before the first real
> write, not after the first disaster.

Follow that order and you get the *speed* of vibe coding with the *safety* of
careful engineering. Violate it — write the live feature first, scale before
testing, add guardrails after a scare — and you get the worst of both: AI speed
pointed at a live system with the seatbelts still in the box.

---

## A worked mini-example: a different project entirely

The recipe isn't about Asana or Jira. To prove it, here's the *same ten steps*
applied to a totally different project: **a tool that bulk-updates customer
records in a CRM** — say, normalizing phone-number formats and tagging dormant
accounts across tens of thousands of customers. Live, shared, high-stakes,
nothing to do with the example toolkit.

1. **Name the blast radius.** *"The worst this tool can do is corrupt or
   overwrite the wrong fields on thousands of real customer records in our live
   CRM."* That's clearly a many-guardrails project. Decided.

2. **Create the project + `CLAUDE.md`.** New folder, `git init`, launch `claude`,
   run `/init`, then: *"Create a `CLAUDE.md`: Python 3.11+ as `python3`, standard
   library only unless approved, tests next to code, Conventional Commits, and
   never commit without my approval."*

3. **Set up the basics.** `python3 --version`, `python3 -m venv .venv && source
   .venv/bin/activate`, `git init`. Secrets: the CRM API token goes in a
   `600`-protected file in your home directory; a pointer file in the repo names
   its path; `.gitignore` covers both the pointer's target and any local data.

4. **Smallest useful thing, read-only.** *Don't* start with the update. Start with
   an **export**: *"A command that reads all customer records from the CRM and
   writes them to a CSV in `exports/`, with the fields we plan to change. It must
   only read — never write to the CRM. Plan it first."* Now you can see the data,
   spot how messy the phone numbers really are, and identify which accounts count
   as "dormant" — all with zero risk.

5. **Tests as you go.** Every piece arrives with a test: *"Write tests for the
   phone-normalizer — a US number with dashes, one with parentheses, an
   already-clean number, an empty string, and garbage input."* Run `python3 -m
   unittest discover tests` after each change; `OK` is the green light. When you
   discover the normalizer mangles international numbers, you *first* write a
   failing test for that case, then fix it.

6. **Factor out helpers.** The second time you touch the CRM API, extract a
   `crm_client.py` helper with the auth and retry logic in one place; the second
   time you load the token, extract a `config.py`. Now the export command and the
   (coming) update command share the same trusted plumbing.

7. **Only then, the risky write — with guardrails.** *Now* add the update command,
   and in the same change: a `--dry-run` that prints "would change customer 12345:
   phone `(555) 123-4567` → `+15551234567`; would add tag `dormant`" and writes
   nothing; a confirmation prompt (*"About to update N customers. Continue?
   [y/N]"*, `--yes` to skip); automatic retry on the CRM's rate limits; and
   idempotency — record each updated customer id so a re-run skips them instead of
   re-applying. Add a `--limit` flag and do a 10-record real run first. Tests for
   the retry and the run-twice behavior.

8. **Wrap it in a guided menu.** *"Add an interactive menu: numbered options for
   Export, Dry-run update, and Live update; on first launch, if there's no CRM
   token, ask for it and save it; route the live update through the dry-run and
   confirmation."* Now a colleague can run the cleanup safely without memorizing
   flags.

9. **Prune + keep docs current.** Delete the throwaway script you used to eyeball
   the messy data. Update `CLAUDE.md`'s command catalog with the export, the
   dry-run, and the update commands and their flags.

10. **Commit small and often.** A commit after the export works; another after the
    normalizer + tests; another after the update command + guardrails; each one
    reviewed, green, and approved by you. When the international-number bug
    surfaced, you reverted nothing — but you *could* have, to a checkpoint minutes
    old.

Same recipe, same order, completely different domain. **Read-only before write,
tests before scale, guardrails before the big run** — and the CRM cleanup is as
safe as the Jira migration was.

---

## Your first week, day by day

A realistic, gentle pace for a beginner taking a real project from zero. Yours
may compress or stretch — the *order* matters more than the calendar.

- **Day 1 — Decide and set up.** Name the blast radius (Step 1). Create the
  project and run `/init` for a `CLAUDE.md` (Step 2). Do the machine setup:
  Python, venv, git, safe secrets (Step 3). End the day with a `CLAUDE.md`, a
  green-but-empty test run, and your credentials safely outside the repo.

- **Day 2 — Ship one read-only thing.** Build the smallest useful read-only
  command (Step 4) with a test (Step 5). Run it, open its output, confirm it's
  right. Commit it with your approval (Step 10). You've shipped something real and
  touched nothing live.

- **Day 3 — Add a second read-only feature, and notice the repetition.** Build
  one more read-only command. The moment you reuse logic, factor out your first
  helper (Step 6). Tests stay green throughout. Two commits.

- **Day 4 — Design the risky part (don't build it yet).** Use plan mode to design
  the live-write command *and its guardrails together* (Step 7). Review the plan,
  correct assumptions. No live code yet — just an agreed approach.

- **Day 5 — Build the risky part behind a dry-run.** Implement the live-write
  command with `--dry-run`, confirmation, retry, and idempotency, plus tests
  (Step 7). Run *only* `--dry-run`. Read its output like an auditor. Do a tiny
  `--limit` real run. Commit.

- **Following days — wrap, prune, and scale carefully.** Add the guided menu
  (Step 8). Prune scaffolding and update docs (Step 9). Do progressively larger
  real runs, always dry-running first. Commit small and often the whole way
  (Step 10).

The shape to notice: **the dangerous capability doesn't appear until day 5, on
top of a tested, helper-backed, version-controlled foundation** — and even then
it hides behind a dry-run. That's the recipe protecting you on a real timeline.

---

## Common starting mistakes

The predictable ways beginners trip on a new project — each one is the recipe's
order being violated.

- **Building the scary feature first.** Jumping straight to the live write because
  it's the exciting part, before any read-only learning or tests exist. *Fix:* do
  Step 4 before Step 7, every time. Read-only first is not a warm-up you can
  skip; it's where you make your mistakes cheaply.

- **Treating tests as "later."** "I'll add tests once it works." It never happens,
  and you end up trusting code you can't verify. *Fix:* ask for the test in the
  same breath as the feature (Step 5), and run the suite after every change.

- **Adding guardrails after a scare.** Running the live feature once "just to see,"
  then adding the dry-run after it does something regrettable. *Fix:* the
  guardrails ship *in the same step* as the live feature (Step 7). No first run
  without them.

- **Putting secrets in the project folder.** A token in a config file inside the
  repo, one `git commit` away from leaking forever. *Fix:* secrets live outside
  the repo in a protected file, with only a pointer inside (Step 3), and
  `.gitignore` as a backstop.

- **Letting `CLAUDE.md` go stale — or skipping it entirely.** Re-explaining your
  standards every session, or letting the docs drift until Claude works from a
  wrong picture. *Fix:* create it on day 1 (Step 2) and ask Claude to record every
  correction and new command in it (Step 9).

- **Giant, unreviewed commits.** A whole afternoon piled into one commit you can't
  review and can't selectively undo. *Fix:* small, coherent, frequent commits,
  each reviewed and approved (Step 10).

- **Skipping the plan on non-trivial work.** Letting Claude charge into a
  multi-file feature, then discovering the approach was wrong after 300 lines
  exist. *Fix:* ask for a plan first; a plan is a sentence to fix, finished code
  is not ([Part 6](06-the-daily-loop.md)).

- **Over-engineering a harmless tool.** The opposite failure: lavishing
  confirmation prompts and idempotency on a script that only writes a local file.
  *Fix:* Step 1 — match the effort to the blast radius. Less can be the right
  answer.

---

## The one-page recipe checklist

Copy this. Pin it somewhere. It's the whole recipe as a thing you can tick off.

```
RECIPE FOR YOUR OWN PROJECT — checklist

ONCE PER PROJECT
[ ] 1. BLAST RADIUS: I wrote "The worst this tool can do is ___" and sized
       my guardrails to it.
[ ] 2. PROJECT + CLAUDE.md: folder created, `/init` run, CLAUDE.md has my
       language, my rules, and "never commit without my approval".
[ ] 3. BASICS: python3 3.11+ · venv active (.venv) · git init · secrets in a
       600 file OUTSIDE the repo · .gitignore covers secrets + data.

PER FEATURE (repeat)
[ ] 4. SMALLEST USEFUL THING, READ-ONLY FIRST: it touches nothing live; I
       inspected its real output.
[ ] 5. TESTS AS I GO: feature + test shipped together; suite ends in OK;
       every bug fix gets a regression test.
[ ] 6. HELPERS: the 2nd time I repeated logic, I extracted it to a shared
       module and used it in both places.
[ ] 7. RISKY WRITE = SAME STEP AS GUARDRAILS:
       [ ] --dry-run (prints, changes nothing)
       [ ] confirmation prompt (--yes to skip)
       [ ] automatic retry on rate limits
       [ ] idempotency (re-run heals, doesn't duplicate)
       [ ] tested with --limit on a tiny real run first
[ ] 8. GUIDED MENU: numbered categories · first-launch credentials setup ·
       guardrails on the guided path.
[ ] 9. PRUNE + DOCS: deleted dead prototypes · CLAUDE.md and docs match the
       tool as it is today.
[ ] 10. COMMIT SMALL & OFTEN, with MY approval · Conventional Commit message.

THE ORDER (non-negotiable)
   Read-only BEFORE write · Tests BEFORE scale · Guardrails BEFORE the big run.
```

---

## Key takeaways

- **The recipe is the whole series, in order.** Ten steps: name the blast radius
  → project + `CLAUDE.md` → basics → smallest read-only thing → tests as you go →
  helpers → the risky write *with* its guardrails → guided menu → prune + docs →
  commit small and often. Steps 1–3 are once; 4–10 repeat per feature.

- **The order is the safety.** *Read-only before write, tests before scale,
  guardrails before the big run.* This single ordering is what turns vibe coding
  from a gamble into a method. Violating it gives you AI speed pointed at a live
  system with no seatbelts.

- **Guardrails ship *with* the dangerous feature, never after.** Dry-run,
  confirmation, retry, and idempotency are part of the same change as the
  live-write code — because there's no undo on a botched live run.

- **It generalizes.** The same ten steps took both an Asana→Jira migration and a
  CRM bulk-update from blank folder to guarded, tested, live tool. Your project is
  next.

- **Let Claude do the typing, but you keep the boundaries.** `/init` drafts your
  rules, plan mode designs the approach, tests prove the work, and the AI writes
  most of the code — but *you* name the blast radius, *you* approve every commit,
  and *you* answer every confirmation prompt. Speed inside guardrails.

---

## Where to next

- **Previous:** [07 — Deciding what NOT to build](07-deciding-what-not-to-build.md)
- **Up:** [The Claude Code playbook (parent)](../claude-code-playbook.md)
- **Next:** [09 — Pitfalls and honest limits](09-pitfalls-and-limits.md)

Other parts in the series:
[01 — What is vibe coding](01-what-is-vibe-coding.md)
· [02 — Speed inside guardrails](02-speed-inside-guardrails.md)
· [03 — Before you write any code](03-before-you-write-any-code.md)
· [04 — Getting set up](04-getting-set-up.md)
· [05 — The five pillars](05-five-pillars.md)
· [06 — The daily loop](06-the-daily-loop.md)
· [Series index](README.md)
