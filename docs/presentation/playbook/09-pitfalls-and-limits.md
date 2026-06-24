# Pitfalls and honest limits

*The credibility chapter. Every safety net in this series has a seam, every
constraint cuts both ways, and the AI that writes your code is fast but
genuinely fallible. Naming those limits honestly — and showing how to live with
them — is what makes the rest of the playbook trustworthy.*

> Part 9 of 9 in the Vibe Coding, Safely series

---

Every part of this series so far has been, in a sense, a sales pitch. We told
you that a rules file makes the AI consistent, that tests catch mistakes, that
dry-runs let you look before you leap, that helpers reduce risk. All true. But a
guide that only lists wins is a guide you can't fully trust — because the real
world is full of seams, exceptions, and "well, *usually*."

This part is the counterweight. It names the honest limits of everything we've
recommended, using the same running example — the **Asana→Jira migration
toolkit**, a standard-library-only Python tool that reads a company's Asana data
and **creates the matching items in a live Jira**. Because that tool writes to a
production system, its pitfalls aren't academic: a misunderstanding here can
mean thousands of wrong tickets in a client's account.

Here is the thesis up front, so you can hold it the whole way down:

> None of these limits is a reason **not** to vibe code. Every one of them is a
> reason to vibe code **inside guardrails** — which is the entire point of this
> series.

A pitfall you've named and planned for is a managed risk. A pitfall you pretend
doesn't exist is the one that bites.

---

## Table of contents

