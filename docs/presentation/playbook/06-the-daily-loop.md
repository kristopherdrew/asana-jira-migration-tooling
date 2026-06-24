# The daily loop: how to actually work with Claude

*The eight-step rhythm that turns "the AI wrote some code" into "I shipped
something I can trust" — plus a prompting playbook, the anti-patterns that bite
beginners, and how to recover when Claude goes wrong.*

> Part 6 of 9 in the Vibe Coding, Safely series

---

The earlier parts of this series gave you the *structure*: the blast radius you
named ([Part 3](03-before-you-write-any-code.md)), the setup you did
([Part 4](04-getting-set-up.md)), and the five pillars that hold the whole thing
up ([Part 5](05-five-pillars.md)). This part is the *motion*. It's the loop you
actually repeat, dozens of times a day, when you sit down with Claude Code and
build something real.

If Part 5 is the gym equipment, Part 6 is the workout. And like a workout, the
value is almost entirely in doing the same small, boring thing correctly, over
and over, instead of one heroic burst. The loop is deliberately unexciting. That
is the point: an unexciting loop is one that doesn't surprise you with a
thousand wrong tickets in a client's live Jira.

We'll use the same running example throughout — the **Asana→Jira migration
toolkit**, a Python tool that reads a company's Asana data and creates the
matching items in a live Jira. When we need a concrete, low-stakes task to walk
through, we'll build a small one together: *a command that exports every user
and their activity counts to a spreadsheet.* (That command really exists in the
toolkit — it's `export_users.py` — which lets us talk about real inputs and
outputs instead of hand-waving.)

---

## Table of contents

