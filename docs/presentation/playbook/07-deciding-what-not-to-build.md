# Deciding what NOT to build

*Restraint is a core engineering discipline — and AI makes it harder, because it
makes generating more code, more features, and more dependencies almost free.
This part is about the harder, more valuable skill: choosing what to leave out,
deleting what has served its purpose, and earning complexity one verified step
at a time.*

> Part 7 of 9 in the Vibe Coding, Safely series

---

Every other part of this series has been about *doing*: setting up
([Part 4](04-getting-set-up.md)), building inside the five pillars
([Part 5](05-five-pillars.md)), and turning the daily loop
([Part 6](06-the-daily-loop.md)). This part is the opposite, and it might be the
most important of all: it is about **not doing**.

A surprising amount of good engineering is *restraint*. The instinct, especially
when you're moving fast with an AI that will happily write anything you ask, is
that more is better — more features, more flags, more files, more helpers, more
libraries. It almost never is. Every one of those is something you now have to
maintain, secure, test, document, and hold in your head. The strongest tools are
not the ones with the most in them. They are the ones with the *least* in them
that still does the job.

This is counterintuitive, so let's make it concrete with the running example —
the **Asana→Jira migration toolkit**, a Python tool that reads a company's Asana
data and creates the matching items in a live Jira. Across its history you can
watch this discipline play out three different ways: it **deleted a prototype**
the moment it was no longer needed, it **said no to convenient libraries** and
paid the real cost of that choice, and it **earned its complexity gradually** —
starting as a humble spreadsheet exporter and only becoming a live-Jira creator
once reality demanded it. We'll walk all three, with the actual commits.

---

## Table of contents

