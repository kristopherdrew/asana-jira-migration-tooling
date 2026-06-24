# Vibe Coding, Safely — A Beginner's Playbook for AI-Assisted Projects

*How to use Claude Code to build a real, high-stakes tool without being a
career software engineer — and without breaking things.*

This is the long-form companion to the slide deck
([`claude-code-vibe-coding.md`](claude-code-vibe-coding.md)). The deck is for
presenting; this document is for **reading and doing**. It walks a newcomer from
zero to running a project safely, using a real example: a toolkit that migrates
an organization's data from **Asana** to **Jira**.

> **Want to go much deeper?** Each of the nine sections below is also expanded
> into its own in-depth, standalone document in the
> [`playbook/`](playbook/README.md) deep-dive series — any one of them could be
> its own presentation.

> **Who this is for.** You don't need to be a software engineer. If you're
> comfortable following instructions and willing to copy-paste a few commands,
> you can follow along. Where a technical word appears for the first time, it's
> explained in plain language, and there's a [Glossary](#glossary) at the end.
>
> **What you'll learn.** Not just *what* this project does, but a **repeatable
> method** you can apply to your own project. The Asana→Jira tool is the worked
> example throughout; the method is the real takeaway.

---

## Table of contents

1. [What "vibe coding" means here](#1-what-vibe-coding-means-here)
2. [The core idea: speed inside guardrails](#2-the-core-idea-speed-inside-guardrails)
3. [Before you write any code](#3-before-you-write-any-code)
4. [Getting set up (full walkthrough)](#4-getting-set-up-full-walkthrough)
5. [The five pillars of safe AI-assisted coding](#5-the-five-pillars-of-safe-ai-assisted-coding)
6. [The daily loop: how to actually work with Claude](#6-the-daily-loop-how-to-actually-work-with-claude)
7. [Deciding what NOT to build](#7-deciding-what-not-to-build)
8. [A step-by-step recipe for your own project](#8-a-step-by-step-recipe-for-your-own-project)
9. [Pitfalls and honest limits](#9-pitfalls-and-honest-limits)
10. [Glossary](#glossary)
11. [Quick command reference](#quick-command-reference)

---

## 1. What "vibe coding" means here

"Vibe coding" usually means describing what you want in plain English and
letting an AI write the code, without poring over every line yourself. It's
fast and accessible — but on its own it's risky, because you're shipping code
you didn't fully scrutinize.

This playbook is about a disciplined version of that. You still let Claude do
the heavy lifting, **but you put a structure around it** so that:

- mistakes get caught automatically (by tests),
- dangerous actions require confirmation (by guardrails),
- and the AI keeps to *your* standards (by a written rules file).

The result: you move at AI speed, but the output is something you can actually
trust and maintain.

### The example project, in one paragraph

The Asana→Jira toolkit reads everything out of a company's Asana account — users,
teams, projects, tasks, comments, attachments, custom fields — stores it locally,
and then **creates the matching items in Jira**. That last part is the scary
part: it *writes to a live system*. A careless run could create thousands of
wrong tickets. That high stakes is exactly why the safety method matters, and
why it's a good teaching example.

---

## 2. The core idea: speed inside guardrails

Imagine giving a powerful, fast assistant the keys to your company's project
tracker. You want the speed — but you'd also want seatbelts, a spell-checker, a
"are you sure?" prompt before anything permanent, and a written list of house
rules they always follow.

That's the whole philosophy:

> **AI speed *with* engineering discipline.**

The discipline comes from **five pillars**. The rest of this guide explains each
one, but here's the map:

| Pillar | Plain-English job | "Seatbelt" it provides |
|---|---|---|
| 1. A rules file (`CLAUDE.md`) | Tells Claude your standards, every time | Consistency; no re-explaining |
| 2. Reusable helpers | Solve each hard problem once, reuse it | Fewer bugs; faster new features |
| 3. Tests | Automatic checks that code still works | Catches mistakes before users do |
| 4. Runtime guardrails | "Are you sure?" + safe retries | Protects the live system |
| 5. Interactive CLI | A guided menu instead of cryptic commands | Anyone can run it safely |

You don't need all five on day one. But knowing they exist — and adding them as
you go — is what separates a risky experiment from a dependable tool.

---

## 3. Before you write any code

Three things to settle first. They take an hour and save you days.

### 3.1 Be honest about the stakes

Ask: *what's the worst thing this tool could do?* For our example, it can create
real tickets in a live Jira. That answer tells you how many guardrails you need.
A tool that only reads data and writes a local file needs few; a tool that
changes a live system needs many. **Match the safety effort to the blast
radius.**

### 3.2 Decide your standards up front

What language? What rules? For our example the answers were deliberately strict:

- **Python 3.11+**, always invoked as `python3` (never the ambiguous `python`).
- **Standard library only** — no third-party packages. This is unusual, but it
  means the tool runs anywhere with nothing to install, and there are fewer
  moving parts to break or to keep secure.
- Tests live next to the code they test; aim for **90%+ coverage** (a measure of
  how much of the code the tests exercise).
- Every change uses a clear, conventional commit message.
- **Never save a permanent checkpoint (a "commit") without the human approving
  it.**

You'll write these into the rules file in Pillar 1. The point now is simply:
*decide them deliberately*, don't let them happen by accident.

### 3.3 Know the tools you'll touch

You'll use four things. Don't worry about mastering them — you'll mostly
copy-paste:

- **The terminal** — a text window where you type commands. On a Mac it's the
  "Terminal" app.
- **Python** — the programming language the tool is written in.
- **Git** — a system that saves snapshots ("commits") of your project so you can
  always go back. Think "track changes," but for an entire folder.
- **Claude Code** — the AI assistant you run *inside* the terminal. You talk to
  it in plain English and it reads/writes files and runs commands for you.

---

## 4. Getting set up (full walkthrough)

This section gets you from a fresh machine to running the example tool. If
you're an engineer, skim it — the methodology resumes in
[Pillar 1](#5-the-five-pillars-of-safe-ai-assisted-coding).

> **Convention:** lines starting with `$` are commands you type into the
> terminal (don't type the `$` itself). Everything is shown for macOS, which is
> what this project targets.

### 4.1 Open the terminal

On a Mac: press `Cmd+Space`, type "Terminal," press Enter. You'll get a window
with a blinking cursor. That's where the commands below go.

### 4.2 Make sure you have Python 3.11+

```bash
$ python3 --version
```

If it prints `Python 3.11.x` or higher, you're set. If not, install it from
[python.org](https://www.python.org/downloads/) or via Homebrew
(`brew install python`).

### 4.3 Install Claude Code

Claude Code is Anthropic's official command-line assistant. Install it and sign
in once:

```bash
$ npm install -g @anthropic-ai/claude-code   # requires Node.js
$ claude                                       # starts it; follow the login prompt
```

(If you don't have `npm`/Node.js, install Node first from
[nodejs.org](https://nodejs.org/).) Once you're logged in, you can launch Claude
from inside any project folder by typing `claude`.

### 4.4 Get the project

Get a copy of the project onto your machine and move into its folder:

```bash
$ git clone <the-project-repository-url>
$ cd asana_jira_migration
```

(`git clone` downloads the project; `cd` means "change directory" — i.e., step
into the folder.)

### 4.5 Create an isolated workspace (virtual environment)

A **virtual environment** is a private sandbox for this project so it never
interferes with the rest of your system. Create and activate it:

```bash
$ python3 -m venv .venv
$ source .venv/bin/activate
```

After this your prompt shows `(.venv)`. To leave the sandbox later, type
`deactivate`. (This particular project uses only the standard library, so there's
nothing extra to install — but the sandbox is still good hygiene.)

### 4.6 Provide your credentials safely

The tool needs API tokens to talk to Asana and Jira. **Never put secrets in the
project folder** (they could be committed and leaked). Instead, store them in a
protected file in your home directory.

Create the credentials file (replace the placeholder values):

```bash
$ cat > ~/.asana_jira_env_client_a <<'EOF'
export ASANA_TOKEN="your_asana_token"
export ASANA_WORKSPACE_GID="your_workspace_gid"
export SKIP_SSL_VERIFICATION="false"
export ATLASSIAN_USERNAME="you@example.com"
export ATLASSIAN_TOKEN="your_jira_api_token"
EOF
$ chmod 600 ~/.asana_jira_env_client_a   # makes the file readable only by you
```

Then point this project copy at that file:

```bash
$ echo "~/.asana_jira_env_client_a" > .asana_jira_env_path
```

**Why this design?** Some teams migrate several clients at once. By keeping one
credentials file per client in your home directory and a small pointer file
(`.asana_jira_env_path`) in each project copy, you can have multiple copies of
the project, each safely aimed at a different client's data. The tool looks for
credentials in this order: your terminal's environment → the file named by
`.asana_jira_env_path` → a default `~/.asana_jira_env`.

> **The easy way:** you don't have to create these files by hand. The project's
> interactive menu (next section) has a setup flow — on first launch, if it can't
> find credentials, it *asks you* for the token, workspace ID, and so on, then
> writes both files for you.

### 4.7 Confirm everything works by running the tests

Before doing anything real, run the project's automated checks:

```bash
$ python3 -m unittest discover tests
```

If it ends with `OK`, the code is healthy on your machine. This is also the
single most important habit in the whole playbook — you'll run this after every
change. (More in [Pillar 3](#pillar-3--tests-the-automatic-safety-net).)

### 4.8 Run the tool through its guided menu

The friendliest way to use the tool is its menu:

```bash
$ python3 -m scripts.interactive_cli --log-level INFO
```

This presents numbered categories (Initialization, Tasks, Atlassian/Jira,
Reports, Credentials) and walks you through them — no need to memorize commands.
We'll come back to *why* this menu matters in
[Pillar 5](#pillar-5--an-interactive-cli-so-anyone-can-run-it).

You are now fully set up. The rest of the guide is the method.

---

## 5. The five pillars of safe AI-assisted coding

This is the heart of the playbook. Each pillar is explained in plain language,
then shown in the real project so you can see what "good" looks like.

### Pillar 1 — A rules file (`CLAUDE.md`) is your contract with the AI

**The problem it solves.** If you tell Claude "use Python 3, no outside
libraries, write tests" in a chat message, it'll comply *that time* — and forget
by next week. Repeating yourself is tedious and error-prone.

**The fix.** Put your standards in a file named `CLAUDE.md` at the top of your
project. Claude reads it **automatically, every single time** it works in that
project. It becomes a binding contract instead of a one-off request.

**In the example project**, `CLAUDE.md` encodes rules like:

- Always use `python3`, never bare `python`; require Python 3.11+.
- **Standard library only** — no third-party packages without prior approval.
- Put tests next to the code they cover; target 90%+ coverage.
- Use clear, conventional commit messages.
- **Never create a commit unless the human explicitly approves it.**

You can *see* the rules holding. The contract says "standard library only," and
the actual network code obeys it — it uses Python's built-in `urllib` instead of
the popular third-party `requests` package:

```python
# src/lib/http_client.py — note: only built-in modules are imported
import json, ssl, time
from urllib import error, parse, request
```

#### You don't have to write `CLAUDE.md` by hand — Claude will help

This is the part that's easy to miss: **`CLAUDE.md` is just a normal text file,
and Claude can create, maintain, and update it for you.** You rarely need to
edit it manually.

**Creating it.** From inside your project, run the built-in command:

```
/init
```

Claude scans your codebase — its structure, how it's built, how tests are run,
the conventions already in use — and writes a starter `CLAUDE.md` for you. You
then read it and tweak anything that's wrong. That's how a useful rules file
exists on day one without you typing it from scratch.

You can also just *ask*, in plain English:

> "Create a `CLAUDE.md` that documents our coding standards: Python 3.11+, always
> `python3`, standard library only, tests next to the code, and never commit
> without my approval."

Claude writes the file like it writes any other.

**Maintaining and updating it.** The rules file should grow as your project
teaches you what you care about. The reliable way to update it is simply to ask:

> "Update `CLAUDE.md` to add a rule that every new command must support a
> `--dry-run` flag."

> "We just added a new export script — add it to the CLI catalog section of
> `CLAUDE.md` with a one-line description of what it does."

Two habits make this powerful:

- **When you correct Claude, ask it to record the correction.** If you find
  yourself saying "no, we always do it *this* way," follow up with "add that to
  `CLAUDE.md` so it sticks." Next session, Claude already knows.
- **Treat the rules file as living documentation.** This project, for instance,
  keeps a running catalog of every command — its inputs, outputs, and options —
  right in the rules file, so the description and the tool never drift apart.

**A couple of handy extras** (nice to know, not required for beginners):

- Type **`/memory`** to see and edit all the instruction files Claude currently
  has loaded.
- A file at **`~/.claude/CLAUDE.md`** in your home folder holds *personal* rules
  that apply to every project on your machine (and is never shared with your
  team) — good for preferences that are about *you*, not the project.
- Inside a `CLAUDE.md` you can pull in another file with `@path/to/file.md`, so
  you can keep long references in their own files and just point to them.

> **Takeaway:** Write your rules down once, in `CLAUDE.md` — but let Claude do
> the writing. Start it with `/init`, then grow it by *asking* Claude to record
> each new convention as you discover it. From then on the AI inherits your
> standards on every turn, and you stop being the rule police.

### Pillar 2 — Reusable helpers mean you decide things once

**The problem it solves.** Left alone, an AI tends to re-solve the same problem
slightly differently in every file — one place handles errors one way, another
place a different way. That inconsistency is where bugs hide.

**The fix.** The first time you solve a hard, shared problem — "how do we talk to
Asana?", "how do we handle being rate-limited?", "how do we save to the
database?" — put that solution in **one** small, well-named file (a "helper" or
"module"). Every new feature then *reuses* that helper instead of reinventing
it.

**In the example project**, a folder called `src/lib/` holds **13 helper
modules**, and they're reused **181 times** across the project's **46 scripts**.
A few of them:

| Helper | What it handles (decided once) |
|---|---|
| `config.py` | Loading your credentials safely, with fallbacks |
| `http_client.py` | Talking to Asana, including automatic retry when rate-limited |
| `jira_client.py` | Talking to Jira, with the same retry safety |
| `database.py` | Saving and loading data from the local database |
| `team_picker.py` | The interactive "choose a team" menu |
| `adf.py` | Converting plain text into Jira's rich-text format |

Because these exist, a big script like the one that creates Jira issues isn't
thousands of lines of new, risky code — it's mostly *wiring together* helpers
that are already tested and trusted. New features become small, low-risk
compositions.

> **Takeaway:** When you notice Claude writing similar code a second time, ask it
> to "extract this into a reusable helper in a shared module, and use it in both
> places." You're not just tidying up — you're making every future decision
> faster and every future feature safer.

### Pillar 3 — Tests: the automatic safety net

**The problem it solves.** When you didn't write the code line-by-line, how do
you *know* it works — and keeps working after the next change? You can't re-check
everything by hand.

**The fix.** **Tests** are small programs that automatically check your real
code behaves correctly. You run them with one command. If something breaks —
even something you weren't thinking about — a test fails and tells you exactly
where. This is what lets you accept fast AI-written code with confidence.

**In the example project** there are **48 unit tests** (checking individual
pieces) plus **2 integration tests** (checking whole commands end-to-end). They
deliberately cover the tricky situations where bugs love to hide:

- **Rate limiting** — what happens when Asana says "slow down"? A test confirms
  the retry logic reads the "wait this long" header correctly.
- **Pagination** — when data comes back in multiple pages, a test confirms the
  tool fetches all of them and doesn't over-fetch.
- **Delete/replace logic** — a test confirms that replacing a task's comments
  correctly marks the old ones removed, instead of silently duplicating or
  losing data.
- **Format conversions** — a test confirms each Jira field type is formatted the
  right way before it's sent.

The golden rule: **every time a bug is found and fixed, add a test for it.** That
turns a one-time fix into a permanent guarantee the bug never returns.

> **Takeaway:** Ask Claude to write a test alongside every feature and fix. Then
> run `python3 -m unittest discover tests` after *every* change. If it says
> `OK`, you're safe to continue. This one habit is the backbone of safe vibe
> coding.

### Pillar 4 — Runtime guardrails for anything that touches a live system

**The problem it solves.** Tests check the code in a sandbox. But the real risk
is the live run: the moment the tool actually creates tickets in the company's
Jira. You need protections *at that moment*.

**The fix.** Build in "look before you leap" and "fail gracefully" behaviors.
The example project has a rich set, all worth copying:

- **`--dry-run`** — a mode that shows you *exactly* what it would send to Jira,
  but changes nothing. You inspect it first, then run for real.
- **Confirmation prompts (`--yes` to skip)** — before creating issues it asks,
  *"About to create N issues. Continue?"* Nothing permanent happens until you
  say yes.
- **Automatic retry when rate-limited** — if Jira says "too many requests," the
  tool waits and retries (up to several rounds) instead of crashing or losing
  work.
- **Graceful fallback** — if a particular person can't be assigned a ticket, it
  drops just that assignment and keeps going, rather than failing the whole
  batch.
- **Two-pass "clear then set"** — when repairing a field, it clears the old value
  on everything first, then sets the new value, so no stale half-updated data
  survives if something is interrupted.
- **Safe to run twice (idempotency)** — it remembers what it already created, so
  re-running after an interruption *heals* the migration instead of creating
  duplicates.
- **Scope guards** — it refuses contradictory instructions (e.g., asking for one
  project *and* all teams at once) so you can't accidentally target the entire
  company.

> **Takeaway:** For anything that changes a live system, insist on a dry-run, a
> confirmation prompt, and re-runnability *before* you do a large run. Ask
> Claude: "add a `--dry-run` mode and a confirmation prompt before this writes
> anything." Design for failure and reversibility first; scale second.

### Pillar 5 — An interactive CLI so anyone can run it

**The problem it solves.** A powerful tool is useless — and dangerous — if the
only way to run it is to type twelve perfectly-correct options. People will get
it wrong, and "wrong" here means bad data in a live system.

**The fix.** Wrap the tool in a friendly, guided experience. The example
project's menu (`scripts/interactive_cli.py`) does exactly this: it presents
numbered categories, walks you through each step, and even runs a credentials
setup flow on first launch if it can't find your tokens. Individual steps offer
searchable pickers (e.g., "choose a team by typing part of its name") and remember
your field mappings so the second run is easier than the first.

Crucially, the guided experience *also enforces the guardrails from Pillar 4* —
the confirmation prompts and dry-runs are right there in the flow.

> **Takeaway:** Accessibility is a safety feature. The more your tool guides the
> operator and refuses obviously-wrong input, the fewer costly mistakes. Ask
> Claude to "add an interactive menu that walks the user through these steps and
> confirms before anything permanent."

---

## 6. The daily loop: how to actually work with Claude

Pillars are the structure. Here's the rhythm of using it day to day.

1. **Describe the goal in plain English.** "I want a command that exports every
   user and their activity counts to a spreadsheet." Be specific about inputs and
   outputs; you don't need to know *how*.
2. **Ask for a plan first on anything non-trivial.** Claude Code has a "plan
   mode" where it researches your code and proposes an approach *before* changing
   anything. Review the plan; it's far cheaper to fix a plan than finished code.
   This very playbook was produced via plan mode.
3. **Let it write the code — and the test.** Always ask for a test alongside the
   feature: "and add a test that covers the empty-input case."
4. **Run the tests.** `python3 -m unittest discover tests`. Green means go.
5. **Try it for real in dry-run.** See what it *would* do before letting it do
   anything permanent.
6. **Review the change.** You don't have to understand every line, but skim what
   changed and ask Claude to explain anything that looks surprising. Claude Code
   also has a built-in code-review helper.
7. **Commit — only when you approve.** A commit is a saved checkpoint you can
   return to. The project's rule (in `CLAUDE.md`) is that Claude never commits
   without your explicit yes. Commit small and often; it's your undo button.
8. **Repeat.**

**A few prompting tips for beginners:**

- Ask for explanations: "Explain what this script does as if I'm not a
  programmer."
- Ask for the safe version: "Add a confirmation prompt and a dry-run mode."
- Ask it to find its own gaps: "What edge cases are we not handling? Write tests
  for them."
- When unsure, ask for options: "Give me two approaches with the trade-offs."

---

## 7. Deciding what NOT to build

A surprising amount of good engineering is *restraint*. Every feature, file, and
outside library you add is something to maintain, secure, and potentially break.
The example project shows this discipline:

- **It deletes prototypes when they've served their purpose.** A scratch helper
  called the "note shuttle" was removed once it was no longer needed, rather than
  left to rot.
- **It said no to convenient libraries.** The "standard library only" rule meant
  *declining* popular third-party packages that would've saved a few lines — in
  exchange for a tool that installs nowhere and has a tiny attack surface.
- **It earned complexity gradually.** It didn't start as a fancy live-Jira
  creator. It began by exporting to spreadsheets, then added direct creation,
  then field mapping, then retry/fallback resilience, then the two-pass repair —
  each step added only when reality demanded it.

> **Takeaway:** Default to *less*. Add a feature or dependency only when the need
> is real and present. Delete experiments once they've taught you what you
> needed. "What we chose not to build" is a feature, not a gap.

A close cousin of this is **documentation as a feedback loop**. The project keeps
its rules in `CLAUDE.md` and its command reference split across focused files in
`docs/`. These aren't just for humans — *Claude re-reads them* each session, so
good docs literally make the AI's next contribution better. Keeping docs current
is an investment that compounds.

---

## 8. A step-by-step recipe for your own project

Putting it all together, here's how to start a similar project from scratch.

1. **Name the blast radius.** Write one sentence: "The worst this tool can do
   is ___." Let that set how many guardrails you need.
2. **Create the project and a `CLAUDE.md`.** Even five lines: your language,
   your "no surprises" rules, and "never commit without my approval." Launch
   `claude` in the folder so it picks the file up.
3. **Set up the basics** (Section 4): terminal, Python, a virtual environment,
   git, and a *safe* place for any secrets — never in the project folder.
4. **Build the smallest useful thing first**, read-only if possible. For a
   migration, that's "export the data to a spreadsheet." No live writes yet.
5. **Add tests as you go**, and run them after every change. Make `OK` your
   green light.
6. **Factor out helpers** the moment you reuse logic. Tell Claude to extract and
   share it.
7. **Only then add the risky part** (writing to a live system) — and add its
   guardrails *in the same step*: dry-run, confirmation, retry, re-runnability.
8. **Wrap it in a guided menu** so non-experts can run it safely.
9. **Prune** prototypes and unused options. Keep `CLAUDE.md` and your docs
   current.
10. **Commit small and often**, always with your approval, so you can always
    step back.

Follow that order — read-only before write, tests before scale, guardrails
before the big run — and you get the speed of vibe coding with the safety of
careful engineering.

---

## 9. Pitfalls and honest limits

No method is free. Be aware of these:

- **Trade-offs are real.** "Standard library only" kept this project portable and
  secure, but it meant writing more code by hand in spots where a library would
  have been shorter. Choose your constraints deliberately; they cut both ways.
- **Coverage targets need discipline.** A "90% coverage" goal only helps if you
  actually uphold it. It's easy to let it slip when you're moving fast.
- **The AI is fast, not infallible.** It can write confident, wrong code. The
  tests, dry-runs, and your review are what catch this — they're not optional
  decoration.
- **You're still accountable.** The human approves every commit and every live
  run. Treat the "are you sure?" prompts as real decisions, not speed bumps.
- **Secrets are serious.** Keep tokens out of the project folder, restrict file
  permissions, and rotate any credential that's ever exposed.

None of these are reasons not to vibe code. They're the reason to vibe code
*inside guardrails* — which is the entire point.

---

## Glossary

- **API** — a way for one program to talk to another over the internet (e.g.,
  how this tool talks to Asana and Jira).
- **API token** — a secret password-like string that proves you're allowed to use
  an API. Keep it private.
- **CLI (command-line interface)** — a tool you run by typing commands in a
  terminal, rather than clicking buttons.
- **Commit** — a saved snapshot of your project at a moment in time. Git keeps a
  history of commits so you can always go back.
- **Coverage** — the percentage of your code that your tests actually exercise.
  Higher is generally safer.
- **Dry-run** — a mode that shows what a command *would* do without actually
  doing it.
- **Git** — software that tracks every change to your project and lets you undo,
  branch, and collaborate.
- **Helper / module** — a small file of reusable code that solves one job, used
  by many other parts of the project.
- **Idempotent** — safe to run more than once; running again doesn't create
  duplicates or damage.
- **Rate limit** — a cap on how many requests a service accepts per minute;
  exceeding it gets you temporarily blocked.
- **Standard library** — the code that comes built into Python itself, with
  nothing extra to install.
- **Terminal** — the text window where you type commands.
- **Test** — a small automatic check that confirms your real code behaves
  correctly.
- **Virtual environment (venv)** — a private sandbox for one project's setup so
  it doesn't interfere with the rest of your system.

---

## Quick command reference

```bash
# --- one-time setup ---
python3 --version                                  # confirm Python 3.11+
npm install -g @anthropic-ai/claude-code           # install Claude Code
git clone <repo-url> && cd asana_jira_migration    # get the project
python3 -m venv .venv && source .venv/bin/activate # isolated sandbox

# --- credentials (or use the interactive menu's setup flow instead) ---
#   create ~/.asana_jira_env_client_a with your tokens, then:
echo "~/.asana_jira_env_client_a" > .asana_jira_env_path

# --- everyday use ---
python3 -m unittest discover tests                 # run the safety checks (do this often!)
python3 -m scripts.interactive_cli --log-level INFO# the guided menu
python3 -m scripts.<name> --help                   # see options for any single command
claude                                             # start the AI assistant in this folder

# --- when you're done with a client ---
rm -rf exports && rm -f data/asana.sqlite && rm -f .asana_jira_env_path
```

---

*This guide is intentionally verbose so it can be trimmed to taste. The companion
slide deck distills it to twelve slides:
[`claude-code-vibe-coding.md`](claude-code-vibe-coding.md).*
