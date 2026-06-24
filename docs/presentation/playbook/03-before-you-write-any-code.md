# Before you write any code

*The hour of thinking that saves you days of cleanup — naming the blast radius,
choosing your standards on purpose, and knowing the four tools you're about to
pick up.*

> Part 3 of 9 in the Vibe Coding, Safely series

---

There's a strong temptation, the moment you open Claude Code, to type "build me
a thing" and watch the magic happen. Resist it for one hour.

The single most leveraged work in an AI-assisted project happens **before any
code is generated**. It is cheap (a worksheet and a conversation), it is fast
(an hour, maybe less), and it sets the ceiling on how badly things can go wrong
and how fast they can go right. The Asana→Jira migration toolkit — our running
example, a Python tool that reads everything out of a company's Asana account
and then *creates real tickets in a live Jira* — is exactly the kind of project
that rewards this hour and punishes skipping it.

This part of the series is that hour, expanded. Three questions, in order:

1. **What is the worst this tool could do?** (Name the blast radius.)
2. **What rules will the tool — and the AI — always follow?** (Choose standards
   deliberately.)
3. **What am I actually touching?** (Know your four tools.)

You won't write code in this part. That's the point.

---

## Table of contents

- [Why an hour up front beats a day of cleanup](#why-an-hour-up-front-beats-a-day-of-cleanup)
- [Part 1 — Naming the blast radius](#part-1--naming-the-blast-radius)
  - [The one-sentence test](#the-one-sentence-test)
  - [A tiered model of blast radius](#a-tiered-model-of-blast-radius)
  - [How each tier dictates guardrail investment](#how-each-tier-dictates-guardrail-investment)
  - [Where the Asana→Jira toolkit lands (and why it's tier 3)](#where-the-asanajira-toolkit-lands-and-why-its-tier-3)
- [Part 2 — Choosing standards deliberately](#part-2--choosing-standards-deliberately)
  - [Language and version: Python 3.11+, always `python3`](#language-and-version-python-311-always-python3)
  - [The standard-library-only decision](#the-standard-library-only-decision)
  - [Naming, types, and shape of the code](#naming-types-and-shape-of-the-code)
  - [Testing and coverage targets](#testing-and-coverage-targets)
  - [Commit discipline and the trust boundary](#commit-discipline-and-the-trust-boundary)
  - [Where secrets live](#where-secrets-live)
- [Part 3 — Knowing the tools you'll touch](#part-3--knowing-the-tools-youll-touch)
  - [The terminal](#the-terminal)
  - [Python](#python)
  - [Git: commits as an undo button](#git-commits-as-an-undo-button)
  - [Claude Code: an agent that reads, writes, and runs](#claude-code-an-agent-that-reads-writes-and-runs)
- [The decision worksheet](#the-decision-worksheet)
- [The pre-flight checklist](#the-pre-flight-checklist)
- [Key takeaways](#key-takeaways)
- [Where to next](#where-to-next)

---

## Why an hour up front beats a day of cleanup

When you let an AI write code fast, the cost of a bad decision doesn't go away —
it moves. Without up-front thinking, the cost shows up *later*, in the worst
possible place: as wrong data in a live system, as a secret accidentally
committed to a repository, as three scripts that each solve "talk to Jira" a
slightly different (and slightly broken) way.

The up-front hour is a forcing function. It makes you answer questions that are
nearly free to answer now and brutally expensive to answer after the fact:

- If I don't decide my standards now, the AI will invent its own — and they'll
  drift from file to file.
- If I don't name the blast radius now, I won't know how many guardrails are
  "enough," so I'll either over-build or (far worse) under-build.
- If I don't know what Git does, I won't have an undo button when I need one
  most.

None of this requires you to be a software engineer. It requires you to be
deliberate for sixty minutes. Let's spend them.

---

## Part 1 — Naming the blast radius

> **Blast radius** (plain language): the worst thing this tool could do if it
> ran wrong — to the maximum extent. Not "what will it usually do," but "what's
> the most damage a single careless run could cause?"

This is the first and most important question because **everything else scales
off the answer.** The number of guardrails you build, the amount of testing you
demand, how paranoid your confirmation prompts are — all of it is a response to
the blast radius. Get this wrong and you either waste effort armoring a harmless
tool, or — much more dangerous — you ship a live-system tool with the caution of
a scratch script.

### The one-sentence test

Before anything else, write one sentence and fill in the blank:

> *"The worst this tool can do is ___."*

For the migration toolkit, the honest sentence is:

> *"The worst this tool can do is create thousands of wrong tickets in a
> company's live Jira."*

Say it out loud. If the sentence makes you a little uncomfortable, good — that
discomfort is the correct calibration. If the sentence is "the worst it can do
is print a wrong number to my screen," then you can relax most of the
machinery this series describes. The sentence *is* the spec for your safety
work.

A few tips for writing an honest version:

- **Assume the run is wrong, not right.** Don't write what it does when it works;
  write what happens when it misfires — bad input, a logic bug, the wrong
  client's credentials loaded by mistake.
- **Assume scale.** "It creates one wrong ticket" and "it creates 8,000 wrong
  tickets in a loop" are different sentences. The tool runs at machine speed.
- **Assume the operator is tired.** The person running it at 5pm on a Friday
  will fat-finger a flag. Your sentence should survive that.

### A tiered model of blast radius

Most tools fall into one of three tiers. Knowing your tier turns a vague worry
into a concrete budget for guardrails.

| Tier | What it does | Reversible? | Worst realistic outcome | Example |
|---|---|---|---|---|
| **Tier 1 — Read-only** | Reads data; produces nothing durable, or only things on your own screen | Fully (close the window) | A wrong answer you act on | A script that *queries* Asana and prints a count |
| **Tier 2 — Writes local files** | Reads data and writes to *your own* machine — files, a local database | Yes (delete the file) | Corrupt local data; a misleading export; a leaked secret if you're careless | The toolkit's export-to-CSV and the local `data/asana.sqlite` cache |
| **Tier 3 — Writes a live system** | Sends changes to a system other people depend on, over the network | **Often not** | Thousands of wrong, hard-to-undo changes others see | The toolkit *creating issues in live Jira* |

The dividing lines that matter:

- **Tier 1 → Tier 2** is crossed the moment your tool leaves something behind on
  disk. Now you can corrupt data, and — critically — you can *write a secret into
  a file*. That's why even a "read-only" tool that caches credentials is really a
  Tier 2 tool.
- **Tier 2 → Tier 3** is crossed the moment your tool changes something *outside
  your machine* that other people rely on. This is the big jump. Local mistakes
  you fix with `rm`. Live-system mistakes you fix with an apology to a client and
  a frantic afternoon of cleanup — if they're fixable at all.

A single project usually spans tiers. The migration toolkit is Tier 2 when it
exports a spreadsheet and Tier 3 when it creates Jira issues. **You assess each
capability at its own tier, and you build the project so the dangerous tier
comes last** (more on that sequencing in
[Part 8](08-recipe-for-your-own-project.md)).

### How each tier dictates guardrail investment

Here's the payoff: the tier tells you how much of the machinery to build, and
when.

**Tier 1 (read-only).** Minimal guardrails. You still want correctness — a
wrong answer can mislead a decision — so you want a *test or two* on the tricky
logic. But you do not need confirmation prompts, dry-runs, or idempotency. There
is nothing permanent to confirm. Don't over-engineer it.

**Tier 2 (writes local files).** Moderate guardrails, with two specific
concerns:

- **Don't lose or corrupt local data silently.** Tests around the
  delete/replace and save/load logic earn their keep here. (The toolkit has a
  test that confirms replacing a task's comments correctly marks the old ones
  removed rather than duplicating or losing them.)
- **Don't write a secret to disk in the project folder.** This is the quiet
  Tier-2 landmine — see [Where secrets live](#where-secrets-live).

You generally don't need a confirmation prompt to write a local file; you *do*
need to make sure the file goes somewhere safe (the toolkit writes exports to
`exports/` and its cache to `data/asana.sqlite`, both local and disposable) and
that no credential ever lands inside the repo.

**Tier 3 (writes a live system).** Maximum guardrails, and this is where you
spend most of your safety budget. For anything in this tier, insist — *before*
the first large run — on at least:

- **A dry-run mode** that shows exactly what *would* be sent, changing nothing.
- **A confirmation prompt** ("About to create N issues. Continue?") that defaults
  to "no."
- **Automatic retry on rate-limiting**, so a busy server doesn't lose your work.
- **Idempotency** — safe to run twice. If a run is interrupted, re-running
  *heals* the migration instead of duplicating it.
- **Scope guards** that refuse contradictory or over-broad instructions, so you
  can't accidentally target the entire company.

These aren't gold-plating; for a Tier-3 tool they are the table stakes that make
fast, AI-written code *acceptable* to run against something real. The five
pillars in [Part 5](05-five-pillars.md) build them out; the daily loop in
[Part 6](06-the-daily-loop.md) puts them to use; what to *not* build is covered
in [Part 7](07-deciding-what-not-to-build.md).

> **Rule of thumb:** Match the safety effort to the blast radius. A read-only
> tool with a battery of confirmation prompts is over-built. A live-system tool
> without a dry-run is *under*-built — and that's the one that ends careers.

### Where the Asana→Jira toolkit lands (and why it's tier 3)

The toolkit is the textbook Tier-3 case, which is exactly why it makes a good
teaching example. Its flow is:

1. **Read everything** out of Asana — users, teams, projects, tasks, comments,
   attachments, custom fields. *(Tier 1: pure reads.)*
2. **Store it locally** in a SQLite database at `data/asana.sqlite`, and export
   spreadsheets to `exports/`. *(Tier 2: local writes.)*
3. **Create the matching items in a live Jira** over the network. *(Tier 3:
   live-system writes — the scary part.)*

Step 3 is irreversible at scale and visible to everyone in the client's Jira.
That single fact is why the project earns every guardrail it has: dry-run modes,
confirmation prompts, retry-on-rate-limit, idempotent re-runs that skip
already-created items, an assignee fallback that drops one bad assignment rather
than failing a whole batch, and scope guards that refuse "one project *and* all
teams" at once. None of that would be worth building for a tool that only printed
a number. All of it is mandatory for a tool that writes to a client's live
system.

---

## Part 2 — Choosing standards deliberately

> **Standards** (plain language): the house rules your project follows — what
> language and version, what's allowed and forbidden, how things are named, how
> much testing is enough, and what the AI may and may not do on its own.

The key word is **deliberately**. If you don't choose these, they get chosen for
you — by the AI's defaults, which drift from session to session, and by whatever
happened to work the first time. Decide them now, write them down later (in the
rules file — [Pillar 1 in Part 5](05-five-pillars.md)), and the AI inherits them
on every single turn instead of you re-explaining them.

In this project, those standards live in a file the repository calls
`CLAUDE.md` — which is actually a symlink to `AGENTS.md`, so the same house
rules apply whether a tool looks for the Claude name or the generic agent name.
Here's each standard the toolkit chose, and — more usefully — *why*.

### Language and version: Python 3.11+, always `python3`

**The choice:** CPython 3.11 or newer, and every command, script, and doc
invokes the interpreter as `python3` — never the bare word `python`.

**Why Python:** it's readable, ubiquitous on macOS and Linux, and has a deep
standard library (which the next decision leans on hard). For a tool a
non-engineer will read and run, readability wins.

**Why pin 3.11+:** newer Python gives you modern type hints and better error
messages, and pinning a floor means the AI won't quietly use a feature your
machine can't run. A version floor is a small thing that prevents a confusing
class of "works on my machine" failures.

**Why insist on `python3`, not `python`:** on many systems the bare word
`python` either doesn't exist or points at an ancient Python 2. Spelling it
`python3` everywhere removes an entire category of "wrong interpreter" bugs that
are maddening to diagnose. It costs nothing and it's unambiguous — so the rule
is absolute.

### The standard-library-only decision

**The choice:** the toolkit uses **only Python's built-in standard library**. No
`requests` for HTTP, no `pytest` for testing — nothing you'd `pip install`. Any
exception must be documented and approved *in advance*.

This is unusual, so it's worth being honest about both sides.

**What you gain:**

- **It runs anywhere, with nothing to install.** Clone it, and as long as you
  have Python 3.11+, it works. No dependency resolution, no version conflicts, no
  "but it worked last month."
- **A tiny attack surface.** Every third-party package is code you didn't write,
  running with your permissions, holding your credentials. Zero dependencies
  means zero supply-chain risk from this dimension. For a tool handling a
  client's API tokens and writing to their live Jira, that's a serious safety
  property, not a nicety.
- **Fewer moving parts to break or to keep updated.** Nothing to patch when a
  dependency ships a security advisory.

**What you pay:**

- **More code by hand.** Talking to an API with the built-in `urllib` is wordier
  than with `requests`. The toolkit's HTTP client imports only
  `json, ssl, time` and `urllib` — and that means it implements retry and
  rate-limit handling itself instead of getting them from a library.
- **You reinvent some wheels.** Testing uses the standard library's `unittest`
  rather than the friendlier `pytest`.

**Why it was the right call *here*:** the blast radius decides it. A Tier-3 tool
that holds client secrets and writes to a live system benefits enormously from
"zero third-party code in the trust boundary," and the extra hand-written code is
a price worth paying — especially when an AI is doing most of the writing. The
trade-off would point the *other* way for a throwaway Tier-1 script, where
`requests` saving you ten lines is pure upside. **Choose your constraints to fit
your blast radius; they cut both ways.** ([Part 9](09-pitfalls-and-limits.md)
returns to this honestly.)

### Naming, types, and shape of the code

**The choice:** PEP 8 style with 4-space indentation, descriptive `snake_case`
names, and a strong preference for dataclasses and type hints.

**Why:** consistency is a safety feature when you're reading code you didn't
write line-by-line. If everything is named and shaped the same way, surprises
stand out. Type hints (e.g., declaring that a function takes a string and returns
a `Credentials` object) let both you and the tools catch a whole class of
mistakes before the code ever runs, and they document intent for the next reader
— human or AI. The toolkit's credential loader, for instance, is a frozen
`dataclass` with typed fields, so it's obvious at a glance what a set of
credentials contains and that it can't be mutated after loading.

There's also a structural rule worth stealing: **keep each module focused on a
single responsibility, with one public entrypoint per file.** That's what makes
the reusable-helpers pillar ([Part 5](05-five-pillars.md)) possible — small,
single-purpose files are the things you reuse instead of re-solve.

### Testing and coverage targets

**The choice:** tests live under `tests/unit` and `tests/integration`, named
`<subject>.test.py`, mirroring the `src/` layout two levels deep so every module
has a neighboring test. Target **≥90% statement coverage**.

**Why mirror the layout:** when the test for `src/lib/config.py` lives at a
predictable spot under `tests/`, nobody has to hunt for it — including the AI,
which can find and update the right test automatically.

**Why a coverage *number*:** "coverage" is the percentage of your code that the
tests actually exercise. A concrete target (90%) turns "did we test enough?" from
a vibe into a measurable check. It's not a guarantee of correctness — you can hit
90% with shallow tests — but as a floor it forces you to test the paths you'd
otherwise skip. The honest caveat ([Part 9](09-pitfalls-and-limits.md)): a number
only helps if you actually uphold it when you're moving fast.

**Why this matters most for vibe coding:** when you didn't write the code
line-by-line, tests are how you *know* it works and keeps working after the next
AI-generated change. They are the backbone — [Pillar 3 in Part 5](05-five-pillars.md)
makes the full case.

### Commit discipline and the trust boundary

**The choice (two parts):**

1. Every change uses a **Conventional Commit** message — a structured format like
   `feat(tasks): add recurring scheduling`, with the scope naming the `src/`
   folder you touched.
2. **The AI never creates a commit unless the human explicitly approves it.**

**Why structured commits:** the commit history becomes readable. Six months from
now, `feat(atlassian): add --dry-run to issue creation` tells you exactly what
changed and where, without opening the diff. Consistent messages also make the
history something you can search and trust.

**Why "never commit without approval" is the most important rule in the file:**
this is your **trust boundary** — the bright line between "the AI suggests" and
"the AI commits something permanent on your behalf." A commit is a checkpoint in
your project's permanent history. Keeping the human in the loop on every commit
means a person always reviews before anything is recorded, and there's no path
where the AI quietly accumulates changes you didn't sign off on. It is the
human-accountability anchor of the whole method: *you* approve every commit and
every live run. ([Part 9](09-pitfalls-and-limits.md): you're still accountable —
treat the prompts as real decisions, not speed bumps.)

There's a documentation rule that rides along with this: **every new CLI script
must be documented** in the `docs/` reference *and* in the command catalog inside
the rules file. That keeps the description and the tool from drifting apart — and
because the AI re-reads those docs each session, good docs literally make its next
contribution better ([Part 7](07-deciding-what-not-to-build.md)).

### Where secrets live

This is a standard, not an afterthought, and it's the one place a Tier-2 mistake
becomes a Tier-3 disaster. **Secrets never live in the project folder**, because
anything in the folder can be committed and leaked. Instead, the toolkit keeps
credentials in a protected file in your *home* directory.

The mechanics, drawn straight from the project's `src/lib/config.py`:

- **Resolution order.** The loader looks for credentials in this order: (1) the
  process's environment variables, then (2) a file whose path is named by a small
  pointer file (`.asana_jira_env_path`) in the project, then (3) a default
  `~/.asana_jira_env` in your home directory.
- **The keys it expects:** `ASANA_TOKEN`, `ASANA_WORKSPACE_GID`,
  `SKIP_SSL_VERIFICATION`, `ATLASSIAN_USERNAME`, `ATLASSIAN_TOKEN`.
- **File permissions.** Credential files are written `chmod 600` — readable and
  writable only by you.
- **`SKIP_SSL_VERIFICATION` stays `false`.** Turning off SSL verification means
  your tool will talk to a server without confirming it's the real one — only
  ever flip it true for a deliberate, temporary debugging reason, never as a
  default.

**Why the home-directory-plus-pointer design:** some teams migrate several
clients at once. Keeping one credentials file per client in your home directory,
with a tiny pointer file in each project copy, lets you have multiple copies of
the project each safely aimed at a different client's data — and *no* secret ever
sits inside a folder that could be committed. The full walkthrough of setting
this up is in [Part 4](04-getting-set-up.md); the point *here* is to decide, up
front, that secrets live outside the repo. ([Part 9](09-pitfalls-and-limits.md):
secrets are serious — restrict permissions and rotate anything ever exposed.)

---

## Part 3 — Knowing the tools you'll touch

You don't need to master these. You'll mostly copy-paste. But knowing *what each
one is for* turns the commands in [Part 4](04-getting-set-up.md) from magic
incantations into things you understand. There are four.

### The terminal

> **The terminal** is a text window where you type commands instead of clicking
> buttons. On a Mac it's the app literally called "Terminal."

It's the room everything else happens in. You'll see a blinking cursor; you type
a command, press Enter, and the computer does it and prints the result. By
convention in this series, a line starting with `$` is something you type (you
don't type the `$` itself). That's all you need to know to begin. If a wall of
text scrolls by, you don't have to read all of it — you're usually looking for one
word, like `OK` after the tests run.

### Python

> **Python** is the programming language the toolkit is written in.

You won't be writing much Python by hand — Claude does that. But it helps to know
that "running a Python program" usually looks like
`python3 -m something`, and that the `python3` (not `python`) is the
deliberate choice from [Part 2](#language-and-version-python-311-always-python3).
When the setup guide has you run `python3 --version` and confirm it prints
`3.11` or higher, you now know *why* that floor matters.

### Git: commits as an undo button

> **Git** is software that saves snapshots of your whole project so you can
> always go back. Think "track changes," but for an entire folder.

This is the tool worth understanding a little more deeply, because it is your
safety net for everything else.

A **commit** is a saved snapshot of your project at one moment, with a short
message describing it. Git keeps every commit in a history, which means **a
commit is an undo button you can press at any point in the future.** Made a mess
in the last hour? If you committed before you started, you can return to that
clean snapshot. This is what makes it safe to let an AI make sweeping changes:
worst case, you roll back to the last good commit and try again.

Two habits make Git pull its weight:

- **Commit small and often.** Lots of little checkpoints give you precise places
  to roll back to. One giant commit a day gives you one blunt undo.
- **Commit only good states.** A commit you can trust is one where the tests
  passed. Combined with the "never commit without my approval" rule from
  [Part 2](#commit-discipline-and-the-trust-boundary), this means your history is
  a series of human-reviewed, test-passing checkpoints — exactly the kind of
  history you want behind you when something goes wrong.

For now, just hold this mental model: **commits = restorable checkpoints, and
you approve each one.** The daily rhythm of using them is in
[Part 6](06-the-daily-loop.md).

### Claude Code: an agent that reads, writes, and runs

> **Claude Code** is the AI assistant you run *inside* the terminal. You talk to
> it in plain English, and it reads and writes files in your project and runs
> commands for you.

The word that matters is **agent**. Claude Code isn't just a chat box that hands
you snippets to paste. It can directly:

- **Read** any file in your project to understand what's there.
- **Write and edit** files — creating new scripts, fixing bugs, updating docs.
- **Run commands** — including running your tests and showing you the results.

A few capabilities to know about before you start, each expanded later in the
series:

- **Plan mode.** For anything non-trivial, Claude can research your code and
  propose an approach *before* changing a single file. Reviewing a plan is far
  cheaper than reviewing finished code. (This very playbook was produced via plan
  mode.) See [Part 6](06-the-daily-loop.md).
- **`/init`.** Run this and Claude scans your codebase and drafts a starter rules
  file (`CLAUDE.md`) for you — so you don't write your house rules from a blank
  page. See [Pillar 1 in Part 5](05-five-pillars.md).
- **Built-in code review.** Claude can review a change for problems, giving you a
  second set of eyes even if you're not an engineer.

The crucial framing for *this* part: because Claude Code can run commands and
write to live systems through the scripts it helps build, **it operates inside
the same blast radius you named in Part 1.** That's precisely why the standards
in Part 2 — especially "never commit without approval" and the Tier-3 guardrails
— exist. The agent is powerful; the structure is what keeps the power pointed in
a safe direction. [Part 1 of the series](01-what-is-vibe-coding.md) and
[Part 2](02-speed-inside-guardrails.md) make the broader case.

---

## The decision worksheet

Copy this, fill in the blanks for *your* project, and you've done the up-front
hour. The right-hand column shows the toolkit's answers as a worked example.

| Decision | Your answer | Toolkit's answer (example) |
|---|---|---|
| **Blast radius (one sentence)** | "The worst this tool can do is ___." | Create thousands of wrong tickets in a client's live Jira. |
| **Highest tier it reaches** | 1 / 2 / 3 | **Tier 3** (also does Tier 1 reads and Tier 2 local writes) |
| **Language & version** | | Python, CPython **3.11+**, always `python3` |
| **Dependencies** | | **Standard library only** (exceptions documented in advance) |
| **Naming & types** | | PEP 8, `snake_case`, dataclasses + type hints |
| **Test location & target** | | `tests/unit` + `tests/integration`, mirror `src/`, **≥90%** coverage |
| **Commit format** | | Conventional Commits, scoped to the `src/` folder touched |
| **Trust boundary** | | **AI never commits without explicit human approval** |
| **Where secrets live** | | Home-dir env file (`chmod 600`), never in the repo |
| **Guardrails required by tier** | | Dry-run + confirmation + retry + idempotency + scope guards |

If your blast-radius sentence lands at Tier 1, expect most of the bottom rows to
read "not needed yet." That's a correct answer — under-building a harmless tool
is fine; under-building a dangerous one is not.

---

## The pre-flight checklist

Run through this before you let Claude write the first line. Everything here is
*thinking and deciding*, not coding.

- [ ] I've written the one-sentence blast radius and said it out loud.
- [ ] I know the **highest tier** my tool reaches (1 / 2 / 3) and which
      capabilities sit at which tier.
- [ ] For any **Tier-3** capability, I've committed to building dry-run,
      confirmation, retry, idempotency, and scope guards *before* the first large
      run — and to letting the dangerous part come last.
- [ ] I've chosen my **language and version** (and how the interpreter is
      invoked).
- [ ] I've made a deliberate **dependency** decision and can state the trade-off
      I'm accepting.
- [ ] I've decided **naming/style**, **where tests live**, and a **coverage
      target**.
- [ ] I've set my **commit format** and locked the trust boundary: *no commit
      without my approval.*
- [ ] I've decided **where secrets live** — outside the project folder,
      permission-restricted — and I will never commit a credential.
- [ ] I understand my four tools well enough to follow along: terminal, Python,
      Git (commits = undo button), Claude Code (an agent that reads, writes, and
      runs).
- [ ] I'm ready to write these decisions into a rules file next — but I'll let
      Claude help draft it ([Part 5, Pillar 1](05-five-pillars.md)).

---

## Key takeaways

- **The most leveraged work happens before any code is generated.** One hour of
  deciding saves days of cleanup, because the cost of a bad decision only *moves*
  when you skip this — it lands later, in a worse place.
- **Name the blast radius first; everything scales off it.** Write "the worst
  this tool can do is ___," place each capability in a tier (read-only / writes
  local files / writes a live system), and match guardrail investment to the
  tier. Over-armoring a harmless tool is waste; under-armoring a live-system tool
  is the dangerous failure.
- **Choose standards deliberately, with reasons.** Language and version, the
  stdlib-only trade-off, naming and types, a concrete coverage target, conventional
  commits, and — above all — the trust boundary that *the AI never commits without
  your approval.* If you don't choose, the AI's drifting defaults choose for you.
- **Keep secrets out of the repo, by design.** Home-directory env files,
  `chmod 600`, SSL verification left on. A Tier-2 leak of a credential becomes a
  Tier-3 disaster.
- **Know your four tools as concepts, not commands.** The terminal is the room;
  Python is the language; Git commits are your undo button; Claude Code is an
  *agent* that reads, writes, and runs — which is exactly why it lives inside the
  blast radius you just named, and why the structure matters.

---

## Where to next

- **Previous:** [02 — Speed inside guardrails](02-speed-inside-guardrails.md)
- **Up:** [The Claude Code playbook (parent)](../claude-code-playbook.md)
- **Next:** [04 — Getting set up](04-getting-set-up.md)

Other parts in the series: [01 — What is vibe coding](01-what-is-vibe-coding.md)
· [05 — The five pillars](05-five-pillars.md)
· [06 — The daily loop](06-the-daily-loop.md)
· [07 — Deciding what not to build](07-deciding-what-not-to-build.md)
· [08 — A recipe for your own project](08-recipe-for-your-own-project.md)
· [09 — Pitfalls and honest limits](09-pitfalls-and-limits.md)
· [Series index](README.md)