- [How to read this chapter](#how-to-read-this-chapter)
- [Pitfall 1 — Your constraints have a backside](#pitfall-1--your-constraints-have-a-backside)
- [Pitfall 2 — Coverage targets only help if you uphold them](#pitfall-2--coverage-targets-only-help-if-you-uphold-them)
- [Pitfall 3 — The AI is fast, not infallible](#pitfall-3--the-ai-is-fast-not-infallible)
- [Pitfall 4 — You are still accountable](#pitfall-4--you-are-still-accountable)
- [Pitfall 5 — Secrets are serious](#pitfall-5--secrets-are-serious)
- [Pitfall 6 — Green tests can lie to you](#pitfall-6--green-tests-can-lie-to-you)
- [Pitfall 7 — Context and consistency drift across a big codebase](#pitfall-7--context-and-consistency-drift-across-a-big-codebase)
- [Pitfall 8 — Prompt ambiguity builds the wrong thing, correctly](#pitfall-8--prompt-ambiguity-builds-the-wrong-thing-correctly)
- [Pitfall 9 — Dependency and security review is still your job](#pitfall-9--dependency-and-security-review-is-still-your-job)
- [Pitfall 10 — Stale docs actively mislead the AI](#pitfall-10--stale-docs-actively-mislead-the-ai)
- [Red flags to watch for](#red-flags-to-watch-for)
- [Mitigations summary table](#mitigations-summary-table)
- [Key takeaways](#key-takeaways)
- [Where to next / wrap-up](#where-to-next--wrap-up)

---

## How to read this chapter

Each pitfall below follows the same four-beat structure so you can skim or dig
in as you like:

1. **What it is** — the limit, in plain language.
2. **How it bites you** — the realistic failure, not the textbook one.
3. **A concrete example** — drawn from the Asana→Jira toolkit wherever possible.
4. **How to mitigate it** — practical moves that *reduce* the risk. None of them
   make the pitfall disappear; honesty means saying so.

That last point matters. The goal here is not to convince you these problems are
solved. It's to give you a clear-eyed relationship with them, so you make
deliberate choices instead of getting surprised.

If you're brand new, read the "what it is" and "how to mitigate" parts of each
section first; the examples are there when you want to go deeper.

---

## Pitfall 1 — Your constraints have a backside

### What it is

A **constraint** is a rule you set for the project: a language version, a "no
outside libraries" policy, a coding style. Constraints are good — they're how
[Part 5's first pillar](05-five-pillars.md) keeps the AI consistent. But every
constraint trades one kind of cost for another. There's no free constraint.

### How it bites you

The bite is subtle: you adopt a constraint for one good reason, then quietly pay
for it somewhere else and forget that you signed up for the bill. You feel the
benefit (it was your choice) but experience the cost as friction ("why is this
taking so long?").

### A concrete example

The Asana→Jira toolkit is **standard-library-only Python 3.11+**. That is a
real, deliberate constraint, and it bought real things:

- **Portability.** The tool runs on any machine with a recent Python and nothing
  to install. No "works on my machine" dependency drift.
- **A tiny attack surface.** Every third-party package you add is code you didn't
  write, running with your permissions, that could ship a vulnerability or be
  hijacked. Zero dependencies means zero supply-chain risk from that direction —
  which matters a lot for a tool holding credentials to a company's live data.

But the backside is just as real. Because there's no `requests` library, the
project hand-writes its HTTP client on top of Python's built-in `urllib`:

```python
# src/lib/http_client.py — only built-in modules
import json, ssl, time
from urllib import error, parse, request
```

With `requests`, retries, JSON handling, and SSL would be a few lines. Here,
rate-limit retries, backoff, and pagination are all hand-written code — more to
write, more to test, more places a bug can hide. The constraint didn't remove
work; it *moved* it from "trusting a library" to "owning the code."

### How to mitigate it

- **Choose constraints out loud, with the tradeoff written down.** When you tell
  Claude "standard library only," also record *why* in `CLAUDE.md` — so future
  you (and the AI) remember it was a choice, not an accident.
- **Re-examine constraints when the cost spikes.** If you find yourself
  hand-writing a third gnarly parser, that's a signal to ask: "Is this constraint
  still worth it?" The answer can be yes — but make it a decision, not a habit.
- **Let the constraint earn its keep on the high-stakes parts.** For a tool that
  writes to live Jira and holds tokens, "fewer moving parts" is worth a lot. For
  a throwaway script, it might not be. Match the constraint to the blast radius,
  exactly as [Part 3](03-before-you-write-any-code.md) taught for guardrails.

What you can't do is pretend the constraint is free. It isn't. It's a trade you
chose — keep it a *chosen* trade.

---

## Pitfall 2 — Coverage targets only help if you uphold them

### What it is

**Test coverage** is the percentage of your code that the tests actually
exercise. This project targets **≥90% coverage** and backs it with **48 unit
test files and 2 integration test files**. A coverage *target* is a promise to
yourself. Like all promises, it's only worth what your follow-through is worth.

### How it bites you

Coverage erodes under time pressure, and it erodes *silently*. The dangerous
sequence looks like this:

1. You're moving fast. A feature works in the dry-run. The deadline is real.
2. Writing the test for the awkward edge case would take twenty more minutes.
3. You ship it "just this once," meaning to circle back.
4. You never circle back. The next feature builds on the untested one.
5. Three weeks later, coverage has quietly fallen, and the gap is exactly in the
   code that touches live Jira — because that's the code that was hardest to test
   and so the easiest to skip.

Nobody decided to lower their standards. The standard just leaked, one
reasonable-sounding exception at a time.

### A concrete example

Imagine adding a new branch to the issue-creation logic — say, a special case for
a project with no owner. It's a five-line `if`. The happy path is covered by
existing tests, so the suite still ends in `OK`. You ship. But the *new branch*
has no test of its own. Months later someone refactors owner resolution, the
no-owner branch silently changes behavior, and because nothing tests it, the
suite stays green while live runs start mis-assigning issues. The 90% number on
the dashboard didn't protect you, because the 10% gap was precisely the new,
risky line.

### How to mitigate it

- **Make the green light a hard gate, not a goal.** Run
  `python3 -m unittest discover tests` after *every* change, and treat a new
  untested branch as "not done," the same way [Part 6](06-the-daily-loop.md)
  treats it. "Code and test ship together" is the rule that keeps coverage from
  leaking.
- **Pin every bug fix with a test.** The single best coverage habit: when you fix
  a bug, write the test that would have caught it *first*. This grows coverage
  exactly where reality proved you needed it.
- **Watch the number, but trust the habit more.** A coverage report tells you
  *where* you're thin; it can't tell you whether the thin parts matter. Use it as
  a map of risk, not a trophy.
- **Be honest in the moment of temptation.** When you're about to skip a test
  "just this once," say it out loud: "I am lowering the standard right now." Often
  that's enough to not do it.

The target doesn't keep itself. *You* keep it — and the day you stop is the day
the safety net quietly develops a hole you can't see.

---

## Pitfall 3 — The AI is fast, not infallible

### What it is

This is the big one. Claude writes code quickly and *confidently* — and
confidence is not correctness. The AI can produce code that reads beautifully,
explains itself fluently, and is wrong. It does not announce its uncertainty in
the prose; you have to go looking for it.

### How it bites you

There are three distinct flavors of confident-wrong, and they fail in different
ways:

**1. Hallucinated APIs and flags.** The AI invents a function, parameter, or
command-line flag that *sounds* right because it fits the pattern of real ones —
but doesn't exist. A made-up `--skip-existing` flag, a `client.bulk_create()`
method that was never written, a Jira field name that's plausible but wrong.

**2. Plausible-but-subtly-broken logic.** The code runs. It even produces
output. But the logic is off in a way you only notice with the right input — an
off-by-one in pagination, a retry that resets a counter it shouldn't, a
comparison that's `>` where it should be `>=`.

**3. Silently dropped edge cases.** The AI solves the case you described and
quietly ignores the ones you didn't mention: the empty list, the user with no
email, the project with no owner, the comment whose author has no Atlassian
account. No error — just a gap.

The common thread: **none of these announce themselves.** The code looks done.

### A concrete example

Suppose you ask Claude to "create Jira issues for every task in the project, and
assign each one to its owner." It writes clean code that does exactly that. But
the toolkit's real behavior is more careful than the naive version: it only sets
an assignee when the owner's Atlassian account is *active*, and when Jira rejects
an assignee as unassignable it drops just that assignment and keeps going rather
than failing the batch (the `assignee dropped (user not assignable)` fallback).
A naive AI draft might skip both nuances. It would run fine in your test data —
and then fail the *whole batch* on the first inactive user in the client's real
account. The draft was confident. The draft was wrong. Nothing in the draft told
you so.

### What actually catches it

This is where the rest of the series pays off. The AI's fallibility is exactly
why the guardrails exist — they are not decoration:

- **Tests** catch hallucinated APIs immediately (the code won't even import) and
  catch broken logic when you've written a test for that case. This project's
  tests deliberately target the bug-prone spots: rate limiting, pagination,
  delete/replace logic, format conversions.
- **`--dry-run` modes** print exactly what *would* be sent to Jira and change
  nothing. This is your last chance to spot "wait, that's the wrong field" before
  it becomes a thousand real tickets. For a live-writing tool, dry-run is not
  optional.
- **Confirmation prompts** ("About to create N issues. Continue?", skippable with
  `--yes`) put a human decision between the plan and the permanent action.
- **Human code review** — skimming the diff, asking Claude to explain anything
  surprising — catches the subtle logic and the dropped edge case that tests
  didn't think to cover.

### How to mitigate it

- **Assume the first draft is wrong somewhere; go find where.** Ask Claude
  directly: "What edge cases does this not handle? Write tests for them." It's
  often good at finding its own gaps once prompted to look.
- **Never let confident prose substitute for evidence.** "This handles rate
  limiting correctly" is a claim. The passing rate-limit test is proof. Demand the
  proof.
- **Always dry-run before the real run on anything that touches live data.** No
  exceptions for a tool with this blast radius.

The AI being fallible is not a bug in your process — it's the *assumption* your
process is built around. The day you forget that is the day you skip the
dry-run.

---

## Pitfall 4 — You are still accountable

### What it is

It is tempting to feel that because the AI wrote the code, the AI is somehow
responsible for it. It isn't, and it can't be. **You** approve every commit and
**you** approve every live run. When the wrong tickets land in the client's
Jira, "the AI wrote it" is not a sentence anyone wants to say to the client.

### How it bites you

Accountability erodes through *automation complacency* — the well-documented
human tendency to rubber-stamp decisions a machine has pre-made. The "are you
sure?" prompt starts as a real checkpoint and degrades into reflex: you type
`y` before reading the number. The day the number is wrong — 12,000 issues
instead of 120 — your reflex creates the disaster the prompt was designed to
prevent.

### A concrete example

The toolkit's `CLAUDE.md` rule is explicit: **never create a commit unless the
human explicitly approves it.** That rule exists precisely to keep a human in the
loop at the moment of permanence. But the rule only works if you treat your
approval as a real decision. If you approve a commit you didn't skim, or hit
`--yes` on a creation run you didn't dry-run, you've satisfied the *letter* of
the guardrail while defeating its *purpose*. The prompt fired; nobody actually
decided.

### How to mitigate it

- **Treat every "are you sure?" as a genuine decision.** Before you confirm a
  live run, read the count out loud. "About to create **347** issues." Does 347
  match what you expect? If you don't know what to expect, you're not ready to
  confirm.
- **Earn the confirmation with a dry-run.** The confirmation prompt is only
  meaningful if you've already seen what the run will do. Confirming blind is
  theater.
- **Keep the human gate on commits real.** The `CLAUDE.md` rule that Claude never
  commits without your approval is a *trust boundary*, not a formality. Skim the
  diff before you say yes. Commit small and often so each decision is small.
- **Own the outcome in advance.** Before a live run, ask yourself: "If this goes
  wrong, am I prepared to explain that I approved it?" If the honest answer is
  no, slow down.

You delegated the typing. You did not delegate the responsibility. There's no
mechanism that can transfer that, and pretending otherwise is how careful people
end up surprised.

---

## Pitfall 5 — Secrets are serious

### What it is

A **secret** is a credential — an API token, a password — that proves the tool is
allowed to act as you against Asana and Jira. Anyone who has the token can do
anything the token can do, including writing to the company's live systems.
Secrets are the highest-consequence data the tool touches.

### How it bites you

Secrets leak through ordinary carelessness, not dramatic hacks:

- A token gets pasted into a file inside the project folder and then committed to
  git — now it's in the history *forever*, even if you delete it in a later
  commit, and anyone who can read the repo can read the token.
- A credentials file is left world-readable, so any process or user on the
  machine can read it.
- A token gets shared in a chat, a screenshot, or a log, and nobody rotates it
  because "it was only for a minute."

The leak is usually silent. You find out when someone uses it.

### A concrete example

This project's design exists specifically to make leaks hard:

- **Secrets live OUTSIDE the project folder**, in a home-directory env file, so
  they can't be swept into a commit. The toolkit resolves credentials in a clear
  order: **process environment → a pointer file (`.asana_jira_env_path`) that
  names the env file → a default `~/.asana_jira_env`.**
- **The env file is locked down with `chmod 600`** (the project's
  `write_credentials_file` sets `0o600` automatically), meaning only your user
  account can read it. Even other users on the same machine can't.
- **Sensitive local artifacts stay local and disposable.** The synced database at
  `data/asana.sqlite` includes user emails, and the CSVs under `exports/` can
  contain personal data. These are git-ignored, and the playbook's teardown step
  deletes them when you're done with a client.

### How to mitigate it

- **Keep tokens out of the repo, period.** If a secret was never in the project
  folder, it can never be committed. The home-dir env file is the whole point.
- **Restrict permissions.** `chmod 600` on any credentials file. Treat a
  world-readable token file as already compromised.
- **Rotate on any exposure — and define "exposure" broadly.** If a token ever
  appears in a commit, a screenshot, a log, a chat, or a screen-share, *rotate it*
  (revoke the old one, issue a new one). "It was probably fine" is not a security
  policy. Rotation is cheap; cleanup after misuse is not.
- **Mind the derived data, not just the tokens.** The local SQLite and the export
  CSVs hold real people's emails. Delete them when the engagement ends; don't let
  a client's personal data linger on a laptop.

Secrets are the one pitfall where the cost of a single mistake can dwarf
everything else in this chapter. Spend your paranoia here.

---

## Pitfall 6 — Green tests can lie to you

### What it is

A passing test suite is reassuring — and it should be. But tests only check
**what you thought to test**. A green suite proves your code does what your tests
ask of it. It says nothing about the cases no test asks about. "All tests pass"
and "the code is correct" are different statements that happen to look identical
on screen.

### How it bites you

You add a feature, write a test for the case you had in mind, see `OK`, and feel
done. But the bug lives in the case you *didn't* imagine — and since no test
asks about it, the suite is happily, confidently green while the bug ships. The
green checkmark gives you a feeling of safety that is, in that exact spot,
unearned.

This is the close cousin of [Pitfall 3](#pitfall-3--the-ai-is-fast-not-infallible):
the AI silently drops an edge case, *and* you didn't write a test for it, so two
safety nets have the same hole and the code sails through both.

### A concrete example

Suppose the comment-export logic is tested with comments that all have known
authors. The tests pass. But a real Asana account has comments whose author has
no mapped Atlassian account. If no test covers the unmapped-author case, the
suite stays green even if the code crashes or silently drops those comments on
real data. The tests weren't wrong — they just never asked the question that
mattered.

### How to mitigate it

- **Read coverage as a map of your blind spots.** Coverage tools show which lines
  no test touches. Those lines are where green tests lie loudest. (This loops back
  to [Pitfall 2](#pitfall-2--coverage-targets-only-help-if-you-uphold-them):
  coverage and "tests can lie" are two sides of the same coin.)
- **Deliberately test the unhappy paths.** Empty inputs, missing fields,
  duplicate data, the user with no email, the project with no owner. This
  project's suite intentionally covers pagination and delete/replace *because*
  those are where bugs hide — not because the happy path needed it.
- **Ask Claude to attack its own code.** "What inputs would break this? Write
  failing tests for them." Turning the AI on its own blind spots is one of the
  fastest ways to find the questions your tests forgot to ask.
- **Let the dry-run and review be the third net.** When tests and your
  imagination both miss a case, seeing real (proposed) output in a dry-run is
  often what finally surfaces it.

Green means "the questions I asked all passed." It never means "there were no
other questions."

---

## Pitfall 7 — Context and consistency drift across a big codebase

### What it is

The AI works with a finite view of your project at any moment. On a small file
it sees everything. As the codebase grows — this one has **46 scripts** leaning
on **13 shared helpers** — the AI may not have the *whole* picture in view when
it writes, and small inconsistencies can creep in: two scripts that handle the
same error differently, a new module that reinvents something a helper already
does.

### How it bites you

Drift is gradual and individually harmless. Each small inconsistency seems fine
on its own. Collectively, they're where bugs hide — because the inconsistent spot
is the one that behaves differently from everywhere else, and so the one your
mental model gets wrong. It's exactly the problem [Part 5's second
pillar](05-five-pillars.md) (reusable helpers) was built to prevent — but the
prevention only works if you actively steer toward it.

### A concrete example

You ask Claude to add a new export script. Left to itself, it might write its own
little credential-loading logic inline, instead of using the shared `config.py`
helper that every other script uses. Now there are two ways credentials get
loaded in the project, and the next time the resolution order changes, one of
them gets updated and the other doesn't. The inconsistency is the bug waiting to
happen.

### How to mitigate it

- **Point the AI at the pattern explicitly.** "Use the existing `config.py`
  helper for credentials, like `export_users.py` does." Naming the canonical
  example beats hoping the AI infers it.
- **Extract helpers the moment you see repetition.** When Claude writes
  similar logic a second time, ask it to "extract this into a shared helper and
  use it in both places." Consolidation is the antidote to drift.
- **Keep `CLAUDE.md` carrying the conventions.** The rules file is how the AI
  inherits context it can't see in the current window. A documented convention
  ("all scripts resolve credentials via `config.py`") survives across sessions
  where raw memory doesn't.
- **Skim for consistency in review.** When you review a new script, one good
  question is simply: "Does this do things the way the rest of the project does
  them?" Drift is easiest to catch right when it's introduced.

The AI is excellent at local correctness and weaker at global consistency. That
gap is yours to close, and the tools to close it — helpers, `CLAUDE.md`, review —
are all things you already have.

---

## Pitfall 8 — Prompt ambiguity builds the wrong thing, correctly

### What it is

The AI builds what you *said*, which is not always what you *meant*. A vague or
ambiguous prompt produces code that is internally correct and entirely wrong for
your purpose. The failure isn't in the code; it's in the gap between your
intent and your words.

### How it bites you

You get something that works, passes its tests, and solves a problem — just not
the one you had. Because it's polished and confident (see
[Pitfall 3](#pitfall-3--the-ai-is-fast-not-infallible)), it's easy to accept. The
mismatch surfaces later, often after you've built more on top of it, when it's
expensive to unwind.

### A concrete example

You ask: "Export the active users." You meant users who are *active in Asana*
(the account flag). Claude reasonably interprets "active" as users with *recent
task activity* and filters by `last_active_date`. Both readings are sensible.
The code is correct for its reading. But you get a different set of users than
you wanted, and you might not notice until someone asks where half the team
went. One ambiguous word, one wrong deliverable — built flawlessly.

### How to mitigate it

- **Be specific about inputs and outputs, not mechanism.** [Part 6](06-the-daily-loop.md)'s
  Step 1 says describe the *what* precisely. "Export users whose Asana account is
  marked active" removes the ambiguity that "active users" leaves open.
- **Ask for a plan before building anything non-trivial.** Plan mode makes the
  AI state its interpretation *before* it writes code. Correcting a one-paragraph
  plan ("no, I mean account-active, not activity-active") is far cheaper than
  unwinding a finished feature. This is the single best defense against ambiguity.
- **When unsure, ask for options.** "Give me two interpretations of 'active
  users' with the trade-offs." This surfaces the ambiguity *to you* before it
  becomes code.
- **Read the first output as a question, not an answer.** The initial draft is
  partly the AI telling you how it understood you. If that's not what you meant,
  you just learned it cheaply.

The AI is a precise interpreter of imprecise instructions. The fix is almost
never "smarter AI" — it's clearer prompts and a plan you check before building.

---

## Pitfall 9 — Dependency and security review is still your job

### What it is

When the AI does add or use an outside dependency — or writes code that touches
security-sensitive surfaces like SSL, file permissions, or auth — *reviewing
that choice* is a human responsibility. The AI can suggest a package; it cannot
be accountable for whether that package is safe, maintained, or appropriate.

### How it bites you

It's easy to wave through whatever the AI reaches for. But a dependency is code
you didn't write running with your permissions, and a security shortcut is a
hole someone else can walk through. The AI may suggest the popular option, the
convenient option, or an option that's subtly insecure — and unless you look, it
ships.

### A concrete example

This toolkit's **standard-library-only** rule means dependency review is mostly
*pre-decided*: the answer to "should we add this package?" is almost always "no,"
which is itself a powerful security posture (zero supply-chain risk). But the
security-sensitive *code* is still real. The project honors a
`skip_ssl_verification` setting — a flag that, if casually flipped on, would
disable the protection that keeps your tokens from being intercepted. The AI
could plausibly suggest setting it `true` to "fix" a certificate error. That's a
security decision a human must own, not rubber-stamp. The right move is to fix
the certificate, not disable the check — and only a human reviewing *why* the
flag is being flipped catches that.

### How to mitigate it

- **Default to "no" on new dependencies.** Make adding a package a deliberate,
  reviewed decision — [Part 7](07-deciding-what-not-to-build.md)'s discipline.
  Fewer dependencies, fewer things to vet.
- **When you must add one, vet it like a human.** Is it maintained? Widely used?
  Does it really save enough to justify the new attack surface? The AI can gather
  facts; the judgment is yours.
- **Treat security-sensitive flags as red-flag territory.** Anything touching SSL,
  auth, file permissions, or credentials gets a slow, skeptical human read — never
  a reflexive `--yes`.
- **Use the built-in security review.** Claude Code can review a diff specifically
  for security issues; use it as a *first* pass, not the *final* word.

The AI is a capable security *assistant*. It is not your security *owner*. That
seat stays human.

---

## Pitfall 10 — Stale docs actively mislead the AI

### What it is

Throughout this series we've praised documentation — `CLAUDE.md`, the `docs/`
reference files — as a feedback loop that makes the AI better. The honest flip
side: because **Claude re-reads `CLAUDE.md` and the docs every session**, stale
documentation doesn't just fail to help. It *actively misleads* the AI on every
single turn.

### How it bites you

Good docs are a force multiplier. Wrong docs are a force multiplier *for the
wrong thing*. If `CLAUDE.md` says a script has a flag it no longer has, or
describes behavior that changed three commits ago, the AI will confidently build
on that false premise — and now the documentation that was supposed to keep it
consistent is the thing teaching it to be wrong. The better the AI trusts your
docs, the more damage a stale doc does.

### A concrete example

This project keeps a detailed CLI catalog inside its rules file — every script,
its inputs, outputs, and flags. That catalog is enormously useful *as long as
it's current*. The moment someone adds a `--clear-first` flag to a script and
doesn't update the catalog, the AI's picture of that script is wrong. Next
session it might generate a command using the old flags, or "helpfully" remove
the new behavior because the docs don't mention it. The doc that was an asset
yesterday is a liability today — and nothing warns you, because the AI reads it
with the same trust either way.

(Worth noting candidly: docs can also disagree with each other. This very
toolkit uses `AGENTS.md` as the real rules file with `CLAUDE.md` symlinked to it
— the kind of detail that's easy to describe backwards if you're not looking at
the repo. The fix is the same: when in doubt, check the source, and keep the
description matched to reality.)

### How to mitigate it

- **Update docs in the same change as the code.** When you add a flag or a
  script, update `CLAUDE.md` and the relevant `docs/` file in the same breath —
  the way this project's conventions require. A doc edit isn't busywork; it's part
  of "done."
- **Record corrections immediately.** When you tell Claude "no, we do it this
  way," follow up with "add that to `CLAUDE.md` so it sticks." The lesson that
  isn't written down is the lesson you'll re-teach next week.
- **Periodically ask the AI to audit its own docs.** "Read the CLI catalog in
  `CLAUDE.md` and check each described flag against the actual scripts. Flag any
  mismatches." The AI is good at this cross-check when you ask for it.
- **Treat the docs as load-bearing, not decorative.** If they're going to steer
  the AI on every turn, they deserve the same care as the code they describe.

Documentation is a feedback loop in both directions. Kept current, it compounds
in your favor. Left stale, it compounds against you — quietly, on every session.

---

## Red flags to watch for

A field guide. If you catch yourself or your process doing any of these, slow
down — it's usually a pitfall above, mid-bite:

- [ ] **You're about to type `y` (or `--yes`) without reading the number.** The
  confirmation prompt has become reflex. Stop and read the count.
- [ ] **You're running for real without having dry-run first.** On a live-writing
  tool, this is the cardinal sin.
- [ ] **The suite is green, but you can't name what the new code's test actually
  checks.** Green may be lying; you may have shipped an untested branch.
- [ ] **You're skipping a test "just this once" under deadline.** Coverage is
  leaking. Say it out loud.
- [ ] **A token, even briefly, appeared in a commit / log / screenshot / chat.**
  Rotate it now, not later.
- [ ] **You pasted a secret into a file inside the project folder.** Move it out;
  assume it may already be committed.
- [ ] **The AI cited a flag, method, or field and you didn't verify it exists.**
  Possible hallucination — check the source.
- [ ] **The first draft "looks great" and you're tempted to ship without review.**
  Confident prose is not evidence. Skim the diff.
- [ ] **A new script reinvents something a helper already does.** Consistency
  drift — point it at the canonical pattern.
- [ ] **You used a vague word in the prompt ("active," "all," "the recent ones").**
  Ambiguity ahead — ask for a plan, get specific.
- [ ] **The AI suggested adding a dependency or flipping a security flag.**
  Human-owned decision — vet it, don't rubber-stamp.
- [ ] **You changed a flag/behavior but not the docs.** Stale docs will mislead
  the AI next session. Update them in the same change.
- [ ] **You're about to approve a commit you didn't skim.** You're delegating
  responsibility you can't actually delegate.

---

## Mitigations summary table

| Pitfall | How it bites | Primary mitigation (doesn't remove the risk) |
|---|---|---|
| 1. Constraints have a backside | You pay a hidden cost for a chosen benefit | Choose constraints out loud, record the tradeoff, re-examine when costs spike |
| 2. Coverage needs discipline | The standard leaks under time pressure | Hard green-light gate after every change; pin every bug with a test |
| 3. AI is fast, not infallible | Confident, wrong code: hallucinations, subtle bugs, dropped edge cases | Tests + `--dry-run` + confirmation prompts + human review |
| 4. You're still accountable | Automation complacency turns checkpoints into reflex | Treat every "are you sure?" as a real decision; earn it with a dry-run |
| 5. Secrets are serious | Silent leaks via commits, loose permissions, sharing | Tokens outside the repo; `chmod 600`; rotate on any exposure |
| 6. Green tests can lie | Tests only check what you thought to test | Test unhappy paths; read coverage as a blind-spot map; ask the AI to attack its code |
| 7. Context / consistency drift | Inconsistencies accumulate where bugs hide | Point at canonical patterns; extract helpers; keep conventions in `CLAUDE.md` |
| 8. Prompt ambiguity | The wrong thing, built correctly | Be specific about inputs/outputs; plan mode states intent before building |
| 9. Dependency / security review | Convenient-but-unsafe choices wave through | Default to "no" on deps; vet what you add; slow read on security flags |
| 10. Stale docs mislead | Docs steer the AI wrong on every session | Update docs in the same change; periodically audit them against the code |

---

## Key takeaways

- **Honesty is a feature.** Every safety net in this series has a seam. Naming the
  seams is what makes the nets trustworthy — a guide that only lists wins isn't
  one you should trust.
- **Constraints cut both ways.** Standard-library-only bought portability and a
  tiny attack surface, and cost more hand-written code. Choose constraints
  deliberately and keep the tradeoff visible.
- **The AI is fast, not infallible — design around that.** It writes confident,
  wrong code: hallucinated APIs, subtly broken logic, silently dropped edge cases.
  Tests, dry-runs, and review aren't decoration; they're the assumption your whole
  process rests on.
- **Your safety nets only work if you uphold them.** Coverage that leaks, green
  tests you don't question, confirmation prompts you reflex-approve — each is a
  net with a hole you put there. Discipline is the net.
- **You stay accountable.** The human approves every commit and every live run.
  That responsibility can't be delegated to the AI, and "are you sure?" prompts
  are real decisions, not speed bumps.
- **Secrets get your sharpest paranoia.** Out of the repo, locked down with
  `chmod 600`, rotated on any exposure. One leak can dwarf every other risk here.
- **Docs steer the AI every session — so keep them current.** Stale docs don't
  just fail to help; they actively mislead. Update them in the same change as the
  code.
- **These are reasons to vibe code inside guardrails, not reasons to avoid it.**
  A pitfall you've named and planned for is a managed risk. That's the whole
  method.

---

## Where to next / wrap-up

You've reached the end of the series. You started by learning what vibe coding
*is*, walked through the structure that makes it safe, practiced the daily loop,
learned what *not* to build, got a recipe for your own project — and now you've
seen the honest limits of all of it.

That last part is not a downer ending. It's the point. The reason to learn the
pitfalls is so that you can vibe code *anyway*, with your eyes open, moving at AI
speed inside guardrails you understand and uphold. The speed is real. The risks
are real. The method is how you get the first without being surprised by the
second.

- **Previous:** [08 — A recipe for your own project](08-recipe-for-your-own-project.md)
- **Up:** [The Claude Code playbook (parent)](../claude-code-playbook.md)
- **Back to the start:** [01 — What is vibe coding](01-what-is-vibe-coding.md)

Other parts in the series:
[02 — Speed inside guardrails](02-speed-inside-guardrails.md)
· [03 — Before you write any code](03-before-you-write-any-code.md)
· [04 — Getting set up](04-getting-set-up.md)
· [05 — The five pillars](05-five-pillars.md)
· [06 — The daily loop](06-the-daily-loop.md)
· [07 — Deciding what NOT to build](07-deciding-what-not-to-build.md)
· [Series index](README.md)
</content>
</invoke>
