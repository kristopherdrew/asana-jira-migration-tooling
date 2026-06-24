# What "Vibe Coding" Means Here

*A plain-language deep dive into what vibe coding actually is, where it goes wrong, and how to do the disciplined version — using a real, high-stakes tool as the worked example.*

> Part 1 of 9 in the Vibe Coding, Safely series
> This document expands Section 1 of the [parent playbook](../claude-code-playbook.md) into a standalone, beginner-to-advanced guide.

---

## Table of contents

1. [The one-sentence version](#the-one-sentence-version)
2. [What "vibe coding" actually means](#what-vibe-coding-actually-means)
3. [Why it's so appealing](#why-its-so-appealing)
4. [Why it's risky on its own](#why-its-risky-on-its-own)
5. [The spectrum: reckless ↔ disciplined](#the-spectrum-reckless--disciplined)
6. [Concrete failure modes](#concrete-failure-modes)
7. [What "disciplined vibe coding" changes](#what-disciplined-vibe-coding-changes)
8. [The "blast radius" framing](#the-blast-radius-framing)
9. [When vibe coding is appropriate — and when it isn't](#when-vibe-coding-is-appropriate--and-when-it-isnt)
10. [The role of the human](#the-role-of-the-human)
11. [How the example project embodies disciplined vibe coding](#how-the-example-project-embodies-disciplined-vibe-coding)
12. [Going deeper (for engineers)](#going-deeper-for-engineers)
13. [Key takeaways](#key-takeaways)
14. [Where to next](#where-to-next)

---

## The one-sentence version

**Vibe coding is describing what you want in plain English and letting an AI write the code, without reading every line yourself.** That's it. It's fast, it's accessible, and — done carelessly — it's a great way to ship code that looks right and quietly isn't.

This whole series is about the *disciplined* version: keeping the speed, while putting a structure around it so mistakes get caught before they hurt anyone.

If you read nothing else, read this: **vibe coding is not a skill level, it's a working style — and the style is only as safe as the guardrails you wrap around it.**

---

## What "vibe coding" actually means

Let's slow down and define the terms, because the phrase gets used loosely.

Traditionally, writing software meant a person typing every instruction by hand, line by line, and understanding each one. The "vibe" in vibe coding is the shift away from that. Instead of writing the instructions, you *describe the outcome* — "I want a command that exports every user and how active they've been to a spreadsheet" — and an AI assistant produces the actual code.

A few words you'll see throughout this series, defined in plain language:

- **Code** — the written instructions that tell a computer what to do. Normally a human writes it; in vibe coding, the AI writes most of it.
- **AI assistant / coding agent** — a program (here, **Claude Code**, Anthropic's official command-line assistant) that you talk to in plain English. It can read your files, write new code, and run commands on your behalf.
- **Prompt** — the plain-English request you give the AI. "Add a confirmation prompt before this creates anything" is a prompt.
- **Plain-language programming** — the heart of the idea: you supply intent and judgment; the AI supplies the typing and the syntax.

So vibe coding is a *division of labor*. You bring the **what** and the **why**. The AI brings the **how** and the **keystrokes**. The interesting question — the one this entire series exists to answer — is: **who is responsible for making sure the "how" is actually correct and safe?** In reckless vibe coding, nobody is. In disciplined vibe coding, a system is.

### A useful analogy: the very fast intern

Picture a brilliant, lightning-fast intern. They can produce a week's worth of work in an afternoon. They're confident, articulate, and almost always right — but not *always* right, and when they're wrong they're wrong with the same confidence as when they're right.

Would you let that intern push changes straight into your company's live systems with nobody checking? Of course not. But you also wouldn't want to slow them to a crawl by re-typing everything yourself. What you'd want is a setup where their speed is preserved *and* their mistakes get caught: a checklist they always follow, automatic checks on their output, and a rule that nothing permanent happens without a human nodding.

That setup is what the rest of this series builds. Vibe coding is the fast intern. The playbook is the management structure that makes the intern trustworthy.

---

## Why it's so appealing

It's worth being honest about *why* people reach for vibe coding, because the appeal is real and not a trap to be ashamed of.

- **Speed.** What used to take days of writing, looking up syntax, and debugging can take minutes. You stay in the flow of *what you're trying to accomplish* instead of getting lost in the mechanics.
- **Accessibility.** You don't have to be a career software engineer. If you can describe a goal clearly and follow instructions, you can build real, useful tools. This series assumes exactly that reader.
- **Lower activation energy.** The hardest part of any project is often just starting. "Describe it and see what comes back" is a much smaller first step than "learn a language, set up an environment, and write your first hundred lines."
- **Exploration.** Because trying an idea is cheap, you can explore several approaches and keep the one that fits — instead of committing to the first design because rewriting is expensive.
- **Focus on judgment, not syntax.** The scarce, valuable human skill becomes *deciding what's worth building and what "correct" means* — not memorizing where the semicolons go.

None of these benefits are illusions. The example project in this series — a toolkit that migrates a company's data from **Asana** to **Jira** — exists *because* vibe coding made it feasible to build something substantial without a full engineering team. The point of the series isn't to talk you out of the speed. It's to let you keep the speed without inheriting the danger.

---

## Why it's risky on its own

Here's the uncomfortable half. The same property that makes vibe coding fast — *you didn't read every line* — is exactly what makes it risky.

Think about what you're actually doing: **you are shipping code you did not fully scrutinize.** In traditional coding, the act of writing each line forces you to at least glance at every decision. In vibe coding, those decisions happen behind a curtain. Most of them are fine. Some of them aren't. And you won't know which is which just by looking at the polished result, because:

- **AI writes confident, wrong code.** This is the single most important thing to internalize. The assistant doesn't sound less sure when it's mistaken. A subtly wrong function and a perfectly correct one are written in the same calm, authoritative tone. Fluency is not the same as correctness.
- **You can't tell quality by reading the summary.** When the AI says "I added retry logic and a confirmation prompt," it probably did — but "probably" is not "verifiably," and the gap is where bugs live.
- **Mistakes compound silently.** A small wrong assumption early on (say, about how data is paginated, or how duplicates are detected) can ripple through dozens of later features that were all built on top of it.
- **The stakes can be invisible.** A tool that only prints text to your screen can be wrong with no consequence. A tool that *writes to a live system used by real people* can be wrong in ways that are expensive, embarrassing, and hard to undo.

So the risk of vibe coding *by itself* — with no structure around it — isn't that the AI is bad. It's that **nothing is checking the AI, and you've removed the one check that traditional coding had built in (a human reading every line).** Remove that check and add nothing in its place, and you've built a fast machine for producing plausible-looking mistakes.

The fix is not "read every line again" — that would throw away the whole benefit. The fix is to **replace the human-reading-every-line check with better, automatic checks** that run faster than you ever could. That's what disciplined vibe coding is.

---

## The spectrum: reckless ↔ disciplined

Vibe coding isn't one thing. It's a spectrum, and where you sit on it is a *choice you make*, not a fixed property of "using AI."

```
RECKLESS  <------------------------------------------------>  DISCIPLINED
"just ship it"                                        "speed inside guardrails"

no rules file            sometimes a rules file            CLAUDE.md every session
no tests                 a few tests                       tests beside every change
write to live, hope      a manual double-check             dry-run + confirm + idempotent
copy-paste each fix      reuse some helpers                shared, tested helpers
human absent             human glances                     human owns every commit & live run
```

At the **reckless** end: you describe what you want, accept whatever comes back, and run it. It works often enough to feel safe — right up until the day it doesn't, and by then you've built a lot on top of code nobody verified.

At the **disciplined** end: you still let the AI do the heavy lifting and you still move fast, but every risky decision passes through a check. Tests catch logic mistakes. Guardrails catch dangerous actions. A written rules file keeps the AI to *your* standards. The human stays accountable for the irreversible moments.

Here's the key insight most people miss: **disciplined vibe coding is not slower than reckless vibe coding in any way that matters.** The guardrails are mostly automatic. A test suite runs in seconds. A dry-run is one extra command. A confirmation prompt is one keypress. You pay a tiny, fixed tax up front and at the moments of real danger — and in exchange you get to *trust* the output, which is the only thing that makes the speed worth having. Reckless speed that you can't trust isn't actually fast; it just defers the cost to a worse moment.

| | Reckless vibe coding | Disciplined vibe coding |
|---|---|---|
| **Rules for the AI** | In your head, re-explained each time (or skipped) | Written down once in a rules file the AI reads every session |
| **How you know it works** | It ran without crashing | Automated tests confirm behavior, including edge cases |
| **Touching a live system** | Run it and hope | Dry-run → inspect → confirm → run; safe to re-run |
| **Repeated logic** | Re-solved slightly differently each time | Solved once in a shared helper, reused everywhere |
| **Recovery from mistakes** | Hard; may be irreversible | Small frequent commits = an undo button |
| **Who's accountable** | Unclear | The human, explicitly, for every commit and live run |
| **Speed** | Fast until it breaks | Fast *and* sustainable |
| **Trust in the output** | "Seems fine" | Verifiable |

The whole rest of this series is a tour of the right-hand column.

---

## Concrete failure modes

Abstract warnings don't stick. Here are specific, recognizable ways reckless vibe coding goes wrong — several of which the example project deliberately defends against (you'll meet the defenses in [Part 5](05-five-pillars.md)).

- **The confident hallucination.** You ask for a feature; the AI uses a function or library that *sounds* real but doesn't exist or doesn't behave the way it claimed. The code looks perfect. It fails only when run — or worse, runs but does the wrong thing.
- **The silent duplicate.** A tool that creates records re-runs after an interruption and creates *everything again*, because nothing remembered what it already did. Now there are two of every ticket. (The defense: **idempotency** — remembering past work so a re-run *heals* instead of duplicating.)
- **The half-finished update.** A repair job that changes records one by one gets interrupted halfway. Now half your data is new, half is old, and nothing tells you which is which. (The defense: a **two-pass "clear then set"** approach so no stale half-updated state survives.)
- **The runaway scope.** You meant to migrate one project; a contradictory or under-specified instruction targets *the entire company* instead. (The defense: **scope guards** that refuse contradictory instructions.)
- **The rate-limit crash.** The live service says "slow down, too many requests," and a naive tool simply crashes and loses all its progress. (The defense: **automatic retry** that waits and resumes.)
- **The brittle one-off.** Each fix is pasted in a slightly different way in a different place, so the same bug has to be fixed three times and one copy is always forgotten. (The defense: **reusable helpers** — solve it once, in one place.)
- **The drift between docs and reality.** The README says the tool does X; the tool now does Y. New work — by humans *and* by the AI — is built on the stale description. (The defense: **living documentation** the AI re-reads every session.)
- **The unreviewed commit.** The AI saves a permanent checkpoint that nobody approved, and now a mistake is baked into the project's history. (The defense: a hard rule — **never commit without explicit human approval.**)

Notice the pattern: none of these failures are exotic. They're ordinary, and they're predictable. That predictability is good news — it means each one has a known, repeatable defense. Reckless vibe coding leaves the defenses out. Disciplined vibe coding builds them in.

---

## What "disciplined vibe coding" changes

So what concretely separates the disciplined version? It adds **three kinds of structure**, and the parent playbook frames the goal as a single phrase worth memorizing:

> **AI speed *with* engineering discipline.**

The three kinds of structure:

1. **Mistakes get caught automatically — by tests.** A *test* is a small program that checks your real code behaves correctly. You run them all with one command. When something breaks — even something you weren't thinking about — a test fails and points at it. This is what lets you accept fast, AI-written code *with confidence*: you didn't read every line, but the tests did exercise them.

2. **Dangerous actions require confirmation — by guardrails.** A *guardrail* is a runtime protection at the moment of real risk: a *dry-run* that shows what *would* happen without doing it, an "are you sure?" prompt before anything permanent, automatic retries instead of crashes, and re-runnability so an interrupted job can resume safely. Tests protect the code; guardrails protect the live system.

3. **The AI keeps to *your* standards — by a written rules file.** A file named `CLAUDE.md` at the top of the project lists your conventions, and the AI reads it automatically *every single time* it works there. Instead of re-explaining "use this version, no outside libraries, write tests, never commit without asking" in every conversation, you write it down once and it becomes a standing contract.

The result, in the parent playbook's words: *you move at AI speed, but the output is something you can actually trust and maintain.* That last word — **maintain** — matters as much as "trust." Reckless vibe coding can produce something that works *today*. Disciplined vibe coding produces something you (or a teammate, or the AI next month) can safely *change* tomorrow.

These three structures are the seed of the **five pillars** covered in [Part 5](05-five-pillars.md). You don't need all of them on day one. Knowing they exist — and adding them as the stakes rise — is what separates a risky experiment from a dependable tool.

---

## The "blast radius" framing

Here is the single most useful mental tool in this entire series, and it deserves its own section.

Before you build anything, ask one question:

> **What is the worst thing this tool could do?**

The answer is your **blast radius** — the size of the crater if everything goes wrong at once. And the rule that follows is simple:

> **Match the safety effort to the blast radius.**

Some tools have a tiny blast radius. A script that reads some data and prints a summary to your screen can be completely wrong and the worst case is... you read a wrong number and run it again. It needs very few guardrails.

Other tools have a large blast radius. A script that **creates real records in a live system used by an entire organization** can, if it's wrong, produce thousands of bad entries that real people then have to find and clean up. That tool needs *many* guardrails.

This framing is liberating, because it tells you when *not* to over-engineer. You don't slather every throwaway script in dry-runs and confirmation prompts — that would be its own kind of waste. You spend your safety budget where the blast radius is large. The discipline scales with the danger.

A quick way to size it up:

| Blast radius | Example | Safety effort |
|---|---|---|
| **Tiny** | Reads data, prints to screen | Almost none — just run it |
| **Small** | Reads data, writes a local file (a spreadsheet) | Light — sanity-check the output |
| **Medium** | Changes local state you depend on (a local database) | Moderate — tests, backups, re-runnability |
| **Large** | Creates or changes records in a *live, shared* system | Heavy — dry-run, confirmation, retry, idempotency, scope guards |

The example project lives at the **large** end, and that's precisely why it's a good teacher: it forces you to see every guardrail in action. A tool that only printed text wouldn't need them, and you'd learn nothing about safety from building it.

---

## When vibe coding is appropriate — and when it isn't

Vibe coding is a tool, not a religion. Here's an honest accounting of when to lean in and when to be cautious.

**Lean in when:**

- The **blast radius is small to medium** — read-only tools, local files, throwaway analysis, prototypes you'll discard.
- You're **exploring** and want to try several approaches cheaply before committing.
- You can **verify the result** easily — you can read the output, or write a test, or do a dry-run.
- The task is **well-bounded and you can describe it clearly** — clear intent in, checkable result out.
- You're **learning** — vibe coding plus reading the AI's explanations is a genuinely good on-ramp.

**Be cautious (and add discipline before scaling) when:**

- The tool **writes to a live, shared system** — irreversible actions demand dry-runs, confirmation, and re-runnability *first*.
- **Correctness is hard to verify by eye** — financial calculations, security-sensitive logic, anything where "looks right" and "is right" can diverge.
- The thing is **long-lived and will be maintained** — code you'll keep and change needs tests and helpers so it doesn't rot.
- **Secrets and credentials** are involved — these need careful, deliberate handling regardless of speed (covered in [Part 4](04-getting-set-up.md)).
- You **can't yet articulate what "correct" means** — if you can't describe the right answer, you can't check the AI's, and you should slow down to define it first.

The honest summary: **vibe coding is almost always appropriate as a *way of working* — what changes with the stakes is how much structure you wrap around it.** Low stakes, light structure. High stakes, heavy structure. The decision is never "vibe code or don't"; it's "how many guardrails does *this* blast radius earn?"

---

## The role of the human

If the AI writes most of the code, what's left for the person? Quite a lot — and it's the part that actually matters.

In disciplined vibe coding, the human is not the typist. The human is the **director, the verifier, and the accountable party.** Specifically, the human:

- **Owns the intent.** Deciding *what* to build, what "correct" means, and — crucially — what *not* to build (the subject of [Part 7](07-deciding-what-not-to-build.md)). The AI can't tell you what your project is for.
- **Sets the standards.** Writing (or, more often, *asking the AI to write*) the rules file that the AI then follows. You decide the conventions; the AI enforces them on itself.
- **Judges the plan.** On anything non-trivial, you review the AI's *approach* before it writes code — it's far cheaper to fix a plan than finished code.
- **Approves the irreversible.** Every permanent checkpoint (a "commit") and every live run is a human decision. The "are you sure?" prompts are not speed bumps to click through — they're the moments where your judgment is the safety system.
- **Stays accountable.** When the tool runs against real data, *you* are responsible for the outcome, not the AI. That responsibility is exactly why the guardrails exist: they make your accountability *manageable* instead of terrifying.

A good way to hold it in your head: **the AI is responsible for the keystrokes; you are responsible for the consequences.** The discipline in this series is what keeps that division honest — it gives you automatic help (tests, guardrails, rules files) so that being accountable doesn't mean re-reading every line, just owning every irreversible decision.

---

## How the example project embodies disciplined vibe coding

Throughout this series, one real tool is the worked example: an **Asana→Jira migration toolkit**. Let's see how every idea above shows up in it concretely.

**What it does.** It reads *everything* out of a company's Asana account — users, teams, projects, tasks, comments, attachments, custom fields — and stores it locally (in a single database file, `data/asana.sqlite`, with spreadsheet outputs going to an `exports/` folder). Then it **creates the matching items in Jira.**

**Why it's the perfect teaching example — its blast radius is large.** That last step writes to a *live system*. A careless run could create thousands of wrong tickets that real people would have to clean up. High stakes is not a reason to avoid vibe coding here — it's the reason the *discipline* matters and is visible.

Now watch the disciplined structure show up:

- **A rules file as a contract.** The project's rules file (`AGENTS.md`, with `CLAUDE.md` pointing to it) encodes deliberately strict standards: use `python3` (never the ambiguous bare `python`), require modern Python (3.11+), **standard library only** (no popular third-party packages like `requests` or `pytest`), put tests next to the code they cover and aim for ≥90% coverage, use conventional commit messages, and — the load-bearing rule — **never create a commit without explicit human approval.** The AI re-reads this every session. (Deep dive in [Part 5](05-five-pillars.md).)

- **Reusable helpers so decisions are made once.** A shared `src/lib/` folder holds **13 helper modules** — for loading credentials, talking to Asana, talking to Jira, saving to the database, converting text to Jira's rich-text format, and more. They're reused **181 times across 46 scripts** (via `src.lib.*` imports). A big new feature is mostly *wiring together* trusted, already-tested helpers rather than thousands of lines of fresh, risky code.

- **Tests as the automatic safety net.** There are **48 unit test files** (checking individual pieces) plus **2 integration test files** (checking whole commands end-to-end), and they deliberately cover the tricky cases where bugs hide — rate limiting, pagination, delete/replace logic, and format conversions. Run them all with `python3 -m unittest discover tests`; if it says `OK`, you're clear to continue.

- **Runtime guardrails for the live writes.** This is where the large blast radius is tamed: a `--dry-run` mode that shows exactly what *would* be sent to Jira without changing anything; confirmation prompts (with `--yes` to skip) before creating issues; automatic **429** rate-limit retry and requeue instead of crashing; a graceful **assignee fallback** that drops one bad assignment rather than failing the whole batch; a two-pass **`--clear-first`** repair so no stale half-updated data survives; **idempotent re-runs** (it persists what it already created, so a re-run heals rather than duplicates); and **scope validation** that refuses contradictory targeting.

- **An interactive, guided experience.** The tool can be driven through a menu rather than by memorizing flags — so a non-expert can run a high-stakes migration without typing a dozen perfect options, *and* the guardrails (confirmations, dry-runs) are built right into the flow.

That's the entire philosophy made tangible. The project moves at AI speed — most of it was vibe-coded — yet you can trust a run against a real company's live Jira, because every dangerous moment passes through a check. **High blast radius, heavy discipline, full speed.** That's the proof that the two can coexist.

---

## Going deeper (for engineers)

If you have an engineering background, here's the same story in your vocabulary, with the nuances that matter.

**Vibe coding doesn't remove engineering rigor — it relocates it.** In traditional development, rigor is front-loaded into authorship: you reason about each line as you write it. With an LLM-based agent authoring the code, that per-line review is no longer the binding constraint (it's also the wrong place to spend it — re-reading every diff defeats the throughput gain). So you relocate rigor to the *boundaries*: the spec (what "correct" means), the verification layer (tests, types, property/edge-case coverage), and the runtime invariants (idempotency, confirmation gates, scope assertions). The agent's output is treated as *untrusted-until-verified*, and you invest in making verification cheap and automatic rather than manual.

**The economic argument.** The cost of a defect rises sharply the later it's caught: a failing unit test costs seconds; a bad production run costs hours of cleanup plus trust. Reckless vibe coding doesn't eliminate that cost — it *defers and amplifies* it. Disciplined vibe coding pays a small, fixed verification tax (seconds per change, one extra command per live run) to keep defects in the cheap zone. The dry-run is the clearest example: it's a near-zero-cost preview of an otherwise irreversible, high-cost action.

**Why "standard library only" is a defensible constraint here.** It's a deliberate trade. You write more code by hand in spots where a dependency would've been shorter — for instance, the Asana HTTP client (`src/lib/http_client.py`) uses Python's built-in `urllib` rather than `requests`:

```python
# src/lib/http_client.py — only built-in modules
import json
import ssl
import time
from urllib import error, parse, request
```

In return you get **zero supply-chain attack surface, no dependency resolution, and a tool that runs anywhere CPython 3.11+ exists with nothing to install.** For a tool that handles a client's credentials and writes to their live Jira, shrinking the trust boundary is worth the extra hand-written code. The constraint also has a *second-order* benefit specific to AI authorship: a smaller, more uniform surface means fewer ways for the agent to drift into inconsistent patterns, and every networking call goes through one reviewed, tested helper.

**Idempotency as a first-class design property.** The create path persists mappings of what it already created and skips them on re-run. This turns the live operation from a fragile one-shot into a *resumable, convergent* process — interrupt it, fix the cause, re-run, and it converges to the desired state instead of duplicating. Combined with the two-pass `--clear-first` repair (clear-all, then set-all, so no record is left in a stale intermediate state), the tool treats "the run got interrupted" as a normal, recoverable case rather than a disaster. This is the runtime analogue of the test suite: tests make *code changes* safe to iterate on; idempotency makes *live runs* safe to iterate on.

**Where the human stays in the loop, by design.** Two gates are non-negotiable and encoded in the rules file: no commit without explicit approval, and confirmation before any live mutation (bypassable only by an explicit `--yes` the operator types deliberately). These are deliberately placed at the two irreversible boundaries — version history and the live system — and nowhere else, so the friction lands exactly where reversibility ends and not before.

**Plan mode and built-in review.** Claude Code can research the codebase and propose an approach *before* editing (plan mode), and offers built-in code review. For an engineer, these map cleanly onto design review and PR review — the difference is they happen continuously and in seconds, so the cost of "review the plan, not the finished code" is low enough to actually do every time.

---

## Key takeaways

- **Vibe coding = describing intent in plain English and letting an AI write the code** without reading every line. It's a working style, not a skill tier.
- **It's appealing for real reasons** — speed, accessibility, exploration, focus on judgment over syntax. Those benefits are genuine, not a trap.
- **On its own it's risky** because you removed the "human reads every line" check and put nothing in its place — and AI writes *confident, wrong* code that looks identical to correct code.
- **It's a spectrum from reckless to disciplined**, and where you sit is a choice. Disciplined vibe coding keeps the speed while replacing line-by-line review with *automatic* checks.
- **Discipline adds three structures:** tests (catch mistakes automatically), guardrails (gate dangerous actions), and a rules file (keep the AI to your standards).
- **Blast radius is the master question:** *what's the worst this could do?* Match the safety effort to that answer — light structure for tiny stakes, heavy structure for live, shared systems.
- **The human's job changes, not disappears:** you own intent, standards, plan review, and every irreversible decision. The AI owns keystrokes; you own consequences.
- **The Asana→Jira toolkit proves it scales:** a large-blast-radius tool, mostly vibe-coded, made trustworthy by a rules file, 13 reusable helpers, 48 unit + 2 integration tests, and a full set of runtime guardrails.

---

## Where to next

- **Up:** [The full playbook](../claude-code-playbook.md) — the single-document overview this series expands. See also the [series index](./README.md).
- **Next:** [Part 2 — The core idea: speed inside guardrails](02-speed-inside-guardrails.md) — how to keep AI speed while the guardrails do the worrying, and a first look at the five pillars.
- **Related:** [Part 3 — Before you write any code](03-before-you-write-any-code.md) sizes your blast radius and sets your standards; [Part 5 — The five pillars](05-five-pillars.md) is the deep dive on the discipline introduced here; [Part 9 — Pitfalls and honest limits](09-pitfalls-and-limits.md) keeps it honest.