- [The loop at a glance](#the-loop-at-a-glance)
- [Why a loop at all?](#why-a-loop-at-all)
- [The eight steps, in depth](#the-eight-steps-in-depth)
  - [Step 1 — Describe the goal in plain English](#step-1--describe-the-goal-in-plain-english)
  - [Step 2 — Ask for a plan first (on anything non-trivial)](#step-2--ask-for-a-plan-first-on-anything-non-trivial)
  - [Step 3 — Let it write the code *and* the test](#step-3--let-it-write-the-code-and-the-test)
  - [Step 4 — Run the tests](#step-4--run-the-tests)
  - [Step 5 — Try it for real, in dry-run](#step-5--try-it-for-real-in-dry-run)
  - [Step 6 — Review the change](#step-6--review-the-change)
  - [Step 7 — Commit, only when you approve](#step-7--commit-only-when-you-approve)
  - [Step 8 — Repeat](#step-8--repeat)
- [Plan mode vs. acting: when to use which](#plan-mode-vs-acting-when-to-use-which)
- [The prompting playbook](#the-prompting-playbook)
  - [Ask for explanations](#ask-for-explanations)
  - [Ask for the safe version](#ask-for-the-safe-version)
  - [Ask it to find its own gaps](#ask-it-to-find-its-own-gaps)
  - [Ask for options with trade-offs](#ask-for-options-with-trade-offs)
  - [Ask it to record the correction in `CLAUDE.md`](#ask-it-to-record-the-correction-in-claudemd)
  - [Prompt hygiene: small, specific, one thing at a time](#prompt-hygiene-small-specific-one-thing-at-a-time)
- [Anti-patterns: the four ways the loop breaks](#anti-patterns-the-four-ways-the-loop-breaks)
- [When Claude goes wrong: recovery moves](#when-claude-goes-wrong-recovery-moves)
- [A worked session, end to end](#a-worked-session-end-to-end)
- [The session checklist](#the-session-checklist)
- [Key takeaways](#key-takeaways)
- [Where to next](#where-to-next)

---

## The loop at a glance

Here's the whole rhythm in one picture. Everything below is an expansion of it.

```
        ┌───────────────────────────────────────────────────────────┐
        │                                                           │
        ▼                                                           │
  (1) DESCRIBE the goal in plain English                            │
        │   inputs & outputs, not "how"                             │
        ▼                                                           │
  (2) PLAN first  ── for anything non-trivial ──┐                   │
        │   Claude researches, proposes approach │                  │
        │   you fix the PLAN (cheap), not code   │                  │
        ▼   ◄────── revise plan ◄────────────────┘                  │
  (3) BUILD  code + test together                                   │
        │                                                           │
        ▼                                                           │
  (4) TEST   python3 -m unittest discover tests                     │
        │        ├── not OK ──► back to (3): fix + add a test       │
        ▼  OK                                                       │
  (5) DRY-RUN   see exactly what it WOULD do — changes nothing      │
        │        ├── looks wrong ──► back to (3)                    │
        ▼  looks right                                              │
  (6) REVIEW    skim the diff · ask for explanations · code review  │
        │        ├── surprising ──► ask, then back to (3)           │
        ▼  satisfied                                                │
  (7) COMMIT    only with YOUR explicit approval (the undo button)  │
        │                                                           │
        └──────────────────► (8) REPEAT ───────────────────────────┘
```

The arrows that loop *backward* are not failures — they are the loop working.
Most of your turns will bounce between steps 3–6 several times before you reach
a commit. That's healthy. The expensive mistake is letting a problem skip *past*
a gate (shipping a test failure, committing un-reviewed code, doing a live run
you never dry-ran).

---

## Why a loop at all?

A beginner's instinct is to treat Claude like a vending machine: insert a
request, receive finished code, paste it, move on. That works right up until it
doesn't — and when it doesn't, you've already built ten more things on top of
the broken one, so the failure is buried and expensive.

The loop exists to **catch problems at the cheapest possible moment**. Each step
is a gate, and each gate is cheaper than the one after it:

- Fixing a **plan** costs a sentence.
- Fixing **code before tests** costs a re-prompt.
- Catching a bug **in the tests** costs minutes.
- Catching it **in a dry-run** costs a careful read of some output.
- Catching it **in a live run** costs an apology to a client and a frantic
  afternoon — if it's fixable at all.

This is the same idea as the blast-radius tiers from
[Part 3](03-before-you-write-any-code.md), seen from the other end. There, you
sized your *defenses* to the worst outcome. Here, you're spending your *attention*
where it's cheapest, so the worst outcome never arrives. The loop is how the five
pillars get *used*: tests (Pillar 3) power step 4, runtime guardrails (Pillar 4)
power step 5, the rules file (Pillar 1) shapes every prompt, and reusable helpers
(Pillar 2) are what Claude wires together in step 3.

---

## The eight steps, in depth

### Step 1 — Describe the goal in plain English

You are the one who knows *what* should happen. Claude is very good at *how*. So
your job in step 1 is to describe the destination, not the route.

The single most useful framing is **inputs and outputs**:

- *What goes in?* (A team? A whole workspace? A CSV file? Nothing but the local
  database?)
- *What comes out?* (A spreadsheet on disk? Tickets in Jira? A printed summary?)
- *What must NOT happen?* (Don't touch Jira. Don't overwrite the existing file.
  Don't run against more than one team.)

For our worked example, a good plain-English goal looks like this:

> "I want a command that exports every user and their activity counts to a
> spreadsheet. Input: nothing but our local database. Output: a CSV in the
> `exports/` folder. For each user I want their name, email, whether they're
> active, how many tasks they created and completed, how many tasks they
> commented on, and their last active date. It should not touch Asana or Jira at
> all — it reads only what we've already synced locally."

Notice what that prompt does and doesn't contain:

- It is **specific about inputs and outputs**. "Every user," "a CSV in
  `exports/`," and the exact columns. Claude doesn't have to guess the shape of
  the result.
- It **names the blast radius out loud** — "should not touch Asana or Jira." For
  a read-only (Tier-1/Tier-2) task that's reassurance; for a Tier-3 task it's a
  guardrail you're explicitly requesting.
- It says **nothing about *how*.** It doesn't mention SQL, file handles, or the
  `csv` module. You don't need to know those, and pre-specifying them often makes
  the result worse, not better.

> **For engineers.** "Inputs and outputs, not how" is just specifying the
> contract and leaving the implementation to the agent — with one twist: because
> the agent re-reads `CLAUDE.md` every turn, a lot of the "how" is *already*
> pinned (stdlib only, `python3`, tests next to code, `--workers` override,
> respect `skip_ssl_verification`). You're describing behavior against a known
> set of house rules, which is why these prompts can stay short.

A quick rule: if you find yourself writing a paragraph of *implementation*
detail, stop — that paragraph belongs in step 2 (the plan), where Claude
proposes the implementation and you react to it, rather than in step 1, where
you'd be guessing.

### Step 2 — Ask for a plan first (on anything non-trivial)

This is the step beginners skip and engineers swear by. **Claude Code has a plan
mode**: instead of immediately editing files, it *researches your codebase* —
reads the relevant modules, checks how similar things are already done, looks at
the existing tests — and then **proposes an approach before changing a single
line.** You read the plan, correct it, and only then let it build.

Why this matters so much:

- **A plan is the cheapest thing to fix.** If the approach is wrong — it's about
  to reinvent a helper you already have, or it misunderstood where the data lives
  — you find out in a paragraph you can correct in one sentence. Fixing the same
  misunderstanding *after* it has written 300 lines and a test suite is far more
  expensive.
- **It surfaces assumptions early.** A good plan says "I'll resolve each user's
  Atlassian account id from the `user_atlassian_accounts` table; users without a
  mapping will get a blank column." Now you can catch a wrong assumption *before*
  it's baked into code.
- **It keeps the work aligned with your standards.** A plan that says "I'll add a
  new HTTP client" when you already have `src/lib/http_client.py` is a plan you
  reject in one line — protecting the reusable-helpers pillar
  ([Part 5](05-five-pillars.md)) before any duplicate code exists.

How you ask for it, in plain English:

> "Before you write anything, give me a plan. How would you build this command,
> which existing helpers would you reuse, what files would you add or change, and
> what would you test? Don't edit anything yet."

What a good plan-mode response gives you back:

- the files it intends to create or change,
- which existing helpers (`config.py`, `database.py`, …) it will reuse rather
  than re-solve,
- the edge cases it noticed (users with no Atlassian mapping, users with zero
  activity, an empty database),
- the tests it intends to write.

You then react: *"Good, but the last-active date should be the most recent of
created, completed, or commented — not just created. Update the plan."* That's a
one-sentence correction to a plan, versus a re-write of finished code.

> **This series was produced via plan mode.** These deep-dive documents were
> planned before they were written: the structure, the cross-links, and what
> each part would cover were proposed and reviewed *first*, then drafted. The
> same discipline that keeps a migration safe keeps a nine-part document set
> coherent.

When can you *skip* the plan? For genuinely trivial work — "fix this typo in the
help text," "rename this variable," "add a one-line docstring." If you can hold
the entire change in your head and it touches one place, just ask for it
directly. The judgment call gets its own section below
([Plan mode vs. acting](#plan-mode-vs-acting-when-to-use-which)).

### Step 3 — Let it write the code *and* the test

Once the plan is right, let Claude build. The non-negotiable rule of this step:
**ask for the code and a test in the same breath.** Not "write the feature, then
later we'll test it." Together.

> "Implement the plan. Write the command and, alongside it, a test that covers a
> normal user with activity, a user with no Atlassian mapping (blank columns),
> and the empty-database case."

Why together, every time:

- **A feature without a test is a claim without proof.** When you didn't write
  the code line by line, the test is *how you know it works* — and keeps working
  after the next change ([Pillar 3, Part 5](05-five-pillars.md)).
- **Asking for tests shapes better code.** Code that's written to be tested tends
  to be more modular and have clearer inputs and outputs — which is exactly the
  shape your house rules already want.
- **It's the project's actual rule.** `CLAUDE.md` requires a regression test for
  every bug fix, tests living next to the code they cover, and a ≥90% coverage
  target. Asking for tests isn't extra credit; it's the contract.

Push specifically for the *unhappy paths*, because those are where bugs hide and
where AI-written code is most confidently wrong. The toolkit's existing tests are
a good model of what to ask for: they cover **rate limiting** (does the retry
read the "wait this long" header correctly?), **pagination** (does it fetch all
pages and not over-fetch?), **delete/replace logic** (does replacing comments
mark the old ones removed instead of duplicating them?), and **format
conversions** (is each Jira field type formatted correctly before it's sent?).
For our export command, the equivalent unhappy paths are the unmapped user, the
zero-activity user, and the empty database.

> **For engineers.** This is where reusable helpers pay off. A well-factored task
> ([Part 5, Pillar 2](05-five-pillars.md)) means step 3 is mostly *composition* —
> wiring `config.py`, `database.py`, and a CSV writer together — not net-new
> risky code. If Claude starts writing a second way to load credentials or talk
> to an API, that's your cue to say "reuse the existing helper instead," and
> (per the prompting playbook) to record the convention so it sticks.

### Step 4 — Run the tests

Now the cheapest automatic gate. One command:

```bash
python3 -m unittest discover tests
```

You're looking for one word at the end: **`OK`**. Green means the code does what
the tests say it should, and — just as important — that the change didn't break
anything else. A wall of text scrolling by is normal; you're scanning for `OK`
or for a `FAILED` summary at the bottom.

Claude Code can run this *for you* and read the result — you can simply say "run
the tests" and it will execute the command and report back. But know the command
yourself, because running it is the single most important habit in the whole
playbook, and you'll do it after *every* change, not just when you remember to
ask.

If the tests **fail**, that's the loop working, not a setback. Hand the failure
straight back:

> "The tests are failing — here's the output. Fix the cause, and if the failure
> revealed a case we weren't handling, add a test for it too."

Resist two temptations here:

- **Don't let Claude "fix" a test by weakening it** to match buggy code. If a
  test is failing, first decide whether the *test* or the *code* is wrong. Ask:
  "Is the test asserting the correct behavior, or is the code wrong?"
- **Don't move on with a red suite.** A failing test is a stop sign. Everything
  downstream (dry-run, review, commit) assumes a green suite beneath it.

> **For a single command's options**, `python3 -m scripts.<name> --help` shows
> you what a script accepts — useful in step 4/5 to confirm the flags you expect
> (like `--dry-run`, `--yes`, `--workers`) actually exist before you rely on
> them.

### Step 5 — Try it for real, in dry-run

Tests check the code in a sandbox with fake data. **The dry-run checks it against
reality** — your actual database, your actual credentials — but *without changing
anything.* It is the bridge between "passes its tests" and "I'd trust it on a
live system."

> **Dry-run** (plain language): a mode that shows you *exactly* what a command
> would do — every ticket it would create, every field it would set — and then
> stops, changing nothing.

For a read-only command like our user export, "dry-run" is essentially free: run
it for real against the local database and inspect the CSV it produces. Nothing
out there can be harmed, so the equivalent of a dry-run is just *running it and
looking at the output.* Open the file. Are the columns right? Are the
activity counts plausible? Is the unmapped user's Atlassian column blank as
planned?

For a **Tier-3** command — anything that writes to live Jira — the dry-run is not
optional, it is the whole ballgame. Many of the toolkit's commands support
`--dry-run`, which prints exactly what *would* be sent to Jira without sending
it, plus a confirmation prompt you can skip with `--yes`. The issue-creation
command, for instance, both supports a dry-run and, on a real run, prints:

> `About to create up to N issue(s) in Jira project <KEY>. Continue? [y/N]`

The default answer is **no**. Nothing permanent happens until you type `y`.

The discipline:

1. Run with `--dry-run` first. Read the planned output as if you were a
   suspicious auditor. Spot-check a few items end to end.
2. Only when the dry-run looks right do you run for real — and even then you
   *read* the confirmation prompt and answer it deliberately. It is a real
   decision, not a speed bump ([Part 9](09-pitfalls-and-limits.md)).
3. Consider a small real run first (the toolkit has a `--limit` flag on the
   creation command, and you can scope to a single team) before unleashing it on
   the whole company.

This step is where the runtime guardrails from
[Part 5, Pillar 4](05-five-pillars.md) earn their existence. If your tool *can't*
dry-run a dangerous action, that's a gap to close in step 3 — ask Claude to add
the dry-run mode — *before* you ever run it live.

### Step 6 — Review the change

You don't have to understand every line. You do have to **look**. The goal of
review is to convert "the AI says it did X" into "I've seen that it did X, and
nothing surprising came along for the ride."

Three layers, increasing in effort:

**1. Skim the diff.** A *diff* is the list of exactly what changed — lines added
(usually green) and removed (red). Ask Claude to show you:

> "Show me everything that changed, and give me a two-sentence summary of each
> file."

You're not auditing syntax. You're checking *scope*: did it change only the
files it said it would? Did a "small export tweak" somehow also edit the HTTP
client? Surprising scope is the single most useful thing a non-engineer can catch
by skimming.

**2. Ask for explanations of anything that looks surprising.** This is your
superpower as a non-engineer — you can just *ask*:

> "Explain what this new function does as if I'm not a programmer."

> "Why did you change `http_client.py`? I only asked for an export command."

> "Walk me through what happens if the database is empty. Show me the line that
> handles it."

If the explanation doesn't match your intent, you've found a problem at review
time — cheap — instead of in production.

**3. Use the built-in code review.** Claude Code has a code-review helper: a
second set of eyes that reads the change specifically looking for correctness
bugs and cleanups. Even if you can't evaluate its findings yourself, you can ask
Claude to triage them:

> "Run a code review on this change and tell me, in plain language, whether any
> of the findings are real problems I should fix before committing."

> **For engineers.** Treat the built-in review as a fast pre-commit pass, not a
> substitute for your own read. It's good at spotting the boring, high-frequency
> stuff — an unhandled `None`, a resource left open, a duplicated branch — which
> is exactly the stuff that's tedious for a human to catch and easy for an agent
> to flag.

### Step 7 — Commit, only when you approve

A **commit** is a saved snapshot of your whole project at one moment, with a
short message. Git keeps every commit, which makes a commit a checkpoint you can
return to — your **undo button** ([Part 3](03-before-you-write-any-code.md)).

The project's rule, written in `CLAUDE.md`, is absolute: **Claude never creates a
commit unless you explicitly approve it.** This is your *trust boundary* — the
bright line between "the AI suggests" and "the AI records something permanent on
your behalf." When the work is reviewed and you're satisfied, you say so:

> "This looks good. Commit it."

And because the rules file also mandates **Conventional Commits scoped to the
`src/` folder touched**, the message will come out structured and searchable —
something like `feat(reports): add user activity export to CSV`. You can ask
Claude to propose the message; you approve it; it commits.

Two habits make commits pull their weight:

- **Small and often.** Lots of little checkpoints give you precise places to roll
  back to. One giant end-of-day commit gives you a single blunt undo and a
  reviewer's nightmare. A good commit is *one coherent change* — the export
  command and its test, together.
- **Only good states.** A commit you can trust is one where the tests passed and
  you reviewed the diff. Combined with the approval rule, your history becomes a
  series of human-reviewed, test-passing checkpoints — exactly the history you
  want behind you when something later goes wrong.

> **For engineers.** Don't push or open a PR unless asked, and branch before
> committing if you're on the default branch — but the load-bearing rule for
> *everyone* is the human gate on commits. It's what guarantees there's no path
> where the agent quietly accumulates changes nobody signed off on.

### Step 8 — Repeat

That's the loop. Describe the next goal, plan it if it's non-trivial, build with
a test, run the suite, dry-run it, review it, commit with your approval. The
toolkit you're building grows one reviewed, tested, reversible checkpoint at a
time.

The compounding benefit: because every commit is a known-good state, you are
*never* more than one `git revert` away from safety. That's what makes it
psychologically safe to move fast — the downside is capped.

---

## Plan mode vs. acting: when to use which

The biggest single skill in the daily loop is knowing when to make Claude *think*
(plan mode) versus when to let it *act* (edit and run directly). Get this right
and the loop hums.

| Situation | Mode | Why |
|---|---|---|
| New command or feature | **Plan first** | Many files, real design choices, easy to head down the wrong path |
| Touches a Tier-3 (live-system) capability | **Plan first** | The blast radius demands you see the approach before any code exists |
| Refactor across multiple files | **Plan first** | You want to see the full scope before it sprawls |
| "I'm not sure how this should work" | **Plan first** | Use the plan as a design conversation |
| Fix a typo / rename / one-line tweak | **Act** | You can hold the whole change in your head |
| Re-run the tests, show me the diff, explain this | **Act** | Read-only or trivial; no design risk |
| Apply a correction you just described | **Act** | The thinking already happened in the conversation |

A useful mental model: **plan mode is for decisions; acting is for execution.**
If a task contains a real decision you'd want to weigh in on — *which* helper,
*where* the data comes from, *what* the edge cases are — plan it. If the decision
is already made and you're just asking for the keystrokes, act.

When in doubt on anything that writes to a live system, plan. The cost of an
unnecessary plan is thirty seconds of reading. The cost of an un-planned Tier-3
change is the whole reason this series exists.

---

## The prompting playbook

The loop tells you *when* to talk to Claude. This section is *how* — a set of
prompt patterns that consistently produce safer, clearer results. Steal them
verbatim; they're written to be copy-pasted and lightly edited.

### Ask for explanations

You can always trade a little of Claude's speed for your own understanding. It's
the cheapest insurance there is.

> "Explain what this script does as if I'm not a programmer."

> "In three sentences, what does this function do and when would it run?"

> "I see a `--workers` flag — what does it do, and what's a safe value for a
> first run?"

> "Trace what happens, step by step, when I run this against an empty database."

Use these liberally during review (step 6), but also any time a piece of the
project mystifies you. Understanding the tool you're operating is itself a safety
feature.

### Ask for the safe version

When a task touches anything permanent, ask for the guardrails *by name* in the
same prompt that asks for the feature. Don't bolt them on later.

> "Add a `--dry-run` mode that prints exactly what would be sent to Jira without
> sending it, and a confirmation prompt before it writes anything."

> "Make this safe to run twice — if it's interrupted halfway, a re-run should
> skip what it already created instead of duplicating."

> "Add a `--limit` flag so I can test with a handful of items before doing the
> whole batch."

> "Before this does anything destructive, show me a summary and require me to
> type `y` to continue."

This is [Pillar 4](05-five-pillars.md) expressed as a prompt. The pattern:
*name the dangerous action, then name the seatbelt you want around it.*

### Ask it to find its own gaps

Claude is often a better critic of its own work than you'd expect — *if you ask*.
Turn it loose on its own blind spots.

> "What edge cases are we not handling here? List them, then write a test for
> each."

> "Where could this fail silently — produce a wrong result without an error? How
> would we detect that?"

> "If a hostile or careless operator ran this, what's the worst input they could
> give it? Guard against it."

> "Review your own change as if you were a skeptical reviewer who didn't trust
> it. What would you flag?"

This pattern is how you make the unhappy paths from step 3 systematic instead of
something you hope you remembered.

### Ask for options with trade-offs

When you're genuinely unsure how something should work, don't ask for *an* answer
— ask for a *choice*.

> "Give me two ways to handle users who have no Atlassian mapping, with the
> trade-offs of each. Don't implement yet."

> "What are my options for storing the field mappings between runs? List the
> simplest and the most robust, and tell me which you'd pick for our blast
> radius and why."

This keeps you in the decision seat. It also tends to surface considerations you
hadn't thought of, because Claude has to articulate *why* one option beats
another. Pair it with plan mode for any real design fork.

### Ask it to record the correction in `CLAUDE.md`

This is the highest-leverage prompt in the whole playbook, and the one beginners
under-use. **When you correct Claude, make the correction permanent.**

`CLAUDE.md` is just a text file that Claude re-reads at the start of every
session, and you can ask Claude to edit it in plain English like any other file.
So whenever you catch yourself saying "no, we always do it *this* way," follow up:

> "Good. Now add that to `CLAUDE.md` so it sticks — every new command must
> support a `--dry-run` flag."

> "We just decided last-active date is the most recent of created/completed/
> commented. Record that definition in `CLAUDE.md` so we don't relitigate it."

> "Add this new export command to the CLI catalog in `CLAUDE.md` with a one-line
> description of its inputs and outputs."

Why this compounds: a correction you give in a chat message helps *this* session
only and is forgotten by next week. A correction written into `CLAUDE.md` helps
*every future session* — you stop being the rule police, and the AI inherits your
standards on every turn ([Pillar 1, Part 5](05-five-pillars.md)). Two related
commands worth knowing: **`/init`** drafts a starter `CLAUDE.md` by scanning your
codebase, and **`/memory`** shows you exactly which instruction files are
currently loaded.

> **The feedback loop.** This is documentation working as a feedback loop
> ([Part 7](07-deciding-what-not-to-build.md)): the toolkit keeps a running
> catalog of every command — inputs, outputs, options — right in its rules file,
> so the description and the tool never drift apart, and Claude's next
> contribution starts from accurate context.

### Prompt hygiene: small, specific, one thing at a time

A few habits that make every prompt above land better:

- **One goal per turn.** "Add the export command" and "also refactor the database
  layer" are two requests; sending them together gets you a sprawling diff you
  can't review. Split them.
- **Be specific about the output.** "A CSV in `exports/` with these columns"
  beats "a report." Specificity is what lets you *verify* the result in step 5.
- **Reference the real thing.** Name the file, the helper, the table. "Reuse
  `src/lib/database.py`" is a better instruction than "use the database code."
- **State the constraint.** If it must be stdlib-only, must not touch Jira, must
  support `--workers` — say so. (Much of this is already in `CLAUDE.md`, but
  restating a constraint that *really* matters for this task is cheap insurance.)

---

## Anti-patterns: the four ways the loop breaks

The loop fails in predictable ways. Each anti-pattern is a step quietly skipped.

**1. Accepting code you didn't skim.** The vending-machine habit: request, paste,
move on. It feels fast and it's the most dangerous one, because un-reviewed code
compounds — you build on it before you know it's wrong. *The fix:* never commit
something you haven't at least skimmed the diff of and asked Claude to summarize
(step 6). "I looked and nothing surprised me" is a low bar, but it's a real gate.

**2. Skipping the dry-run.** "The tests pass, so it's fine — just run it for
real." Tests use fake data in a sandbox; they cannot catch a problem that only
shows up against your actual database or the live Jira's real constraints. *The
fix:* for anything Tier-3, dry-run is mandatory and comes before the confirmation
prompt — no exceptions ([Part 3](03-before-you-write-any-code.md),
[Part 5](05-five-pillars.md)).

**3. Giant commits.** Letting a whole afternoon of changes pile into one commit.
Now your undo button reverts ten unrelated things at once, your reviewer can't
follow it, and a bug could be hiding anywhere in 600 lines. *The fix:* commit
small and often — one coherent change plus its test (step 7). If you notice the
diff sprawling, that's a sign to commit what's done and start a fresh turn.

**4. Vague prompts.** "Make the export better." "Fix the user thing." Vague in,
vague out — you get a change you can't verify because you never said what
"better" means. *The fix:* describe inputs and outputs (step 1), and if you're
unsure, ask for a plan or for options with trade-offs rather than a guess.

A useful self-check: if you can't say *which step you're on*, you've probably
skipped one. The loop is the antidote to all four.

---

## When Claude goes wrong: recovery moves

It will happen. Claude will write confident, wrong code; misunderstand the goal;
or "fix" one thing and break another. This is expected — the AI is fast, not
infallible ([Part 9](09-pitfalls-and-limits.md)) — and the loop is *built* to
absorb it. Here's how to recover, in order of escalation.

**1. Revert via Git — the big undo.** If a change made things worse and you'd
committed a good state before it, you are one command from safety. A commit is a
restorable checkpoint; reverting undoes a change and returns you to the last good
snapshot. Ask:

> "This made things worse. Revert back to the last commit so we're at a clean
> state, then let's try a different approach."

This is *why* you commit small and often (step 7). The smaller your last commit,
the less you lose by rewinding to it. (If the work wasn't committed yet, you can
still discard the uncommitted changes and return to the last commit — same idea.)

**2. Narrow the prompt.** Often Claude went wrong because the request was too big
or too vague, so it guessed. Don't re-send the same broad ask louder — shrink it.

> "Let's do just the first piece: only write the function that reads users from
> the database and returns them as a list. No CSV, no command yet. Show me that
> and a test for it."

Decomposing the task gives Claude a target small enough to hit, and gives you a
change small enough to verify. Then build up one verified piece at a time.

**3. Add a failing test first.** When there's a specific bug — it produces the
wrong output for some input — the most durable recovery is to pin the bug with a
test *before* fixing it.

> "Here's a case it gets wrong: a user with no Atlassian mapping should have a
> blank Atlassian column, but it's writing the word `None`. First write a test
> that fails on exactly this, confirm it fails, then fix the code so it passes —
> and don't change the test to make it pass."

This does three things at once: it forces a precise definition of "correct," it
proves the fix actually fixes *this* bug, and it leaves behind a regression test
so the bug can never silently return — which is the project's standing rule for
every bug fix anyway. It's the single most powerful debugging move in the whole
playbook.

**4. Reset the context and re-plan.** If a session has wandered — lots of
half-finished attempts, Claude seems confused about the current state — the best
move is sometimes to stop, get back to a clean committed state (move 1), and
*start the loop over from step 2* with a fresh plan. A confused session rarely
un-confuses itself; a clean restart from a known-good commit almost always does.

The throughline: **every recovery move leans on something the loop already gave
you** — a clean commit to revert to, a smaller goal to re-prompt, a test to pin
the bug. You're never improvising from zero.

---

## A worked session, end to end

Putting it all together with our example. Watch the loop turn once.

1. **Describe (step 1).** "I want a command that exports every user and their
   activity counts to a CSV in `exports/`. Columns: name, email, active flag,
   tasks created, tasks completed, tasks commented, last active date. Reads only
   the local database — must not touch Asana or Jira."
2. **Plan (step 2).** "Before writing anything, give me a plan — which helpers
   you'll reuse, what files change, what you'll test." Claude proposes reusing
   `database.py` and resolving Atlassian ids from `user_atlassian_accounts`,
   leaving the column blank when there's no mapping. You correct one thing: "last
   active date = most recent of created/completed/commented." It updates the plan.
3. **Build (step 3).** "Implement it, with tests for a normal user, an unmapped
   user (blank column), and an empty database."
4. **Test (step 4).** `python3 -m unittest discover tests` → `OK`.
5. **Try it (step 5).** Run it for real against the local database (read-only, so
   this is the equivalent of a dry-run) and open the CSV. The unmapped user's
   Atlassian column is blank as planned; counts look plausible.
6. **Review (step 6).** "Show me the diff and summarize each file." Only the new
   script and its test changed — scope is clean. You ask why one line uses a
   particular date; the explanation matches your intent.
7. **Commit (step 7).** "Looks good — commit it." Claude proposes
   `feat(reports): add user activity export to CSV`; you approve; it commits.
8. **Capture the lesson (still step 7 / the playbook).** "Add this command to the
   CLI catalog in `CLAUDE.md`, and record that last-active-date is the most recent
   of created/completed/commented." Now the next session starts smarter.
9. **Repeat (step 8).** On to the next small, reviewed, reversible change.

No single step was hard. The *order* is what made the result trustworthy.

---

## The session checklist

Copy this and keep it next to you. It's the loop as a thing you can tick off,
every session.

```
DAILY LOOP — session checklist

GOAL
[ ] I stated the goal as inputs + outputs (not "how").
[ ] I said out loud what must NOT happen (the blast radius for this task).

PLAN (skip only for truly trivial changes)
[ ] I asked for a PLAN before any edits.
[ ] The plan reuses existing helpers instead of re-solving.
[ ] I corrected wrong assumptions in the plan (cheap) — not in the code.

BUILD
[ ] I asked for the code AND a test together.
[ ] The test covers the unhappy paths (empty input, missing data, errors).

TEST
[ ] python3 -m unittest discover tests  →  ends in OK
[ ] If it failed: I fixed the cause (not the test) and added a regression test.

DRY-RUN / TRY IT
[ ] Read-only task: I ran it and inspected the real output.
[ ] Live-system task: I ran --dry-run, read it like an auditor, used --limit /
    a single team first, and answered the confirmation prompt deliberately.

REVIEW
[ ] I skimmed the diff and confirmed only the expected files changed.
[ ] I asked Claude to explain anything surprising — and it matched my intent.
[ ] (Optional) I ran the built-in code review and triaged its findings.

COMMIT  (only with my explicit approval)
[ ] Tests green + diff reviewed.
[ ] Small, coherent change + its test.
[ ] Conventional Commit message scoped to the src/ folder touched.

CAPTURE
[ ] I asked Claude to record any correction/convention in CLAUDE.md.

IF IT WENT WRONG
[ ] Revert to the last good commit · narrow the prompt · add a failing test first.
```

---

## Key takeaways

- **The loop is the method in motion.** Describe → plan → build-with-test → test
  → dry-run → review → commit → repeat. Each step is a gate that catches problems
  while they're still cheap; the backward arrows (re-prompting, re-testing) are
  the loop *working*, not failing.
- **Plan before you build anything non-trivial.** Plan mode lets Claude research
  first and propose an approach you can correct in a sentence — far cheaper than
  fixing finished code. (This series itself was produced that way.) Plan for
  decisions; act for execution.
- **Code and test ship together, then you run the suite.** A feature without a
  test is a claim without proof. `python3 -m unittest discover tests` ending in
  `OK` is your green light after *every* change.
- **Dry-run before any live run.** Tests check a sandbox; the dry-run checks
  reality without changing it. For Tier-3 work it's mandatory, and the
  confirmation prompt that follows is a real decision, not a speed bump.
- **Review, then commit only with your approval.** Skim the diff, ask for
  explanations of anything surprising, lean on the built-in code review — then
  commit small and often. The human gate on commits is the trust boundary; the
  commit is your undo button.
- **Prompt deliberately, and make corrections permanent.** Ask for explanations,
  ask for the safe version by name, ask Claude to find its own gaps and offer
  options with trade-offs — and when you correct it, ask it to record the
  correction in `CLAUDE.md` so the lesson sticks.
- **Recovery is built in.** When Claude goes wrong: revert to the last good
  commit, narrow the prompt, or pin the bug with a failing test before fixing it.
  Every recovery move leans on something the loop already gave you.

---

## Where to next

- **Previous:** [05 — The five pillars of safe AI-assisted coding](05-five-pillars.md)
- **Up:** [The Claude Code playbook (parent)](../claude-code-playbook.md)
- **Next:** [07 — Deciding what NOT to build](07-deciding-what-not-to-build.md)

Other parts in the series:
[01 — What is vibe coding](01-what-is-vibe-coding.md)
· [02 — Speed inside guardrails](02-speed-inside-guardrails.md)
· [03 — Before you write any code](03-before-you-write-any-code.md)
· [04 — Getting set up](04-getting-set-up.md)
· [08 — A recipe for your own project](08-recipe-for-your-own-project.md)
· [09 — Pitfalls and honest limits](09-pitfalls-and-limits.md)
· [Series index](README.md)
