# The five pillars of safe AI-assisted coding

*The load-bearing part of the whole series: five layers of defense — a rules
file, reusable helpers, tests, runtime guardrails, and an interactive CLI — that
together let you move at AI speed without breaking a live system. Each pillar is
a presentation in its own right.*

> Part 5 of 9 in the Vibe Coding, Safely series

---

If you read only one part of this series, read this one. Parts 1–4 set the
stage: what "vibe coding" is ([Part 1](./01-what-is-vibe-coding.md)), why speed
needs guardrails ([Part 2](./02-speed-inside-guardrails.md)), the thinking you
do *before* writing code ([Part 3](./03-before-you-write-any-code.md)), and how
to get set up on a fresh machine ([Part 4](./04-getting-set-up.md)). This part
is the method itself.

The running example throughout is the same one used across the series: the
**Asana → Jira migration toolkit**. It reads an entire company's Asana account —
users, teams, projects, tasks, comments, attachments, custom fields — stores it
locally in SQLite, and then **creates the matching work items in a live Jira**.
That last clause is what makes it a good teaching example: a careless run could
create thousands of wrong tickets in a system real people depend on. High stakes
are exactly when method matters.

---

## Table of contents

- [Why five? Defense-in-depth, not a checklist](#why-five-defense-in-depth-not-a-checklist)
- [The five pillars at a glance](#the-five-pillars-at-a-glance)
- [Pillar 1 — A rules file (`CLAUDE.md`) as your contract](#pillar-1--a-rules-file-claudemd-as-your-contract)
  - [The plain-language problem and fix](#the-plain-language-problem-and-fix)
  - [In the real project](#in-the-real-project)
  - [Claude writes and maintains `CLAUDE.md` for you](#claude-writes-and-maintains-claudemd-for-you)
  - [Going deeper (for engineers)](#going-deeper-for-engineers--pillar-1)
  - [How to ask Claude for this](#how-to-ask-claude-for-this--pillar-1)
  - [Pillar 1 takeaways](#pillar-1-takeaways)
- [Pillar 2 — Reusable helpers: decide once](#pillar-2--reusable-helpers-decide-once)
  - [The plain-language problem and fix](#the-plain-language-problem-and-fix-1)
  - [In the real project](#in-the-real-project-1)
  - [Going deeper (for engineers)](#going-deeper-for-engineers--pillar-2)
  - [How to ask Claude for this](#how-to-ask-claude-for-this--pillar-2)
  - [Pillar 2 takeaways](#pillar-2-takeaways)
- [Pillar 3 — Tests: the automatic safety net](#pillar-3--tests-the-automatic-safety-net)
  - [The plain-language problem and fix](#the-plain-language-problem-and-fix-2)
  - [In the real project](#in-the-real-project-2)
  - [Going deeper (for engineers)](#going-deeper-for-engineers--pillar-3)
  - [How to ask Claude for this](#how-to-ask-claude-for-this--pillar-3)
  - [Pillar 3 takeaways](#pillar-3-takeaways)
- [Pillar 4 — Runtime guardrails for anything touching a live system](#pillar-4--runtime-guardrails-for-anything-touching-a-live-system)
  - [The plain-language problem and fix](#the-plain-language-problem-and-fix-3)
  - [In the real project](#in-the-real-project-3)
  - [Going deeper (for engineers)](#going-deeper-for-engineers--pillar-4)
  - [How to ask Claude for this](#how-to-ask-claude-for-this--pillar-4)
  - [Pillar 4 takeaways](#pillar-4-takeaways)
- [Pillar 5 — An interactive CLI so anyone can run it safely](#pillar-5--an-interactive-cli-so-anyone-can-run-it-safely)
  - [The plain-language problem and fix](#the-plain-language-problem-and-fix-4)
  - [In the real project](#in-the-real-project-4)
  - [Going deeper (for engineers)](#going-deeper-for-engineers--pillar-5)
  - [How to ask Claude for this](#how-to-ask-claude-for-this--pillar-5)
  - [Pillar 5 takeaways](#pillar-5-takeaways)
- [How the pillars reinforce each other](#how-the-pillars-reinforce-each-other)
- [Key takeaways](#key-takeaways)
- [Where to next](#where-to-next)

---

## Why five? Defense-in-depth, not a checklist

The instinct, when you hear "five pillars," is to treat them as a checklist:
build pillar 1, tick the box, build pillar 2, tick the box. That misses the
point. The pillars are **layers of defense**, each catching a different class of
mistake at a different moment. A mistake the rules file fails to prevent, a test
should catch. A mistake the test misses, a dry-run should surface. A mistake the
dry-run lets through, a confirmation prompt gives you one last chance to stop. A
mistake an expert wouldn't make but a newcomer would, the interactive menu
prevents by not offering the dangerous option in the first place.

Think of it the way aviation thinks about safety: no single device is trusted to
be perfect, so you stack independent ones whose failure modes don't overlap. The
same applies here. Map each pillar to *when* it catches a mistake:

| When | Layer | Catches |
|---|---|---|
| Before code is written | Pillar 1 — rules file | The AI drifting from your standards |
| As code is written | Pillar 2 — reusable helpers | The same problem solved five inconsistent ways |
| After code is written, before you run it | Pillar 3 — tests | Logic that's subtly wrong or regressed |
| The instant you run against the live system | Pillar 4 — runtime guardrails | A correct tool aimed at the wrong target, or a transient failure |
| Whenever a non-expert operates it | Pillar 5 — interactive CLI | Wrong flags, wrong scope, skipped confirmations |

You do not need all five on day one. Part 8's
[recipe](./08-recipe-for-your-own-project.md) shows the order to add them in
(read-only first, write last; tests before scale; guardrails before the big
run). But knowing all five exist — and that they cover *different* failures — is
what turns a risky experiment into a tool you'd trust with a client's data.

---

## The five pillars at a glance

| Pillar | Plain-English job | The seatbelt it provides | In the example |
|---|---|---|---|
| 1. Rules file (`CLAUDE.md`) | Tells Claude your standards, every session | Consistency; you stop being the rule police | `AGENTS.md` (with a `CLAUDE.md` symlink) encoding stdlib-only, `python3`, tests, never-commit-without-approval |
| 2. Reusable helpers | Solve each hard problem once, reuse it | Fewer bugs; new features are small | `src/lib/` — 13 modules reused 181 times across 46 scripts |
| 3. Tests | Automatic checks that the code still works | Catches mistakes before users do | 48 unit + 2 integration tests, run with one command |
| 4. Runtime guardrails | "Are you sure?" + safe retries + re-runnable | Protects the live system at the moment of the write | `--dry-run`, confirmation prompts, 429 requeue, assignee fallback, idempotent mappings, scope guards |
| 5. Interactive CLI | A guided menu instead of cryptic flags | Anyone can run it safely | `scripts/interactive_cli.py` — categorized menu, searchable pickers, first-launch credential setup |

The rest of this part takes each row and goes deep.

---

## Pillar 1 — A rules file (`CLAUDE.md`) as your contract

### The plain-language problem and fix

**The problem.** If you tell Claude "use Python 3, no outside libraries, write
tests" in a chat message, it'll comply *that time* — and forget by next session.
You end up re-explaining your standards constantly, and the moment you forget to,
the AI quietly reverts to its own defaults. That inconsistency is where drift
(and bugs) creep in.

**The fix.** Put your standards in a file named `CLAUDE.md` at the top of your
project. Claude reads it **automatically at the start of every session** in that
project — you don't have to paste it, attach it, or remind it. It becomes a
*binding contract* rather than a one-off request. From then on, every answer the
AI gives is already shaped by your rules.

A subtle but important detail: this file is just plain Markdown. There's no
special syntax to learn, no schema to satisfy. If you can write a bulleted list
of "here's how we do things," you can write a `CLAUDE.md`.

### In the real project

In this repository the actual file on disk is `AGENTS.md`, and `CLAUDE.md` is a
**symlink** that points to it:

```
$ ls -la AGENTS.md CLAUDE.md
-rw-r--r--  ... AGENTS.md
lrwxr-xr-x  ... CLAUDE.md -> AGENTS.md
```

That's a small piece of cross-tool hygiene: `AGENTS.md` is a convention several
AI coding tools recognize, and the `CLAUDE.md` symlink means Claude Code finds
the same rules without maintaining two copies that can drift apart. One source of
truth, two names.

What's *in* it is the contract. The real file encodes rules like:

- Always use **`python3`**, never the ambiguous bare `python`; require **CPython
  3.11+**.
- **Standard library only** — no third-party packages (no `requests`, no
  `pytest`) without prior, documented approval.
- Tests **mirror the `src/` layout two levels deep** in `tests/`, targeting
  **≥90% statement coverage**, with async and pagination edge cases covered.
- **PEP 8**, `snake_case`, dataclasses, and type hints.
- **Conventional Commits**, scoped to the `src/` folder you touched (e.g.
  `feat(tasks): …`).
- **Never create a commit unless the human explicitly approves it.**
- **Every new CLI script must be documented** both in the matching `docs/` file
  *and* in the script catalog kept inside the rules file itself.

You can *watch the contract hold* in the code. The rule says "standard library
only," and the network layer obeys it — `src/lib/http_client.py` talks to Asana
using Python's built-in `urllib` instead of the hugely popular third-party
`requests` package:

```python
# src/lib/http_client.py — only built-in modules are imported
import json
import ssl
import time
from dataclasses import dataclass
from typing import Dict, Generator, Iterable, Optional
from urllib import error, parse, request
```

The Jira client (`src/lib/jira_client.py`) follows the same discipline, even
building its Basic-auth header by hand with the stdlib `base64` module rather than
reaching for a convenience library. The rule in the file is not aspirational; it
shows up in every helper.

### Claude writes and maintains `CLAUDE.md` for you

This is the part newcomers most often miss: **you rarely write or edit
`CLAUDE.md` by hand.** Because it's an ordinary text file, Claude can create,
grow, and update it for you.

**Creating it with `/init`.** From inside your project, run the built-in
command:

```
/init
```

Claude scans your codebase — its folder structure, how it's built, how tests are
run, the naming conventions already in use — and **drafts a starter `CLAUDE.md`**
for you. You then read it and correct anything that's wrong. That's how a useful
rules file exists on day one without you typing it from scratch. (`/init` is also
how a brand-new repo gets its first version; on this project the result was the
catalog-heavy `AGENTS.md` you can read today.)

**Creating it by asking.** You can also just describe what you want in plain
English:

> "Create a `CLAUDE.md` that documents our coding standards: Python 3.11+, always
> `python3`, standard library only, tests next to the code, and never commit
> without my approval."

Claude writes the file like it writes any other.

**Maintaining it by asking.** The rules file should *grow as your project teaches
you what you care about.* The reliable way to update it is simply to ask:

> "Update `CLAUDE.md` to add a rule that every new command must support a
> `--dry-run` flag."

> "We just added a new export script — add it to the CLI catalog section of
> `CLAUDE.md` with a one-line description of what it does."

This project does exactly that: it keeps a running **catalog of every CLI
script** — purpose, inputs, outputs, every flag — right inside the rules file, so
the description and the tool can't drift apart. (You can see how detailed that
catalog is by reading `AGENTS.md`; entries run to full paragraphs describing each
script's flags and behaviors.)

**Two habits make this powerful:**

- **When you correct Claude, ask it to record the correction.** If you catch
  yourself saying "no, we always do it *this* way," follow up with "add that to
  `CLAUDE.md` so it sticks." Next session, Claude already knows — you've turned a
  one-time correction into a permanent rule.
- **Treat the rules file as living documentation,** not a one-time setup. Every
  convention you discover is a candidate for the file.

**A few handy extras** (nice to know, not required for beginners):

- Type **`/memory`** to see and edit every instruction file Claude currently has
  loaded — useful when you're not sure *why* it's behaving a certain way.
- A file at **`~/.claude/CLAUDE.md`** in your home folder holds *personal*,
  machine-wide rules that apply to every project you work on and are never shared
  with your team — good for preferences about *you*, not the project (e.g. "always
  run the test suite after a change without asking which command").
- Inside any `CLAUDE.md` you can pull in another file with **`@path/to/file.md`**,
  so long references can live in their own files and the rules file just points to
  them — keeping the main file readable.

> **A note on the `#` shortcut.** Older guidance suggested typing a line starting
> with `#` to quick-add a memory. That shortcut still exists, but it has been
> unreliable in recent versions — sometimes the line doesn't land where you
> expect. The dependable workflow is the one above: start with **`/init`**, then
> **ask Claude in plain English** to record each new convention. Treat `#` as a
> bonus, not the primary path.

### Going deeper (for engineers) — Pillar 1

A few things worth understanding under the hood:

- **Load order and scope.** Claude loads project rules from the working directory
  (and parent directories), and personal rules from `~/.claude/CLAUDE.md`.
  Project rules are the shared contract checked into the repo; personal rules are
  yours alone. Keeping them separate matters: machine-specific preferences
  shouldn't pollute a teammate's checkout.
- **The symlink trick is intentional, not incidental.** `AGENTS.md` is the
  canonical file (it's what's tracked and edited), and `CLAUDE.md -> AGENTS.md`
  is a symlink. If you adopt this pattern, make the *real file* the one your
  tooling and humans expect to edit, and let the symlink be the alias. Editing
  through either name edits the same bytes, so there's no divergence risk.
- **The catalog-in-the-rules-file pattern is a forcing function.** Because the
  contract says "document every new script in the catalog," adding a script
  *forces* a documentation update in the same change. That's how the file stays
  trustworthy enough that Claude can rely on it the next session — the docs are a
  feedback loop the AI itself reads (see [Part 7](./07-deciding-what-not-to-build.md)
  on documentation as a compounding investment).
- **Rules that constrain the AI's *defaults* are the highest-value ones.** "No
  third-party packages" is powerful precisely because it contradicts the model's
  instinct to reach for `requests` or `pytest`. The rules you most need to write
  down are the ones where your project differs from the mainstream.

### How to ask Claude for this — Pillar 1

Copy-pasteable prompts:

- "Run `/init` and draft a `CLAUDE.md` for this repo, then summarize what rules
  you inferred so I can correct them."
- "Add a rule to `CLAUDE.md`: standard library only, no third-party packages
  without my written approval. Then check the codebase for any violations."
- "I just told you we always use `python3`, never bare `python`. Record that in
  `CLAUDE.md` so it persists."
- "We added `scripts/export_users.py`. Add a catalog entry to `CLAUDE.md`
  describing its inputs, outputs, and flags."

### Pillar 1 takeaways

- Write your standards down **once**, in `CLAUDE.md` — but let Claude do the
  writing. Start with `/init`, then grow it by *asking*.
- Capture corrections the moment you make them, so they persist across sessions.
- The rules that matter most are the ones where you *differ* from the AI's
  defaults.
- Keep the file living: a catalog that's part of the contract stays current
  because updating it is part of every change.

---

## Pillar 2 — Reusable helpers: decide once

### The plain-language problem and fix

**The problem.** Left to its own devices, an AI tends to re-solve the same
problem slightly differently in every file. One script handles a rate-limit one
way; another handles it a different way; a third forgets to handle it at all.
Each variation is a place a bug can hide, and each is something you'd have to fix
separately when the API changes.

**The fix.** The first time you solve a hard, *shared* problem — "how do we talk
to Asana?", "how do we behave when we're rate-limited?", "how do we save to the
database?" — put that solution in **one** small, well-named file (a "helper" or
"module"). Every new feature then *reuses* that helper instead of reinventing it.
You decide the hard thing once; everything downstream inherits the decision.

### In the real project

The project keeps its shared logic in a folder called `src/lib/`. There are
**13 helper modules** there, and they are imported **181 times** across the
project's **46 scripts**. That ratio is the whole point: most scripts are not
piles of new, risky code — they're thin compositions of helpers that are already
tested and trusted.

| Helper | What it decided once |
|---|---|
| `config.py` | How to load credentials safely, with a documented fallback order |
| `http_client.py` | How to talk to Asana, including automatic retry when rate-limited |
| `jira_client.py` | How to talk to Jira, with the same retry safety (Basic auth, jittered backoff) |
| `database.py` | How to save and load data in the local SQLite database |
| `team_picker.py` | The interactive "choose a team" experience (paginated list + name search) |
| `adf.py` | How to convert plain text into Jira's rich-text format |
| `project_scope.py` | How to resolve and *validate* which projects a command targets |
| `logging_utils.py` | How verbosity is configured (`configure_logging` / `resolve_log_level`) |

(The full set also includes `asana_urls.py`, `attachments.py`,
`date_formatting.py`, `team_analysis.py`, and `user_backfill.py`.)

Look at how the big, scary script — the one that creates Jira issues —
`scripts/atlassian/create_team_project_jira_issues.py` — *begins*:

```python
from src.lib.adf import text_to_adf
from src.lib.config import Credentials, load_credentials
from src.lib.database import Database
from src.lib.jira_client import JiraApiError, JiraClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.team_picker import search_team
from scripts.atlassian.export_team_project_jira_csv import build_team_project_rows
```

Almost everything dangerous — authentication, network retries, database writes,
rich-text formatting, the team picker — is *delegated* to a helper that was built
and tested separately. The script's own job is mostly orchestration. That's why
adding the live-Jira creator did not mean writing thousands of lines of new risky
code; it meant *wiring together* trusted parts.

The repair script, `scripts/atlassian/update_personal_project_jira_urls.py`,
makes the force-multiplier effect even more vivid: it imports its scope rows from
`build_team_project_rows`, *and* reuses six helpers directly out of the create
script (`load_field_map`, `_resolve_field_def`, `format_custom_value`,
`resolve_issue_type_id`, `ensure_atlassian_credentials`, and the rate-limit
constants):

```python
from scripts.atlassian.create_team_project_jira_issues import (
    DEFAULT_WORKERS,
    MAX_RATE_LIMIT_ROUNDS,
    RATE_LIMIT_REQUEUE_DELAY,
    _is_rate_limited,
    _resolve_field_def,
    ensure_atlassian_credentials,
    format_custom_value,
    load_field_map,
    resolve_issue_type_id,
)
```

A whole new script — one that *repairs* a field in place via `PUT` — was possible
largely by reusing decisions already made. That's leverage.

### Going deeper (for engineers) — Pillar 2

Three patterns in `src/lib/` are worth stealing:

- **Single responsibility, single public entrypoint.** `config.py` does exactly
  one thing — turn the environment into a validated `Credentials` dataclass — and
  exposes a clear front door (`load_credentials`) while keeping parsing internals
  private (`_load_env_file`, `_resolve_env_path`). Callers never reach past the
  front door, so the internals can change without breaking 46 scripts.

- **Dataclasses as the contract between layers.** Both the HTTP client and the
  config loader return frozen dataclasses (`RateLimitInfo`, `Credentials`). A
  typed, immutable value is far easier to reason about — and to mock in a test —
  than a loose dict. From `config.py`:

  ```python
  @dataclass(frozen=True)
  class Credentials:
      token: str
      workspace_gid: str
      skip_ssl_verification: bool
      atlassian_username: Optional[str] = None
      atlassian_token: Optional[str] = None
  ```

- **Two consumers, one helper.** `jira_client.py` is used by *both* the issue
  creator and `sync_jira_attachments.py`. `adf.py` deliberately renders the *same*
  structured content two ways — `render_blocks_adf` for the rich API and
  `render_blocks_text` for the plain-text CSV export — "so the same content can
  feed the API (rich) and the CSV export (plain) without drifting." The second
  consumer is the proof a helper earned its existence; if logic is only used once,
  it may not need extracting yet.

- **The credential fallback chain lives in one place.** `config.py` resolves
  credentials in a single, documented order — process environment, then the file
  named by `.asana_jira_env_path`, then the default `~/.asana_jira_env` — so
  every script gets identical behavior and the multi-client setup
  ([Part 4](./04-getting-set-up.md)) works everywhere automatically.

The discipline to watch for: *extract when you reuse, not before.* Premature
abstraction is its own cost (see [Part 7](./07-deciding-what-not-to-build.md)).
The signal to extract is the **second** copy, not the first.

### How to ask Claude for this — Pillar 2

- "This is the second place we've written rate-limit retry logic. Extract it into
  a reusable helper in `src/lib/` and use it in both places."
- "Both scripts parse credentials. Move that into a single `config` helper that
  returns a typed `Credentials` object, and update both callers."
- "Before adding this feature, check `src/lib/` for an existing helper I should
  reuse instead of writing new code."
- "Give this helper one public function and make the rest internal."

### Pillar 2 takeaways

- When you notice Claude writing similar code a **second** time, ask it to extract
  a shared helper and use it in both places.
- Reuse isn't just tidiness — it's *fewer decisions, fewer bugs, faster and safer
  features.*
- Prefer typed dataclasses at the boundaries between modules; they're easier to
  reason about and to test.
- A high import-to-script ratio (here, 181 reuses across 46 scripts) is a sign
  the codebase is composing, not copy-pasting.

---

## Pillar 3 — Tests: the automatic safety net

### The plain-language problem and fix

**The problem.** When you didn't write the code line by line, how do you *know*
it works — and keeps working after the next change? You can't re-verify
everything by hand every time, and "it ran once without crashing" is not the same
as "it's correct."

**The fix.** **Tests** are small programs that automatically check your real code
behaves correctly. You run them all with one command. If something breaks — even
something you weren't thinking about — a test fails and points at exactly where.
This is the thing that lets you *accept fast AI-written code with confidence*: the
net is always under you.

The single most important habit in this entire playbook is one command:

```bash
$ python3 -m unittest discover tests
```

If it ends with `OK`, the code is healthy. You run this after *every* change.

### In the real project

There are **48 unit test files** (checking individual pieces) plus **2
integration test files** (checking whole commands end to end). The test layout
deliberately *mirrors* the source layout — `tests/unit/lib/` mirrors `src/lib/`,
`tests/unit/scripts/` mirrors `scripts/` — so every module has a neighboring
test, and a newcomer can find the test for any file by analogy.

The tests deliberately cover the tricky situations where bugs love to hide:

- **Rate limiting.** `tests/unit/lib/http_client.test.py` confirms the client
  reads the "wait this long" header and surfaces rate-limit metadata correctly —
  including the failure path, where it asserts the captured `retry_after` matches
  the `Retry-After` header.
- **Pagination.** `tests/unit/lib/jira_client.test.py` checks that createmeta
  field listings are fetched across pages and keyed by name, so the tool gets
  *all* the fields and doesn't over-fetch.
- **Delete / replace semantics.** `tests/unit/lib/database_task_extras.test.py`
  confirms that replacing a task's comments correctly persists the new rows
  (rather than silently duplicating or losing data).
- **Format conversions.** `tests/unit/scripts/create_team_project_jira_issues.test.py`
  asserts each Jira field type is coerced the right way before it's sent —
  `option` → `{"value": ...}`, an array of options → `[{"value": ...}]`, a number
  string → an actual number, and a "Paragraph" custom field → an ADF document.
- **Streaming and hierarchy.** The integration test
  `tests/integration/scripts/get_tasks_cli.test.py` exercises a whole command's
  streaming path and the task `hierarchy_depth` it produces.

The golden rule, stated in the contract and practiced in the repo: **every time a
bug is found and fixed, add a test for it.** That converts a one-time fix into a
permanent guarantee the bug never silently returns.

### Going deeper (for engineers) — Pillar 3

What makes these tests fast, deterministic, and stdlib-only is worth studying,
because it's how you test network code *without* a network or a `pytest`-style
framework:

- **No real network — inject a fake opener.** Because `AsanaHttpClient` is built
  on `urllib` with an injectable opener, the test swaps in a fake. From
  `http_client.test.py`:

  ```python
  client = AsanaHttpClient(token="token")
  client._opener = Always429Opener(headers)  # force a 429 with no network
  with self.assertRaises(AsanaHttpError) as ctx:
      client.get("/users/me")
  ```

  The fake opener raises a real `urllib.error.HTTPError` with status 429 and a
  header dict, so the *actual* retry/backoff/parse code runs against a controlled
  failure. (The suite uses a tiny `Retry-After` like `0.001` so the real
  `time.sleep` doesn't slow the run.)

- **Pure functions are trivially testable.** `format_custom_value` takes a schema
  dict and a string and returns a value — no I/O, no state. So its test is a
  straight table of inputs and expected outputs:

  ```python
  self.assertEqual(format_custom_value({"type": "option"}, "Active"), {"value": "Active"})
  self.assertEqual(format_custom_value({"type": "array", "items": "option"}, "a"), [{"value": "a"}])
  self.assertEqual(format_custom_value({"type": "number"}, "10"), 10)
  ```

  This is why Pillar 2's "extract a small, focused helper" makes Pillar 3 cheap:
  the more logic lives in pure functions, the more of your code is testable
  without elaborate setup.

- **A `FakeJiraClient` lets the orchestration be tested without Jira.** The issue
  creator's test injects a fake client (the function accepts an optional `client`
  parameter precisely so a test can pass one) that records every payload and can
  be told to fail on cue — which is how the assignee-fallback and 429-requeue
  behaviors (Pillar 4) get covered.

- **Coverage with the standard library.** The contract targets ≥90% statement
  coverage; the project measures it with `python3 -m trace --count --file
  trace.out -m unittest discover tests` — again, no third-party tooling. Coverage
  is a *discipline*, not a guarantee (see [Part 9](./09-pitfalls-and-limits.md)):
  it tells you what code ran, not whether you asserted the right thing.

- **`--failfast` for the inner loop.** During development, `python3 -m unittest
  --failfast` stops at the first failure for faster feedback; the full
  `discover tests` run is what you do before committing.

### How to ask Claude for this — Pillar 3

- "Add a unit test alongside this feature, mirroring the `src/` path in `tests/`.
  Cover the empty-input and pagination cases."
- "We just fixed a bug where replacing comments duplicated rows. Write a
  regression test that would have failed before the fix."
- "What edge cases are we *not* testing here? List them, then write tests for the
  risky ones."
- "Test this network client without making real requests — inject a fake opener
  that raises a 429 so the retry path actually runs."

### Pillar 3 takeaways

- Ask for a test **alongside** every feature and **after** every bug fix.
- Run `python3 -m unittest discover tests` after *every* change; make `OK` your
  green light to continue.
- Design code to be testable: small pure functions and injectable dependencies
  make tests cheap and fast.
- Coverage is a useful target, not a substitute for asserting the *right* thing.

---

## Pillar 4 — Runtime guardrails for anything touching a live system

### The plain-language problem and fix

**The problem.** Tests check the code in a sandbox. But the real risk is the live
run: the moment the tool actually creates tickets in the company's Jira. Tests
can't protect you there — you need protections *at that moment*, when a correct
program might still be pointed at the wrong target, or the network might hiccup,
or a single bad record might otherwise sink the whole batch.

**The fix.** Build "look before you leap" and "fail gracefully" behaviors into
the tool itself. Match the number of guardrails to the blast radius you named in
[Part 3](./03-before-you-write-any-code.md). For this project — which writes to a
live system — the set is rich, and every one is worth copying.

### In the real project

All of the following live in `scripts/atlassian/create_team_project_jira_issues.py`
and its sibling repair script. They are not theoretical; you can read each one:

- **`--dry-run`** — resolves every field and **prints the exact JSON payloads it
  would send to Jira, but creates nothing.** You inspect it first, then run for
  real. In the code, dry-run rows are recorded with a `dry-run` status and a fake
  `MIG-NEW1`-style key so even the run report shows what *would* have happened.

- **Confirmation prompt (`--yes` to skip).** Before any creation it asks:

  ```
  About to create up to N issue(s) in Jira project MIG. Continue? [y/N]
  ```

  Nothing permanent happens until you type `y`. The default answer is *no* — if
  you hit Enter by reflex, it aborts. `--yes` exists for automation, but the
  human-facing path always stops and asks.

- **Automatic retry when rate-limited.** Two layers. First, the Jira client
  itself retries 429s up to `max_retries=8` with jittered backoff. Second, if a
  creation is *still* rate-limited after that, the script **requeues** it to the
  end of the run and retries it across up to `MAX_RATE_LIMIT_ROUNDS = 3` more
  rounds with an escalating delay (`RATE_LIMIT_REQUEUE_DELAY = 5.0` seconds,
  multiplied by the round number) before recording it as an error.

- **Graceful fallback for unassignable users.** If Jira rejects a create with a
  `400` whose error names the `assignee` (an active account that simply can't be
  assigned issues in that project), `_create_issue_with_assignee_fallback`
  **retries once with the assignee stripped** and records the issue as `created`
  with the message `assignee dropped (user not assignable)`. One bad assignment
  doesn't sink the ticket, let alone the batch.

- **Two-pass "clear then set."** The repair script's `--clear-first` mode splits
  the run into two passes — **clear** the field on *every* in-scope issue, then
  **set** the new value on the ones that have one — "so no stale value can survive
  even if the set pass misses an issue." A set failure *after* a successful clear
  is honestly reported as `error` with the message `set failed (field left
  cleared)`, so you know precisely what state each issue is in.

- **Idempotency — safe to run twice.** As each issue is created, its
  Asana → Jira key is persisted to the `team_project_jira_mappings` table. On
  re-run, already-mapped rows are recorded as `skipped-existing` and *not*
  recreated. So if a run is interrupted, re-running **heals** the migration
  instead of creating duplicates. (`--reset` deliberately deletes the persisted
  mappings when you *want* recreation, e.g. after deleting the issues in Jira.)

- **Scope guards.** `src/lib/project_scope.py`'s `validate_scope_args` refuses
  contradictory or empty instructions before any work begins — so you can't
  accidentally target the entire company.

### Going deeper (for engineers) — Pillar 4

The implementations reward a closer look:

- **The confirmation is computed against the *real* count, capped by `--limit`.**
  ```python
  planned = total_pending if limit is None else min(limit, total_pending)
  answer = input(
      f"About to create up to {planned} issue(s) in Jira project {jira_project_key}. Continue? [y/N] "
  ).strip().lower()
  if answer not in {"y", "yes"}:
      raise SystemExit("Aborted by user.")
  ```
  Note it counts *pending* work (already-mapped rows are excluded up front), so
  the number you're approving is the number that will actually be created.

- **The assignee fallback is surgical.** It only retries on the specific
  assignee `400`; every other error — including rate limits — propagates so the
  *caller's* retry/requeue handling still applies. The detector parses the JSON
  `errors` dict and falls back to a substring check:
  ```python
  if isinstance(errors, dict) and "assignee" in errors:
      return True
  ...
  return "assignee" in lowered and "cannot be assigned" in lowered
  ```
  This is the right shape for a fallback: narrow trigger, single retry, honest
  reporting — not a blanket "swallow all errors."

- **The 429 requeue is a round-based queue, not a tight loop.** Epics are created
  sequentially (tasks depend on epic keys), tasks in parallel across `--workers`
  (default 4). In both passes, rate-limited items are collected into a `requeue`
  list and retried in escalating-delay rounds. `JiraApiError` exposes `.status`
  and `.body` precisely so `_is_rate_limited` and `_is_unassignable_error` can
  classify failures without string-guessing.

- **Idempotency is enforced at the data layer, not just by convention.** The
  primary key on `team_project_jira_mappings` is `(asana_gid, kind)`. Mappings
  are loaded *before* the run and checked per row; errored items are intentionally
  left *unmapped* so a re-run retries exactly them. This is why "run it again" is
  a safe instinct rather than a dangerous one.

- **Scope validation fails loud and early.** `validate_scope_args` raises
  `SystemExit` with a helpful message when zero or more-than-one scope is given:
  ```python
  if sum(scope_flags) == 0:
      raise SystemExit("No project scope specified. Provide --project-gid, ...")
  if sum(scope_flags) > 1:
      raise SystemExit("Multiple project scopes specified. Choose only one of ...")
  if exclude_team_gids and not all_teams:
      raise SystemExit("--exclude-team-gids can only be used with --all-teams.")
  ```
  Failing before any network call is the cheapest possible place to catch "you
  asked for one project *and* all teams."

The meta-point: each guardrail addresses a *specific failure mode you can name* —
wrong target, transient 429, unassignable user, interrupted run, stale field.
Don't bolt on generic "error handling"; design the guardrail to the failure.

### How to ask Claude for this — Pillar 4

- "Add a `--dry-run` mode that prints the exact payloads it would send and changes
  nothing, plus a confirmation prompt that defaults to *no* before any write."
- "Make this re-runnable: persist what we created and skip already-created items
  on the next run. Add a `--reset` flag to force recreation."
- "If the API rate-limits us, retry with backoff, then requeue and retry a few
  more rounds before giving up — and report the ones that still fail."
- "Add scope validation that refuses if the user specifies zero or more than one
  target, with a helpful error message."

### Pillar 4 takeaways

- For anything that changes a live system, insist on **dry-run + confirmation +
  re-runnability** *before* a large run — design for failure and reversibility
  first, scale second.
- Build guardrails to *named* failure modes (wrong target, transient error,
  interrupted run), not generic catch-alls.
- Make "run it again" safe by persisting progress and skipping done work;
  idempotency is a feature, not an accident.
- Confirmation prompts should default to *no* and report the true count.

---

## Pillar 5 — An interactive CLI so anyone can run it safely

### The plain-language problem and fix

**The problem.** A powerful tool is useless — and *dangerous* — if the only way to
run it is to type twelve perfectly-correct command-line options. People will get
it wrong, and "wrong" here can mean bad data in a live system. The expert who
wrote the tool can run it safely; the operator who inherits it next month cannot.

**The fix.** Wrap the tool in a friendly, guided experience: a menu that presents
choices, walks you through each step, refuses obviously-wrong input, and *carries
the Pillar 4 guardrails right into the flow.* Accessibility, here, is a safety
feature — the easier the safe path is, the less anyone reaches for the dangerous
one.

### In the real project

`scripts/interactive_cli.py` is that menu. Launch it with:

```bash
$ python3 -m scripts.interactive_cli --log-level INFO
```

It presents numbered **categories** that mirror the natural workflow:

1. **Initialization** (metadata only)
2. **Tasks** (full / extras / incremental)
3. **Atlassian** (exports, mappings, attachments)
4. **Reports** (projects, users, memberships, team summary, Jira CSV)
5. **Credentials** (create or change the env file)

…plus **Quit**. Each category opens a submenu, and each step *builds the
command-line arguments for you* by asking plain-English questions, so you never
have to remember a flag.

Several touches make it genuinely safe for a non-expert:

- **First-launch credential setup.** Before showing the menu, `main` calls
  `ensure_interactive_credentials`. If no credentials file is found, it doesn't
  crash with a cryptic error — it says "Let's create one before continuing,"
  prompts you for your token, workspace ID, and so on, then **writes both the env
  file and the `.asana_jira_env_path` pointer for you** (via the same `config.py`
  helpers from Pillar 2). This is the "easy way" promised in
  [Part 4](./04-getting-set-up.md).
- **Searchable pickers instead of raw GIDs.** Rather than asking you to paste a
  cryptic ID, steps offer fuzzy search by name — projects, teams, and users —
  ordered so the best match floats to the top. You type part of a name and pick a
  number.
- **Guardrails carried into the flow.** When the menu drives the attachment sync,
  it *defaults the dry-run prompt to yes*:
  ```python
  if confirm("Dry run (no uploads)?", default=True):
      args.append("--dry-run")
  ```
  The safe choice is the default choice.
- **It teaches you the direct command.** After running a step, it prints the exact
  `python3 -m …` command it just ran, so an operator can graduate to the raw CLI
  when they're ready — accessibility *and* a learning ramp.

### Going deeper (for engineers) — Pillar 5

The design choices that keep this maintainable:

- **A wrapper, not a rewrite.** The menu imports each script's `main` and calls it
  with a built argv — "they stay runnable on their own; we just reuse them."
  ```python
  def run_script(main_func, argv):
      return main_func(argv)
  ```
  This is Pillar 2 again: the menu is a thin composition over the same entrypoints
  the raw CLI exposes. There's no second implementation to keep in sync, so the
  menu can't drift from what the scripts actually do.

- **Menu items are data.** Each option is a small frozen dataclass binding a key,
  a label, and a handler:
  ```python
  @dataclass(frozen=True)
  class MenuItem:
      key: str
      label: str
      handler: Callable[[], None]
  ```
  Adding a feature to the menu is adding one `MenuItem`, not editing a sprawling
  `if/elif` block.

- **The pickers reuse the shared `team_picker` patterns.** The full searchable
  team experience (`choose_team` / `search_team` in `src/lib/team_picker.py`,
  paginating 10 per page with `n`/`p`/`q` controls) is the same logic used by
  `get_tasks.py`, `get_task_extras.py`, and the issue creator's `--pick-team`. The
  interactive CLI's own `_fuzzy_projects` / `_fuzzy_teams` / `_fuzzy_users`
  helpers order results by match position (`instr(...)`) then length, so the
  closest name comes first.

- **It degrades gracefully without a database.** The lookup helper tries to open
  SQLite and, if it can't, silently falls back to "type a GID" rather than
  crashing — so the menu is usable even before the first sync.

- **Every step is logged.** `log_run` appends a JSONL record (timestamp, step,
  argv, status) to `exports/interactive_runs/…`, so there's an audit trail of what
  an operator did — useful when a migration goes sideways and you need to
  reconstruct the sequence.

The principle: the interactive layer should be a *thin, data-driven wrapper over
the same tested entrypoints* — never a parallel implementation. That keeps Pillars
3 and 4 in force no matter which door the user comes through.

### How to ask Claude for this — Pillar 5

- "Add an interactive menu that wraps these scripts by calling their existing
  `main` functions with built-up arguments — don't reimplement them."
- "On first launch, if no credentials are found, walk the user through creating
  them and write the env file, instead of crashing."
- "Replace the raw GID prompt with a fuzzy name search that lists matches and
  lets the user pick a number."
- "In the menu flow, default the dry-run prompt to *yes* and confirm before
  anything permanent."

### Pillar 5 takeaways

- Accessibility is a safety feature: the more the tool guides the operator and
  refuses obviously-wrong input, the fewer costly mistakes reach the live system.
- Build the menu as a **thin wrapper** over the same tested entrypoints, so it
  can't drift and your tests still cover what it runs.
- Carry the Pillar 4 guardrails *into* the flow — default to the safe choice.
- Make the menu a learning ramp: show the equivalent direct command so operators
  can graduate.

---

## How the pillars reinforce each other

The pillars are not independent — they compound:

- **Pillar 1 mandates Pillars 3 and 4.** The rules file *requires* a test for
  every script and (in practice) dry-run/confirmation for anything that writes.
  The contract makes the other pillars non-optional.
- **Pillar 2 makes Pillar 3 cheap.** Small, pure, well-named helpers are trivial
  to test (`format_custom_value` is a one-line assertion table). Extracting logic
  *creates* testable surface.
- **Pillar 2 makes Pillar 4 reliable.** The retry, the fallback, and the
  rate-limit constants live in *one* place and are reused by every write path, so
  every script that touches Jira inherits the same battle-tested guardrails.
- **Pillar 5 delivers Pillars 4 and 3 to non-experts.** Because the menu wraps the
  same tested entrypoints and defaults to the safe choices, an operator who's
  never read the code still gets the dry-runs, confirmations, and validated logic.

That's defense-in-depth: a mistake that slips past one pillar runs into the next.

---

## Key takeaways

- **Treat the five pillars as layered defense, not a checklist.** Each one catches
  a different class of mistake at a different moment.
- **Pillar 1 — Rules file:** write your standards once in `CLAUDE.md`, but let
  Claude write it (`/init`, then *ask*). Capture corrections so they persist. Keep
  it living.
- **Pillar 2 — Reusable helpers:** decide each hard problem once in `src/lib/`,
  reuse everywhere. Extract on the *second* copy. (Here: 13 modules, 181 reuses,
  46 scripts.)
- **Pillar 3 — Tests:** a test alongside every feature and after every bug fix;
  run `python3 -m unittest discover tests` after every change; design code to be
  testable. (Here: 48 unit + 2 integration files.)
- **Pillar 4 — Runtime guardrails:** for live-system writes, insist on dry-run +
  confirmation + re-runnability, built to *named* failure modes.
- **Pillar 5 — Interactive CLI:** wrap the tool in a guided, data-driven menu over
  the same tested entrypoints, defaulting to the safe choice, so anyone can run it.
- You don't need all five on day one — but adding them in order (see
  [Part 8](./08-recipe-for-your-own-project.md)) is what separates a risky
  experiment from a dependable tool.

---

## Where to next

- **Previous:** [Part 4 — Getting set up](./04-getting-set-up.md) — terminal,
  Python, virtualenv, credentials, and running the tool.
- **Parent / index:** [Vibe Coding, Safely — the playbook](../claude-code-playbook.md)
  · [series index](./README.md)
- **Next:** [Part 6 — The daily loop](./06-the-daily-loop.md) — how to actually
  work with Claude turn by turn, putting these five pillars into a rhythm.

Other parts in the series:
[1 — What vibe coding means](./01-what-is-vibe-coding.md) ·
[2 — Speed inside guardrails](./02-speed-inside-guardrails.md) ·
[3 — Before you write any code](./03-before-you-write-any-code.md) ·
[4 — Getting set up](./04-getting-set-up.md) ·
[5 — The five pillars](./05-five-pillars.md) ·
[6 — The daily loop](./06-the-daily-loop.md) ·
[7 — Deciding what not to build](./07-deciding-what-not-to-build.md) ·
[8 — A recipe for your own project](./08-recipe-for-your-own-project.md) ·
[9 — Pitfalls and honest limits](./09-pitfalls-and-limits.md)
