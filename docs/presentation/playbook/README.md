# Vibe Coding, Safely — the deep-dive series

This folder expands each section of the
[beginner playbook](../claude-code-playbook.md) into its own in-depth,
standalone document. Each one is written **layered** — it opens accessible
enough for a non-engineer, then goes deeper with clearly-marked "for engineers"
sections — and each is substantial enough to stand on its own or become its own
presentation.

The running example throughout is this repository: a standard-library-only
Python toolkit that migrates an organization from **Asana → Jira**, including
creating items in *live* Jira (a high-stakes, "high blast radius" job that makes
the safety method matter).

## Read in order

1. [What "vibe coding" means here](01-what-is-vibe-coding.md) — the disciplined-vs-reckless spectrum, failure modes, and the blast-radius framing.
2. [Speed inside guardrails](02-speed-inside-guardrails.md) — the philosophy: AI speed *with* engineering discipline, and how the five pillars interlock.
3. [Before you write any code](03-before-you-write-any-code.md) — naming the blast radius, choosing standards deliberately, and the tools you'll touch.
4. [Getting set up](04-getting-set-up.md) — a beginner-proof, end-to-end setup walkthrough (terminal → Python → Claude Code → credentials → tests → first run).
5. [The five pillars](05-five-pillars.md) — the core, in depth: the `CLAUDE.md` contract, reusable helpers, tests, runtime guardrails, and the interactive CLI.
6. [The daily loop](06-the-daily-loop.md) — how to actually work with Claude: plan → generate + test → run → dry-run → review → commit, with a prompting playbook.
7. [Deciding what NOT to build](07-deciding-what-not-to-build.md) — restraint as a discipline: the cost of every feature, pruning prototypes, earning complexity.
8. [A recipe for your own project](08-recipe-for-your-own-project.md) — the capstone: a concrete, ordered, copyable playbook for starting your own project.
9. [Pitfalls and honest limits](09-pitfalls-and-limits.md) — the credibility check: real tradeoffs, where the AI fails, and how guardrails contain them.

## How to use this series

- **Reading to learn?** Start at the [playbook](../claude-code-playbook.md) for
  the whole picture, then dive into the parts you care about.
- **Presenting?** Each document maps cleanly onto a talk. The condensed slide
  deck lives at [`../claude-code-vibe-coding.md`](../claude-code-vibe-coding.md)
  (Marp-compatible — see [`../README.md`](../README.md) for how to render it).
- **Doing it yourself?** Read Part 8, then keep the others open as references.

> Every figure, file name, flag, and commit hash cited in these documents was
> verified against this repository at the time of writing. If you reuse them
> later, re-confirm with `git log` and the current source — and remember the
> stale-docs pitfall in [Part 9](09-pitfalls-and-limits.md).
