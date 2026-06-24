# Speed Inside Guardrails

*Why AI speed and engineering discipline aren't enemies — and how five interlocking guardrails let you move fast without breaking a live system.*

> Part 2 of 9 in the *Vibe Coding, Safely* series.

---

This is the philosophical spine of the whole series. If you read only one part,
read this one. Everything else — the setup steps, the five pillars in detail,
the daily loop, the recipe — is downstream of a single idea:

> **You can have AI speed *and* engineering discipline at the same time. They're
> not opposites. The discipline is what *lets* you keep the speed.**

The rest of this document earns that claim. We'll start in plain language, build
up the central analogy, lay out the five guardrails and show how they reinforce
each other, and then — in clearly-marked sections for engineers — go deep on the
mechanics. Throughout, the worked example is the same one used across the series:
a toolkit that migrates a company's data from **Asana** (a work-tracking tool)
to **Jira** (another one). It reads everything out of Asana, stores it locally,
and then **creates real items in a live Jira account**. That last part is the
scary part, and it's exactly why this example is worth learning from.

---

## Table of contents

1. [The false choice: fast OR safe](#1-the-false-choice-fast-or-safe)
2. [Seatbelts let you drive fast](#2-seatbelts-let-you-drive-fast)
3. [The whole philosophy in one line](#3-the-whole-philosophy-in-one-line)
4. [The five-pillar map](#4-the-five-pillar-map)
5. [How the pillars layer (and interlock)](#5-how-the-pillars-layer-and-interlock)
6. [Match guardrail effort to blast radius](#6-match-guardrail-effort-to-blast-radius)
7. [The cost and benefit of each pillar](#7-the-cost-and-benefit-of-each-pillar)
8. [Discipline as a force multiplier](#8-discipline-as-a-force-multiplier)
9. [Scaling: from a five-line script to a high-stakes tool](#9-scaling-from-a-five-line-script-to-a-high-stakes-tool)
10. [For engineers: the guardrails in this codebase](#10-for-engineers-the-guardrails-in-this-codebase)
11. [Key takeaways](#11-key-takeaways)
12. [Where to next](#where-to-next)

---

## 1. The false choice: fast OR safe

There's a story people tell themselves about AI-assisted coding, and it goes
like this:

> *"There are two kinds of people. The fast ones who let the AI rip and ship
> whatever it produces, and the careful ones who slow everything down with
> process, reviews, and tests. Pick a lane."*

It's a tidy story. It's also wrong — and believing it is the single most
expensive mistake a newcomer can make.

Let's name the two caricatures plainly, because each one is a real trap:

- **The reckless lane.** You describe what you want, the AI writes it, you run
  it, it seems to work, you move on. This feels incredible — for a while. The
  cost is invisible right up until it isn't: a confident, wrong piece of code
  does something you didn't intend, and because you never built a way to *catch*
  mistakes, you find out from the damage. In our example that means thousands of
  wrong tickets in a live system that real people now have to clean up by hand.

- **The bureaucratic lane.** You're so worried about that outcome that you wrap
  every change in heavyweight process — long reviews, sign-offs, manual checks of
  everything. You're safe, but you've thrown away the entire reason you reached
  for an AI assistant. You're now slower than you were before, and frustrated
  enough that you'll eventually abandon the discipline and snap back to the
  reckless lane.

The trap is the assumption underneath *both* lanes: that **safety is friction**,
a tax you pay out of your speed budget. If that were true, you really would have
to choose.

But the good kind of safety isn't friction you pay continuously. It's
**infrastructure you build once that then runs by itself.** A test you write
today checks your code automatically *forever*, every time, in a fraction of a
second, without you thinking about it. A rule you write down once is obeyed on
every future turn without you re-typing it. A confirmation prompt you add once
guards every future run. You pay a small, one-time cost to install each
guardrail, and from then on it gives back speed — because you can now move
quickly *and* trust the result.

That's the reframe this whole series rests on. Fast-or-safe is a false choice.
The real choice is: *do you install the cheap, automatic safety that lets you
stay fast, or do you skip it and pay later in cleanup and fear?*

---

## 2. Seatbelts let you drive fast

Here's the analogy worth keeping in your head.

Imagine you're handed a very fast car. You *could* drive it slowly out of fear —
that's the bureaucratic lane. You *could* drive it fast with no safety
equipment — that's the reckless lane. Or you could do what every actual race
team does: install excellent safety equipment **so that you can use the speed.**

Think about what a race car actually has. A seatbelt. A roll cage. A fire
suppression system. Mirrors. A pit crew watching telemetry. Run-off areas beside
the track. None of these make the car *slower*. They're not a tax on the speed —
they're the **precondition** for using it. A race driver doesn't think "I'd go
faster without a seatbelt." The seatbelt is *why* they're willing to take the
corner at full commitment. Remove the safety equipment and the rational move
isn't to go faster — it's to go *slower*, because now every mistake is
catastrophic.

This is exactly the relationship between guardrails and AI speed:

| In the car | In AI-assisted coding | What it actually buys you |
|---|---|---|
| Seatbelt / roll cage | **Tests** (Pillar 3) | Crash without dying — a mistake fails a test, not a customer |
| "Are you sure?" before launch | **`--dry-run` + confirmation** (Pillar 4) | See the consequence before it's permanent |
| The driver's house rules / setup sheet | **Rules file `CLAUDE.md`** (Pillar 1) | Every run starts from the same known-good standards |
| Pre-built, trusted parts | **Reusable helpers** (Pillar 2) | You're assembling proven components, not machining new ones each lap |
| A cockpit anyone on the team can operate | **Interactive CLI** (Pillar 5) | The person driving can't fat-finger a catastrophe |

The mental flip is this: **stop thinking of guardrails as the brakes and start
thinking of them as the thing that lets you take your foot off the brake.** When
you have a test suite that catches regressions in two seconds, you *accept the
AI's fast code more readily*, because you have a cheap, instant way to know if
it's wrong. When you have a `--dry-run` mode, you *try bold things*, because
trying costs nothing. The guardrails don't slow the loop down; they're what make
a fast loop survivable.

A newcomer worry, addressed head-on: *"But building all this safety stuff sounds
like a lot of work — isn't that itself slow?"* Two answers. First, you don't
build it all on day one (see [Section 9](#9-scaling-from-a-five-line-script-to-a-high-stakes-tool)) —
you add guardrails as the stakes rise. Second, and more importantly: **you're not
the one building them.** You ask Claude to. "Add a `--dry-run` mode and a
confirmation prompt." "Write a test for the empty-input case." "Pull this
repeated logic into a shared helper." The AI that gives you the speed also builds
the seatbelts. That's the quiet magic of the whole approach.

---

## 3. The whole philosophy in one line

If you compress this document to a single sentence, it's this:

> **AI speed *with* engineering discipline.**

Read it as one phrase, not two. The word that matters most is *with*. Not
"speed, but be careful." Not "discipline, and try to be quick." The two halves
hold each other up:

- **Speed without discipline** collapses into the reckless lane. It's thrilling
  and then it's a disaster.
- **Discipline without speed** is just old-fashioned slow software development —
  in which case, why did you reach for an AI assistant at all?
- **Speed *with* discipline** is the thing that's genuinely new and genuinely
  good. You get the AI doing the heavy lifting *and* an output you can trust,
  maintain, and hand to other people.

The discipline, in this method, isn't vibes or willpower. It's concrete: it's
**five pillars**, each of which is a guardrail you install once and benefit from
forever. The next section maps them.

---

## 4. The five-pillar map

Here are the five guardrails, in plain English, with the "seatbelt" each one
provides. (Each gets its own deep-dive doc later in the series; this is the map,
not the territory.)

| Pillar | Plain-English job | "Seatbelt" it provides |
|---|---|---|
| **1. A rules file (`CLAUDE.md`)** | Tells Claude your standards, every time | Consistency; you never re-explain the house rules |
| **2. Reusable helpers** | Solve each hard problem once, then reuse it | Fewer bugs; new features are cheap, low-risk wiring |
| **3. Tests** | Automatic checks that the code still works | Mistakes get caught before users (or live systems) do |
| **4. Runtime guardrails** | "Are you sure?" + safe retries + reversibility | Protects the live system at the dangerous moment |
| **5. Interactive CLI** | A guided menu instead of cryptic commands | Anyone can run it safely; fewer fat-finger mistakes |

A few things to notice before we go deeper.

**None of these is exotic.** A rules file is a text file. A helper is a small,
well-named file of shared code. A test is a tiny program that checks another
program. A runtime guardrail is often a single `if` that asks "are you sure?". An
interactive CLI is a numbered menu. There's nothing here a beginner can't have
Claude build in an afternoon.

**You don't need all five on day one.** A read-only script that exports a
spreadsheet barely needs Pillar 4 at all. The art is adding pillars *as the
stakes rise* — which is the subject of [Section 6](#6-match-guardrail-effort-to-blast-radius)
and [Section 9](#9-scaling-from-a-five-line-script-to-a-high-stakes-tool).

**They're ordered roughly by when you adopt them**, and roughly from "cheap and
always worth it" (a rules file) toward "only worth it when the stakes justify it"
(a full interactive CLI). That ordering is itself a guardrail: it stops you
gold-plating a throwaway script.

> For the full, worked-example treatment of all five pillars, see
> [05-five-pillars.md](05-five-pillars.md). This document is about *why they
> exist and how they fit together*; that one is the detailed how.

---

## 5. How the pillars layer (and interlock)

The five pillars aren't a checklist of unrelated good habits. They **stack**, and
they **interlock** — each one strengthens the others, and a weakness in one is
partly covered by the next. Picture them as layers wrapping the dangerous core
(touching the live system), from the outside in:

```
   ┌──────────────────────────────────────────────────────────────┐
   │  Pillar 1: RULES FILE (CLAUDE.md)                              │
   │  Sets the standards the AI follows on every single turn.       │
   │  "stdlib only", "tests next to code", "never commit w/o me".   │
   │                                                                 │
   │   ┌────────────────────────────────────────────────────────┐  │
   │   │  Pillar 2: REUSABLE HELPERS (src/lib/)                   │  │
   │   │  Hard problems solved ONCE, then reused everywhere.      │  │
   │   │  Talking to Asana, talking to Jira, the database...      │  │
   │   │                                                          │  │
   │   │   ┌──────────────────────────────────────────────────┐  │  │
   │   │   │  Pillar 3: TESTS (tests/)                         │  │  │
   │   │   │  Automatic proof each helper & script behaves.    │  │  │
   │   │   │  Run them after every change → green = go.        │  │  │
   │   │   │                                                    │  │  │
   │   │   │   ┌────────────────────────────────────────────┐ │  │  │
   │   │   │   │  Pillar 4: RUNTIME GUARDRAILS              │ │  │  │
   │   │   │   │  --dry-run, confirm prompts, retries,      │ │  │  │
   │   │   │   │  fallbacks, idempotency, scope checks.     │ │  │  │
   │   │   │   │                                            │ │  │  │
   │   │   │   │   ┌────────────────────────────────────┐  │ │  │  │
   │   │   │   │   │  THE DANGEROUS CORE                │  │ │  │  │
   │   │   │   │   │  Actually CREATING items in a      │  │ │  │  │
   │   │   │   │   │  live Jira. The irreversible act.  │  │ │  │  │
   │   │   │   │   └────────────────────────────────────┘  │ │  │  │
   │   │   │   └────────────────────────────────────────────┘ │  │  │
   │   │   └──────────────────────────────────────────────────┘  │  │
   │   └────────────────────────────────────────────────────────┘  │
   │                                                                 │
   │  Pillar 5: INTERACTIVE CLI wraps the whole thing in a guided    │
   │  menu, and surfaces Pillar 4's prompts right in the flow.       │
   └──────────────────────────────────────────────────────────────┘
```

Read it from the outside in and you can see how each layer hands off to the next:

1. **Rules (Pillar 1)** govern *how all the inner layers get built*. Because the
   rules say "standard library only" and "tests next to the code," the helpers and
   tests inside come out consistent. The outermost layer shapes everything it
   contains.

2. **Helpers (Pillar 2)** are *what the inner layers are made of*. The script that
   touches the dangerous core isn't a thousand lines of fresh, risky code — it's
   mostly *wiring together* helpers that already exist and are already trusted.

3. **Tests (Pillar 3)** *prove the helpers and scripts behave* — including in the
   nasty edge cases (rate limits, pagination, delete/replace logic) where bugs
   hide. They turn "I think this works" into "this is checked, automatically,
   every time."

4. **Runtime guardrails (Pillar 4)** are the *last line of defense around the
   irreversible act itself*. Tests check the code in a sandbox; guardrails protect
   you in the live run, the one moment the sandbox can't reach.

5. **The interactive CLI (Pillar 5)** wraps all of it so a human operator can
   drive safely — and it deliberately *re-exposes Pillar 4's prompts* inside the
   menu, so the safety isn't something you have to remember to invoke.

Now the interlocking — why these are mutually reinforcing rather than redundant:

- **Helpers make tests cheaper, and tests make helpers safer.** Because rate-limit
  handling lives in *one* helper, you only have to test it *once* — and that one
  battle-tested helper is then trusted everywhere it's reused. The two pillars pay
  each other's costs.
- **Rules make tests happen at all.** The rule "tests live next to the code; aim
  for 90%+ coverage" is what keeps Pillar 3 from quietly eroding when you're
  moving fast. A pillar that enforces another pillar.
- **The CLI enforces the runtime guardrails.** A `--dry-run` flag only protects
  you if someone remembers to pass it. The menu (Pillar 5) bakes the dry-run and
  the confirmation prompt into the flow, so Pillar 4 protects even the operator
  who never read the docs.
- **Guardrails are only trustworthy because of tests.** A "retry when
  rate-limited" guardrail that's subtly broken is worse than none, because you'd
  *trust* it. The test that exercises the retry logic is what makes the guardrail
  something you can actually lean on.

That mutual reinforcement is the point: a gap in one layer is partly caught by
the next. Miss a rule? A test may still catch the consequence. Skip a
confirmation flag on the command line? The menu adds it back. The layers overlap
on purpose. **Defense in depth** is the engineering name for it — and it's why
the *combination* is far stronger than any pillar alone.

---

## 6. Match guardrail effort to blast radius

Here's the question that tells you *how many* of the five pillars you need, and
how much to invest in each:

> **What's the worst thing this tool could do if it went wrong?**

Engineers call that worst case the **blast radius** — how far the damage spreads
if something explodes. The single most useful instinct in this whole method is to
**match the guardrail effort to the blast radius.** Light stakes, light
guardrails. Heavy stakes, heavy guardrails. Over-protecting a harmless script
wastes effort; under-protecting a dangerous one is how disasters happen.

Walk it along a spectrum:

| If the tool's worst case is... | Blast radius | Guardrails you actually need |
|---|---|---|
| It prints the wrong number to your screen | Tiny | Read it, fix it, move on. A test if you'll reuse it. |
| It writes a wrong local file you can delete | Small | Tests; maybe a confirmation before overwriting. |
| It reads a live system but writes nothing | Medium-low | Tests; respect rate limits so you don't get blocked. |
| It **creates real items in a live system** | **High** | The full stack: tests, dry-run, confirmation, retries, fallbacks, idempotency, scope guards. |
| It deletes or overwrites live production data | Severe | All of the above, plus backups, staged rollout, and a tested rollback path. |

Our example sits at **High**, and that placement is the whole reason it carries
so many guardrails. Trace the logic:

- It *reads* everything from Asana and stores it in a **local SQLite database** (a
  single file on your disk, `data/asana.sqlite`). If that part goes wrong, you
  delete the file and re-run. Low blast radius. So the read side leans mostly on
  tests, not on heavy runtime protection.
- Then it **creates matching items in a live Jira account.** This is
  irreversible-ish (you'd have to find and delete every wrong ticket by hand) and
  it's *public* — other people see those tickets. A careless run could create
  thousands of wrong issues. High blast radius. So the write side gets the *full*
  Pillar 4 treatment: dry-run, confirmation, retry, fallback, idempotency, and
  scope guards.

Notice that **the same project has different blast radii in different places**,
and the guardrails are deployed accordingly. You don't apply maximum paranoia
uniformly. You spend your safety budget where the explosion would be biggest.
That's not cutting corners — it's *engineering judgment*, and it's what keeps the
method fast: you're not paying for protection you don't need.

The practical move: before you build anything that touches the outside world,
write one sentence — *"The worst this tool can do is ___."* — and let that
sentence set your guardrail budget. ([03-before-you-write-any-code.md](03-before-you-write-any-code.md)
turns this into a pre-flight habit.)

---

## 7. The cost and benefit of each pillar

Every guardrail has a price. Being honest about the price is what keeps this
method from turning into the bureaucratic lane. The trick is that for each
pillar, **the cost is paid once and the benefit recurs forever** — which is why
the math comes out so strongly in favor.

| Pillar | What it costs you | What it pays back | When it's worth it |
|---|---|---|---|
| **1. Rules file** | A few minutes to seed (`/init` does most of it); occasional "add that to `CLAUDE.md`" follow-ups | Every future turn inherits your standards; you stop being the rule police | Always. Cheapest pillar, biggest leverage. |
| **2. Helpers** | A little extra thought to extract shared logic instead of copy-pasting | Each hard problem solved once; new features become small, low-risk compositions | The moment you write similar code a *second* time. |
| **3. Tests** | Time to write the test alongside each feature/fix | Instant, automatic proof nothing broke; you can accept fast AI code with confidence | Anything you'll run more than once, or that matters if it's wrong. |
| **4. Runtime guardrails** | Engineering the dry-run, prompts, retry, idempotency | The live system is protected at the exact moment of risk | Whenever the tool changes a live or shared system. |
| **5. Interactive CLI** | Building and maintaining a menu layer | Non-experts can operate it safely; fat-finger errors drop sharply | When people other than its author will run it, *and* the stakes are real. |

The asymmetry to internalize: **the cost is one-time and small; the benefit is
ongoing and compounding.** You write a test once; it runs thousands of times. You
write a rule once; it's obeyed on every session. You extract a helper once; every
future feature that reuses it is cheaper *and* safer. This is why the math of
guardrails is so lopsided — and why "skip the guardrails to go faster" is almost
always a bad trade. You save a few minutes today and pay it back many times over
in cleanup, fear, and re-explaining.

The flip side, kept honest: a guardrail you don't need *is* pure cost. A full
interactive CLI around a script only you will ever run, once, is wasted effort —
that's the bureaucratic lane sneaking in. This is precisely why
[Section 6](#6-match-guardrail-effort-to-blast-radius) comes first: the blast
radius tells you which of these costs are worth paying.

---

## 8. Discipline as a force multiplier

The deepest idea in this document is also the least obvious one, so let's state
it directly:

> **Discipline doesn't slow the AI down. It multiplies what the AI can safely
> do.**

A *force multiplier*, in plain terms, is something that makes the same effort
produce a much larger result. A small lever moves a heavy rock. The discipline in
this method is a lever on the AI's speed. Here's the mechanism, step by step:

1. **An undisciplined fast AI produces output you can't trust.** So you have to
   slow down and check everything by hand — which cancels the speed. Net result:
   medium speed, low confidence.

2. **A disciplined fast AI produces output you *can* trust**, because the rules
   shaped it, the helpers are proven, the tests check it, and the guardrails
   contain it. So you *don't* have to hand-check everything. You skim, you trust
   the green checkmark, you move on. Net result: high speed, high confidence.

The discipline isn't competing with the speed for your time budget. It's what
*unlocks* the speed by removing the slow, fearful hand-verification that
undisciplined AI work forces on you. Every guardrail you add lets you say "yes"
to the AI faster, because you've pre-built the mechanism that would catch it if
it's wrong.

There's a compounding effect on top. Because the AI re-reads your rules file and
your docs every session, **good discipline makes the AI's *next* contribution
better too.** Write down a convention once and every future suggestion respects
it. Keep a running catalog of your commands in the rules file and the AI proposes
new ones that fit the existing pattern. The discipline you install today doesn't
just protect today's work — it raises the floor on all future work. The lever
gets longer over time.

This is the resolution of the false choice from Section 1. The careful people
aren't slow *because* they're careful — they're slow when their care takes the
form of manual, repeated, human effort. Move that care into *automatic
infrastructure* — rules, helpers, tests, guardrails, a guided menu — and care
stops being a tax on speed and becomes a multiplier of it.

---

## 9. Scaling: from a five-line script to a high-stakes tool

A fair beginner worry: *"This all sounds heavy. I just want to write a little
script."* Good — you should. The five pillars are not an all-or-nothing
ceremony you perform before writing your first line. They're a **growth path**,
adopted in order as a project earns complexity. Watch the same idea scale from
trivial to high-stakes:

**Stage 0 — A five-line throwaway.** You ask Claude for a script that counts the
rows in a CSV. Blast radius: it prints a wrong number. Guardrails needed: roughly
none. Read the output, done. *Adding tests and a confirmation prompt here would
be the bureaucratic lane.* Match the effort to the stakes — and the stakes are
nil.

**Stage 1 — A read-only export you'll reuse.** Now it exports a spreadsheet of
users and their activity, and you'll run it for several clients. Blast radius:
still small (a wrong local file), but now it *matters* if it's wrong and you'll
run it repeatedly. Adopt **Pillar 1** (a short rules file so Claude is consistent)
and **Pillar 3** (a test or two, especially for the empty-input and
multiple-page cases). Cheap, and now you trust re-runs.

**Stage 2 — Logic starts repeating.** A second script needs to talk to the same
API and handle the same rate-limit response. The moment you'd copy-paste that
logic, adopt **Pillar 2**: extract it into a shared helper, test the helper once,
reuse it everywhere. Both scripts get safer at once, and the next script that
needs it is nearly free.

**Stage 3 — You touch the live system.** Now the tool stops only reading and
starts *creating items in live Jira*. The blast radius jumps to **High**, so the
guardrails jump with it. Adopt **Pillar 4** in the *same step* you add the write
capability — never after. Dry-run so you can preview, a confirmation prompt so
nothing permanent happens without a "yes," retry so a rate limit doesn't lose
your work, idempotency so an interrupted run *heals* on re-run instead of
duplicating, scope guards so you can't accidentally target the whole company.

**Stage 4 — Other people run it.** Once non-engineers operate the tool, a wrong
command becomes bad data in a live system. Adopt **Pillar 5**: wrap everything in
a guided menu that walks the operator through each step and surfaces the
confirmations inline. Accessibility becomes a *safety* feature.

The lesson: **the pillars are a ladder, and you climb a rung when the project
earns it.** The blast radius is your guide for which rung you're on. This is how
the method stays fast — you are never paying for protection the current stakes
don't justify — while still arriving, by the time you're doing something
genuinely dangerous, with the full set of seatbelts fastened. The order is not
arbitrary: read-only before write, tests before scale, guardrails before the big
run. ([08-recipe-for-your-own-project.md](08-recipe-for-your-own-project.md)
turns this ladder into a step-by-step recipe.)

---

## 10. For engineers: the guardrails in this codebase

Everything above is true at the level of principle. Here's where it's true at the
level of code, with the specifics from this repo so you can see what "good" looks
like in practice. (Each pillar has its own deep-dive in
[05-five-pillars.md](05-five-pillars.md); this is the engineer's spot-check that
the philosophy is actually load-bearing here.)

**Pillar 1 — the contract is a real file and it's binding.** The rules live in
`AGENTS.md` (with `CLAUDE.md` symlinked to it, so both names resolve to the same
contract). It's read automatically every session. Among its enforced rules:
`python3`, never bare `python`; CPython 3.11+; **standard library only**; tests
mirror `src/` two levels deep; ≥90% statement coverage; Conventional Commits; and
the hard one — *never create a commit without explicit human approval.* The rules
aren't aspirational. You can watch one hold in the network code: the contract
says "stdlib only," and `src/lib/http_client.py` opens with built-ins only —
`json`, `ssl`, `time`, and `urllib` — rather than the popular third-party
`requests`. The constraint is visible in the imports.

**Pillar 2 — decide once, reuse everywhere.** `src/lib/` holds **13 helper
modules**, imported **181 times across 46 scripts**. That ratio is the point:
the average script is mostly *composition* of trusted parts. `config.py` resolves
credentials with fallbacks; `http_client.py` and `jira_client.py` centralize all
API talk including rate-limit retry; `database.py` owns persistence; `adf.py`
converts text to Jira's rich-text format; `team_picker.py` is the searchable
"choose a team" menu. Because rate-limit handling lives in one place, it's tested
in one place and trusted in 46.

**Pillar 3 — the safety net is real and sized.** **48 unit test files** plus **2
integration test files**, against a **≥90% coverage target**, run with
`python3 -m unittest discover tests`. They concentrate on the edge cases that bite:
rate-limit header parsing, pagination (fetch every page, don't over-fetch),
delete/replace logic (mark old comments removed rather than duplicate or lose
them), and per-field-type format conversions before anything is sent to Jira. The
governing habit: **every bug fix ships with a regression test**, converting a
one-time fix into a permanent guarantee.

**Pillar 4 — the live-write protections.** The high-blast-radius creation path
carries the full set, all worth copying into your own high-stakes tools:

- `--dry-run` — print exactly what *would* be sent to Jira; change nothing.
- A confirmation prompt before creating issues, skippable with `--yes` for
  automation once you trust the run.
- **429 retry + requeue** — beyond the Jira client's own jittered backoff, a
  creation still rate-limited is requeued to the end of the run and retried over
  several more rounds before being recorded as an error (and errored items stay
  unmapped, so a re-run retries them).
- **Assignee fallback** — if Jira rejects a create because the assignee can't be
  assigned in that project, the create is retried once with the assignee
  stripped, and recorded as created-with-a-note rather than failing the batch.
- **`--clear-first` two-pass mode** — when repairing a field, clear it on every
  in-scope issue first, *then* set the new value, so no stale half-updated value
  survives if the set pass misses one.
- **Idempotent re-runs** — created items are persisted in a mappings table and
  skipped on re-run, so an interrupted migration *heals* instead of duplicating.
- **Scope validation** — contradictory targeting (e.g., one project *and* all
  teams) is refused, so you can't accidentally aim at the whole company.

**Pillar 5 — the guided front door.** `scripts/interactive_cli.py` is a numbered
menu (Initialization, Tasks, Atlassian/Jira, Reports, Credentials) that walks the
operator through each step, runs a credentials setup flow on first launch if no
tokens are found, offers searchable pickers, and remembers field mappings so the
second run is easier than the first. Critically, it *re-exposes the Pillar 4
prompts inline* — the dry-run and confirmation are part of the flow, not
something the operator has to know to ask for.

The takeaway for engineers: the five pillars in this repo aren't decoration. The
import ratio, the coverage target, the retry/requeue/fallback logic, and the
menu's inline confirmations are the mechanism by which a stdlib-only Python tool
can safely *create thousands of items in someone's live Jira* — fast.

---

## 11. Key takeaways

- **Fast-or-safe is a false choice.** The good kind of safety is one-time
  infrastructure that runs by itself, not continuous friction. You can have both.
- **Guardrails are seatbelts, not brakes.** They don't slow you down — they're
  what let you commit to AI speed, because a mistake fails a test instead of a
  customer.
- **The philosophy is one phrase: *AI speed with engineering discipline.*** The
  *with* is the whole point; the two halves hold each other up.
- **Five pillars deliver the discipline:** a rules file, reusable helpers, tests,
  runtime guardrails, and an interactive CLI.
- **The pillars layer and interlock.** Rules shape the helpers; helpers make up
  the scripts; tests prove the helpers; runtime guardrails protect the live act;
  the CLI wraps it all and re-exposes the guardrails. A gap in one is partly
  covered by the next — defense in depth.
- **Match guardrail effort to blast radius.** Write the "the worst this can do
  is ___" sentence first; let it set how many pillars you need. Over-protecting a
  harmless script is the bureaucratic lane in disguise.
- **Every pillar's cost is one-time; its benefit recurs.** That asymmetry is why
  the math almost always favors installing the guardrail.
- **Discipline is a force multiplier.** It removes the slow hand-verification that
  undisciplined AI work forces on you, so you can say "yes" to the AI faster — and
  it compounds, because the AI re-reads your rules and docs every session.
- **The pillars are a ladder you climb as the project earns it** — read-only
  before write, tests before scale, guardrails before the big run.

---

## Where to next

- **Previous:** [01-what-is-vibe-coding.md](01-what-is-vibe-coding.md) — what
  "vibe coding" means in this series, and the disciplined version of it.
- **Next:** [03-before-you-write-any-code.md](03-before-you-write-any-code.md) —
  the pre-flight: naming the blast radius, deciding your standards, and knowing
  the tools you'll touch.
- **Series index:** [README.md](./README.md)
- **Parent playbook:** [../claude-code-playbook.md](../claude-code-playbook.md) —
  the full long-form companion this series expands.

*Other parts:*
[04-getting-set-up.md](04-getting-set-up.md) ·
[05-five-pillars.md](05-five-pillars.md) ·
[06-the-daily-loop.md](06-the-daily-loop.md) ·
[07-deciding-what-not-to-build.md](07-deciding-what-not-to-build.md) ·
[08-recipe-for-your-own-project.md](08-recipe-for-your-own-project.md) ·
[09-pitfalls-and-limits.md](09-pitfalls-and-limits.md)