- [Why AI makes restraint harder, not easier](#why-ai-makes-restraint-harder-not-easier)
- [The true cost of every feature, file, and dependency](#the-true-cost-of-every-feature-file-and-dependency)
- [YAGNI: "You Aren't Gonna Need It"](#yagni-you-arent-gonna-need-it)
- [Delete prototypes once they've served their purpose](#delete-prototypes-once-theyve-served-their-purpose)
  - [The "note shuttle"](#the-note-shuttle)
  - [Deprecate, don't just hoard](#deprecate-dont-just-hoard)
- [Saying no to convenient libraries: the stdlib-only decision](#saying-no-to-convenient-libraries-the-stdlib-only-decision)
  - [What you gain](#what-you-gain)
  - [What it actually costs](#what-it-actually-costs)
  - [When the answer should be "yes, add the library"](#when-the-answer-should-be-yes-add-the-library)
- [Earning complexity gradually: the feature arc](#earning-complexity-gradually-the-feature-arc)
- [Resisting AI sprawl in practice](#resisting-ai-sprawl-in-practice)
- [Documentation as a feedback loop](#documentation-as-a-feedback-loop)
- [The "should this exist?" decision framework](#the-should-this-exist-decision-framework)
- [Key takeaways](#key-takeaways)
- [Where to next](#where-to-next)

---

## Why AI makes restraint harder, not easier

Before AI, restraint was partly *enforced by friction*. Writing a feature took
hours; adding a dependency meant reading docs and wiring it up; deleting code was
something you did because the alternative was maintaining it by hand. The cost of
"more" was paid up front, in your own time, which made you naturally reluctant.

AI removes that friction almost entirely. You can ask Claude for a new flag, a
new script, a whole new subsystem, and have it — written, plausibly correct, with
tests — in minutes. That is genuinely wonderful. It is also a trap, because **the
cost of code was never mostly in writing it.** The cost is in *living with it*.
And living with it doesn't get cheaper just because the AI wrote it.

So the discipline shifts. When generation was expensive, the scarce resource was
*production* and your judgment mostly went into "can I afford to build this?" Now
that generation is cheap, the scarce resource is *attention and trust* — your
ability to understand, verify, and stand behind what exists — and your judgment
has to go into a different question:

> **Not "can I build this?" but "should this exist at all?"**

That question is the entire subject of this part. The AI will never ask it for
you. It is your job, and it is the job that most separates a dependable tool from
a sprawling one.

---

## The true cost of every feature, file, and dependency

When you're deciding whether something should exist, it helps to have the full
bill in front of you. The purchase price — the time to write it — is the smallest
line item. Here's the rest, the part that recurs forever:

- **Maintenance.** Every line of code can break when something around it changes —
  an API tweak, a Python upgrade, a refactor next door. More code means more
  things that can go stale, and more places to update when one assumption shifts.
- **Security surface (a.k.a. attack surface).** *Plain language:* every piece of
  outside code you pull in, and every input your tool accepts, is a door someone
  could come through. A bug or a malicious change in a dependency you didn't write
  becomes *your* problem the moment you depend on it. Fewer doors, fewer locks to
  check.
- **Cognitive load.** Someone — often future-you — has to *hold the tool in their
  head* to use or change it safely. Every extra flag, file, and concept is more to
  understand before anyone can move with confidence. A tool with five flags is
  learnable in a sitting; a tool with fifty is a part-time job.
- **Testing burden.** Per [Pillar 3](05-five-pillars.md), real code deserves real
  tests, and the project targets ≥90% coverage. That's not free: every feature you
  add is a feature you must *also* test, across its happy path and its unhappy
  ones, and keep green forever. Untested code is worse than no code; *tested* code
  is a standing commitment.
- **Documentation drift.** Anything that exists should be documented (the project
  keeps a catalog of every command in its rules file and a `docs/` reference). A
  feature nobody documents is a trap; a feature whose docs fall out of date is a
  *worse* trap, because now the docs actively lie.

Notice that **AI changes only the first line item.** It writes the code fast. It
does not maintain it for you across the next year, shrink the attack surface,
reduce what a human must understand, or keep the docs honest. So when you let the
ease of generation tempt you into "sure, add it," you are signing up for the
whole recurring bill to save the one cost that was already cheap.

> **A useful reframe.** Code is not an asset; it is a liability that occasionally
> does something useful. The useful behavior is the asset. The code is the
> ongoing cost of having it. You want the most behavior for the least code — which
> is exactly why deleting code that no longer earns its keep is a *win*, not a
> loss.

---

## YAGNI: "You Aren't Gonna Need It"

**YAGNI** is an old engineering proverb: *You Aren't Gonna Need It.* It means —
don't build something *now* for a need you only *imagine* you'll have *later*.
Build for the need that is real and present. When the imagined future arrives (if
it ever does), build it then, with the benefit of actually knowing what it needs
to do.

The reason YAGNI works is that **speculative features are almost always wrong.**
You guess at requirements you don't have yet, build to that guess, and then when
the real need shows up it's shaped differently — so now you have code that's both
unused *and* in the way of the thing you actually need. You paid the full
recurring bill (above) for a guess.

YAGNI matters *more* in the AI era, not less, for a subtle reason. When you ask
Claude for a feature, it is often helpful to a fault — it will volunteer adjacent
features it imagines you might want. "I added the export command, and also a
`--format json` option and a `--compress` flag and a config file for default
columns." None of that was asked for. Each piece might be individually reasonable
and even well-written. Collectively, they are sprawl you didn't choose, didn't
need, and now own.

So a YAGNI habit, expressed as prompts you actually use:

> "Build *only* the export-to-CSV command I described. No extra formats, no extra
> flags, nothing speculative. We'll add more if and when we need it."

> "You added three options I didn't ask for. Remove the two we don't have a
> concrete need for right now and keep just `--output-dir`."

This pairs directly with the daily loop's *one goal per turn* hygiene
([Part 6](06-the-daily-loop.md)). A turn that does exactly one thing is a turn
whose output you can fully review — and "exactly one thing" is YAGNI enforced at
the level of a single conversation.

---

## Delete prototypes once they've served their purpose

A **prototype** is a throwaway — a sketch you build to learn something, prove an
idea, or work out a plan, with no intention of keeping it. Prototypes are
fantastic. The mistake is *forgetting they were throwaways* and letting them
linger in the project, where they slowly start to look like real, load-bearing
code that nobody dares delete.

The discipline is simple and ruthless: **a prototype's job is to teach you
something; once you've learned it, the prototype is done — delete it.**

### The "note shuttle"

This project did exactly that, and you can see the moment in its history. Commit
`3322a93`, *"removed note shuttle - no longer needed,"* deleted a single scratch
file — `Sample Data/note shuttle.txt`, 21 lines — and nothing else.

What was it? Not code at all. It was a *planning note* — a place to think out
loud about a feature before building it. The file worked through an idea: some
projects had no task data because the service account wasn't a member of them, so
maybe the tool should detect those projects during a sync, keep a running list,
and then offer to add a service user to them via Asana's "add members" endpoint
and re-fetch. A genuinely reasonable design conversation, parked in a text file
while it was being figured out.

And then it was deleted, because it had **served its purpose**. The thinking it
held had either been built, superseded, or decided against — and once that's
true, the note is no longer a plan, it's clutter. Worse than clutter, actually:
a stale design note that sits in the repo is a liability, because the next person
(or the next AI session, which re-reads the repo) might mistake it for current
intent and act on a plan that's no longer the plan.

The lesson generalizes well beyond text files:

- A scratch script you wrote to explore an API? Once it taught you how the API
  behaves and that knowledge is baked into the real helper, delete the scratch
  script.
- A `_v2` copy of a function you kept "just in case" while trying a new approach?
  Once the new approach is in and tested, delete the old copy. Git remembers it
  if you ever truly need it — that's what Git is *for*
  ([Part 6](06-the-daily-loop.md)).
- A planning note in the repo? Once its decisions are made and recorded where
  they belong (in `CLAUDE.md`, in `docs/`, or in the code itself), delete the
  note.

> **The Git safety net is what makes deletion cheap.** People hoard dead code
> because deleting feels irreversible. It isn't. Every commit is a restorable
> snapshot, so anything you delete is still recoverable from history. That means
> deletion has *almost no downside* and a real upside: a smaller, clearer project
> that's easier for both you and the AI to reason about. When in doubt, delete it
> — you can always `git revert` to bring it back.

### Deprecate, don't just hoard

Sometimes a script isn't a throwaway prototype, but it *is* retired — it did real
work once, and might be a useful reference, but it's no longer part of how the
tool is used day to day. Deleting it outright loses the reference; leaving it in
the main command surface clutters the menu and tempts someone into running
something obsolete.

This project's answer is a `scripts/deprecated/` folder. Retired scripts get
*moved* there rather than left in `scripts/` or wiped. The effect is a clean
separation:

- The **active CLI surface** (what the interactive menu and the catalog present)
  stays small and current — only the things you should actually run.
- **History stays discoverable.** A retired script is still there to read, to
  learn from, or to resurrect, without pretending to be a live command.

That's a middle path between "keep everything" (sprawl) and "delete everything"
(losing useful context). A deprecation folder is restraint *with* a memory: the
main tool shrinks back to what's real, and the old work is parked somewhere
honest about its status.

---

## Saying no to convenient libraries: the stdlib-only decision

Here is the most striking restraint decision in the whole project, and the one
most worth understanding in depth, because it cuts both ways.

The toolkit is **standard-library-only Python.** *Plain language:* the **standard
library** is the code that comes built into Python itself, with nothing extra to
install. A **third-party library** (or "package," or "dependency") is outside code
you pull in to save yourself work — `requests` for HTTP, `pytest` for testing,
and so on. They're popular for good reason: they're convenient, battle-tested, and
often let you do in two lines what the standard library does in twenty.

This project **declines them.** The rule, written into `CLAUDE.md`, is: standard
library only, no third-party packages without prior approval. You can *see* it
holding in the network code — instead of reaching for the wildly popular
`requests` package, `src/lib/http_client.py` builds on Python's built-in `urllib`:

```python
# src/lib/http_client.py — only built-in modules
import json, ssl, time
from urllib import error, parse, request
```

And instead of `pytest`, the project's many tests run on the built-in `unittest`
(`python3 -m unittest discover tests`). Every "obvious" convenience library was,
deliberately, *not* added.

### What you gain

This is not asceticism for its own sake. The payoffs are concrete and they map
directly onto the cost list from earlier:

- **Zero-install portability.** The tool runs anywhere Python 3.11+ exists, with
  *nothing to install.* No `pip install`, no dependency resolution, no
  "works-on-my-machine" version mismatches. For a tool that gets handed to
  different operators migrating different clients, that's a real, recurring
  win — the setup in [Part 4](04-getting-set-up.md) is genuinely just "have
  Python, clone, run."
- **A tiny security/attack surface.** Every dependency you don't have is a door
  you don't have to guard. You are not exposed to a compromised package, a
  surprise breaking change, or a transitive dependency (a dependency *of* your
  dependency) you never even chose. The supply chain you have to trust is exactly:
  Python itself. That is a powerful thing to be able to say about a tool that
  writes to a client's live system.
- **Less to keep current.** No dependency upgrades, no security advisories to
  chase, no lockfiles to maintain. The maintenance line item shrinks toward zero.
- **Full comprehensibility.** Everything the tool does, it does in code you can
  read, in modules that ship with the language. There's no opaque black box doing
  something you can't inspect.

### What it actually costs

Restraint is a *trade-off*, not a free win, and being honest about the cost is
part of the discipline (this is the same point [Part 9](09-pitfalls-and-limits.md)
makes about constraints cutting both ways). The stdlib-only rule means:

- **You write more code by hand.** The retry-and-rate-limit logic, the pagination,
  the multipart upload handling — things `requests` or a Jira SDK would hand you —
  the project had to build itself, in helpers like `http_client.py` and
  `jira_client.py`. That's more code to write, and more code to *maintain and
  test* (the very bill we said matters).
- **You re-solve solved problems.** Some of what you build by hand is genuinely
  well-trodden ground that a library has already gotten right, including edge
  cases you might miss the first time.
- **You forgo ergonomic niceties.** Libraries often have pleasant, expressive APIs
  refined over years. The stdlib equivalents can be clunkier.

The reason the trade still comes out *favorable for this project* is the blast
radius ([Part 3](03-before-you-write-any-code.md)). This is a high-stakes tool
that writes to a live system and gets run by different people against different
clients. For *that* profile, "installs nowhere and depends on nothing" and "the
only code I have to trust is the code I can read" are worth a lot — enough to pay
the extra hand-written code. The reusable-helpers pillar
([Part 5](05-five-pillars.md)) then softens the cost: because the hand-written
HTTP and Jira logic lives in *one* tested helper each and is reused everywhere,
you pay the "write it yourself" cost once, not in every script.

### When the answer should be "yes, add the library"

The point is not "never use a dependency." That would be its own dogma. The point
is to make it a *decision* with a justification, instead of a reflex. A library
is worth adding when:

- it solves a genuinely hard problem you'd get wrong by hand (cryptography is the
  classic example — *never* hand-roll that),
- it's well-maintained and widely trusted, so its own bill is low,
- and the convenience it buys clearly outweighs the maintenance, security, and
  comprehension costs *for your blast radius*.

The discipline is simply that the bar is **real and high**, and that you pause to
clear it deliberately. In this project the bar was set by a written rule — *no
third-party packages without prior approval* — which turns "should we add this
dependency?" from an offhand `pip install` into a conscious conversation. That's
the whole trick: make adding weight require a decision, so weight only gets added
when it's earned.

---

## Earning complexity gradually: the feature arc

The most encouraging part of this whole discipline is that the toolkit did **not**
start out as the sophisticated, resilient, live-Jira creator it is now. It started
small and *earned* every layer of complexity, adding each only when reality
demanded it. You can read the whole arc in the commit history — and reading it is
a masterclass in "don't front-load complexity."

Walk it in order:

1. **Export to a spreadsheet first (read-only).** Long before it could create
   anything, the tool's job was to read Asana data and write it to CSV files for a
   human to inspect and import by hand. This is the safest possible starting
   point: it *cannot* damage a live system, because it doesn't write to one
   ([Part 3's](03-before-you-write-any-code.md) "read-only before write"). All the
   early value — and all the early learning about the data's shape — came with
   essentially zero blast radius.

2. **Then create directly via REST (`62bc708`).** Only once the export was solid
   did the project add the riskier capability: *"create team/project Jira work
   items via REST API"* — a direct-to-Jira alternative to the CSV-import path. This
   is the moment the tool crossed from Tier-1/2 (read-only / local) into Tier-3
   (writes to a live system). Crucially, it didn't *replace* the export; it built
   *on top of* the same row-building logic the exporter already had. The safe thing
   came first and stayed; the risky thing was layered on.

3. **Then interactive field mapping (`5599c9a`).** *"add interactive `--map-fields`
   field mapping with id support."* Once issues could be created, a real need
   surfaced: matching Asana's custom fields to Jira's fields isn't one-size-fits-
   all. So a feature was added to let the operator map columns to Jira field ids
   interactively (with a fallback to name-based resolution). Note the *ordering*:
   this wasn't speculated up front; it was added when actual use revealed the
   mismatch. YAGNI in action.

4. **Then resilience: requeue + assignee fallback (`b965761`).** *"fall back to
   unassigned + requeue rate-limited Jira creations."* Real runs against a live
   Jira surfaced two failure modes — an "active" user who *can't* actually be
   assigned issues in the target project, and rate-limiting under load. So two
   always-on safety behaviors were added: drop just the bad assignee and keep going
   ([Pillar 4's](05-five-pillars.md) graceful fallback), and requeue a
   rate-limited creation to retry later instead of losing it. This complexity was
   *earned by reality* — these aren't problems you'd reliably predict from an
   armchair; they're problems the live system taught the tool.

5. **Then `--reset` (`effd0ec`).** *"add `--reset` to recreate deleted team/project
   Jira issues."* Operators sometimes delete issues in Jira and want to recreate
   them — but the tool remembers what it already made (idempotency,
   [Pillar 4](05-five-pillars.md)) and would otherwise skip them. So a deliberate
   `--reset` was added to clear those remembered mappings for re-creation. Again:
   a flag added to serve a need that *showed up*, not a flag imagined in advance.

6. **Then the `--clear-first` two-pass repair (`2616b09`).** *"add `--clear-first`
   two-pass mode to personal-project URL repair."* The most specialized of all —
   a repair operation that, for extra safety, splits into two passes: clear a field
   on every in-scope issue *first*, then set the new value, so no stale half-updated
   value can survive if the set pass misses something. This is sophisticated,
   defensive engineering — and it arrived *last*, on top of everything else, for a
   narrow, concrete situation. You would never have built this on day one; you
   couldn't have, because you didn't yet know you needed it.

Read top to bottom, the shape is unmistakable: **safe and simple first, risky and
sophisticated last, each step justified by a need that had actually appeared.** No
step front-loaded complexity for an imagined future. Every layer sat on a tested
foundation below it. That is how you end up with a tool that is *both* powerful
*and* trustworthy — you let it grow into its power instead of starting there.

> **The contrast.** Imagine the opposite: starting by designing the
> "perfect" all-singing Jira creator with field mapping, reset, two-pass repair,
> and every resilience behavior, *before* you'd ever read a single Asana record or
> created a single test issue. You'd be guessing at all of it. Half your guesses
> would be wrong, the wrong half would be in the way, and the whole thing would be
> too big to review or trust. Earning complexity gradually isn't slower — it's the
> path that actually *arrives*.

---

## Resisting AI sprawl in practice

"AI sprawl" is what you get when the ease of generation runs unchecked: a project
that accretes features, flags, files, and dependencies faster than anyone
decides they should exist. It's the specific failure mode this whole part is
guarding against. Here's how to resist it, concretely, in the daily loop:

- **Make "should this exist?" a step.** Before accepting a new file, flag, or
  dependency, run it through the framework below. The friction AI removed, you
  re-add deliberately, as a question.
- **Review for scope, not just correctness.** During step 6 of the loop
  ([Part 6](06-the-daily-loop.md)), don't only ask "is this right?" — ask "is this
  *more than I asked for*?" The single most useful thing a reviewer can catch is a
  change that's bigger than its goal. Surprising scope is sprawl arriving.
- **Prune as a regular habit, not an emergency.** Periodically ask Claude:
  *"What in this project is no longer used — dead code, flags nobody runs,
  prototypes that already taught us what we needed? List candidates for deletion."*
  Then delete with confidence, because Git is your net.
- **Resist the helpful volunteer.** When Claude adds adjacent features you didn't
  ask for, push back: *"I only asked for X. Remove the rest unless we have a
  concrete need."* Helpfulness is a feature; unrequested scope is a cost.
- **Let the rules file carry the constraint.** Because `CLAUDE.md` says
  "stdlib only" and is re-read every session, the AI inherits the restraint by
  default — you're not re-fighting the dependency question every turn. Writing a
  constraint down once is how you make restraint *scale* across sessions.

The throughline: AI made *adding* free, so you have to make *deciding* the part
that takes effort. The tool that stays trustworthy is the one where every piece
in it was chosen, not just generated.

---

## Documentation as a feedback loop

There's a close cousin to all of this restraint, and it's about *curation* —
deciding deliberately what your documentation says, because that documentation
isn't only for humans. **Claude re-reads it every session.**

The project keeps two kinds of curated documentation, and a key event in its
history shows the curation happening:

- **`CLAUDE.md`** (the rules file; the file is `AGENTS.md`, symlinked) holds the
  standards *and* a running **catalog of every CLI script** — its inputs, outputs,
  and options. It auto-loads at the start of each session, and Claude re-reads it,
  so it's not passive reference — it's *active context for the AI's next
  contribution* ([Pillar 1, Part 5](05-five-pillars.md)).
- **The `docs/` reference**, which commit `0f397ca` — *"split CLI reference into
  docs/ topic files"* — carved out of a single bloated README into focused topic
  files: `docs/sync-commands.md`, `docs/reports.md`, and `docs/jira-integration.md`.
  The README became a slim landing page; each topic got its own home.

That split is itself an act of restraint and curation. A 500-line README that
tries to be everything is hard for a human to navigate *and* expensive for an AI
to load and reason over. Three focused files, each about one thing, are easier on
both. The same instinct that prunes dead code prunes bloated docs — into
something smaller and sharper.

And here's the feedback loop, which is the payoff: **because Claude re-reads these
docs each session, keeping them accurate literally makes the AI's next output
better.** When the script catalog in `CLAUDE.md` is current, Claude starts each
session knowing what already exists — so it reuses helpers instead of
re-inventing them ([Pillar 2](05-five-pillars.md)), proposes plans that fit the
real codebase ([Part 6](06-the-daily-loop.md)), and doesn't suggest building
something that's already there. Stale docs do the opposite: they teach the AI
wrong things, and it acts on them.

This closes the circle with the rest of this part. *Deciding what not to build* and
*curating what you document* are the same discipline pointed at two surfaces — the
code and the prose. In both, less-but-accurate beats more-but-drifting. And in
both, the investment compounds: a tool that's small and a doc set that's true make
*every* future session — human or AI — start from a better place.

> **The practical habit.** Whenever you add, delete, or change a command, update
> the catalog and `docs/` in the *same* breath — ask Claude to do it: *"Add this
> script to the catalog in `CLAUDE.md` and to `docs/sync-commands.md` with a
> one-line description of its inputs and outputs."* And when you delete something,
> delete its docs too. Documentation that drifts is documentation that lies — to
> you and to the AI.

---

## The "should this exist?" decision framework

Pull it all together into something you can actually use. Before you accept a new
feature, file, flag, or dependency, walk it through these questions. If it can't
clear them, it doesn't exist yet — and that's a fine answer.

```
SHOULD THIS EXIST? — a checklist before you accept new code

NEED
[ ] Is there a REAL, PRESENT need — not an imagined future one? (YAGNI)
[ ] Did I ask for this, or did the AI volunteer it? (Volunteered ⇒ scrutinize hard.)
[ ] If I removed it, what concretely breaks today? (If "nothing," that's a tell.)

COST (the recurring bill — AI only paid the first line)
[ ] Maintenance: what has to change when something around this changes?
[ ] Security surface: does this add a dependency or accept new input? More doors?
[ ] Cognitive load: is this one more thing a human must understand to use the tool?
[ ] Testing burden: am I willing to test this — happy AND unhappy paths — forever?
[ ] Docs: will I document it now and keep the docs honest?

DEPENDENCIES (extra bar)
[ ] Could the standard library do this acceptably? (For this project: usually yes.)
[ ] If a library: is it solving a genuinely hard problem, well-maintained, trusted?
[ ] Does its convenience clearly beat its cost FOR THIS BLAST RADIUS?

TIMING
[ ] Is the safe/simple version already in place before I add the risky/complex one?
[ ] Am I earning this complexity in response to reality — or front-loading a guess?

PROTOTYPES
[ ] If this was a prototype/scratch/planning note: has it served its purpose?
    If yes ⇒ DELETE it (Git remembers). Don't let throwaways become permanent.

DEFAULT
[ ] When genuinely unsure ⇒ default to LESS. Add later when the need is undeniable.
```

A few notes on using it:

- **The default is "no."** Like the confirmation prompt that defaults to *no*
  ([Part 6, step 5](06-the-daily-loop.md)), this framework defaults to *not
  adding*. Things should have to *earn* their way in. That's the whole posture.
- **"Volunteered by the AI" is a yellow flag, not a red one.** The AI's
  suggestions are often good. But they bypass the friction that used to make you
  decide — so they deserve a *deliberate* yes, not an automatic one.
- **Match the rigor to the blast radius.** A read-only local report can clear this
  bar loosely. A new dependency in a tool that writes to a client's live Jira
  should clear it strictly. This is [Part 3's](03-before-you-write-any-code.md)
  blast-radius thinking applied to *additions*, not just safeguards.

---

## Key takeaways

- **Restraint is the discipline AI makes harder.** Generation is now nearly free,
  so the cost of code shifts entirely to *living with it* — maintenance, security
  surface, cognitive load, testing, and docs. The job is no longer "can I build
  this?" but "**should this exist?**" The AI will never ask that for you.
- **Every feature, file, and dependency carries a recurring bill.** AI pays only
  the smallest line item (writing it). You pay the rest forever. Count the whole
  bill before you accept anything new.
- **Delete prototypes once they've served their purpose.** The toolkit removed the
  "note shuttle" planning file (`3322a93`) the moment its thinking was done, and
  parks retired scripts in `scripts/deprecated/` so history stays discoverable
  without cluttering the live tool. Git makes deletion cheap and reversible —
  when in doubt, delete.
- **Saying no to convenient libraries is a real, two-sided trade.** The stdlib-only
  rule buys zero-install portability and a tiny attack surface, at the cost of more
  hand-written code. It's the right call *for this blast radius* — and the point is
  that it was a *decision*, with a bar high enough that weight only gets added when
  it's earned.
- **Earn complexity gradually.** Walk the arc: export to CSV first, then direct
  REST creation (`62bc708`), then interactive field mapping (`5599c9a`), then
  resilience — requeue + assignee fallback (`b965761`), then `--reset` (`effd0ec`),
  then the `--clear-first` two-pass repair (`2616b09`). Safe and simple first,
  risky and sophisticated last, each layer justified by a need that actually
  appeared. Never front-load a guess.
- **Resist AI sprawl deliberately.** Make "should this exist?" a step, review for
  scope (not just correctness), prune as a habit, push back on unrequested
  features, and let `CLAUDE.md` carry the constraint so you don't re-fight it every
  session.
- **Documentation is a feedback loop, so curate it.** Splitting docs into focused
  files (`0f397ca`) and keeping `CLAUDE.md`'s command catalog current isn't busywork
  — Claude re-reads those docs every session, so accurate, lean docs literally make
  the AI's next contribution better. Less-but-true beats more-but-drifting, in code
  and in prose alike.
- **Default to less.** "What we chose not to build" is a feature, not a gap.

---

## Where to next

- **Previous:** [06 — The daily loop](06-the-daily-loop.md)
- **Up:** [The Claude Code playbook (parent)](../claude-code-playbook.md)
- **Next:** [08 — A recipe for your own project](08-recipe-for-your-own-project.md)

Other parts in the series:
[01 — What is vibe coding](01-what-is-vibe-coding.md)
· [02 — Speed inside guardrails](02-speed-inside-guardrails.md)
· [03 — Before you write any code](03-before-you-write-any-code.md)
· [04 — Getting set up](04-getting-set-up.md)
· [05 — The five pillars](05-five-pillars.md)
· [09 — Pitfalls and honest limits](09-pitfalls-and-limits.md)
· [Series index](README.md)
