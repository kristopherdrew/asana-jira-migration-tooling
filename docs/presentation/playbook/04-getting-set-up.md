# Getting set up (full walkthrough)

*From a fresh Mac to a running, credentialed, test-passing tool — every command
explained, every "what should I see?" answered, and a troubleshooting note for
each step. The most hands-on part of the series: if you can copy and paste, you
can finish this.*

> Part 4 of 9 in the Vibe Coding, Safely series

---

Everything up to now has been thinking. This part is doing. By the end you will
have the example project — the Asana→Jira migration toolkit — installed on your
machine, credentialed safely, verified healthy by its own test suite, and
running through its guided menu. You will also understand *why* each step exists,
so that when you set up your own project later you're not just imitating
keystrokes.

This is the longest and most literal part of the series on purpose. A
non-engineer should be able to follow it line by line without ever feeling
lost. So we'll do three things at every step:

- **What** to type, and what it does in plain language.
- **What success looks like** — the actual output or signal that it worked.
- **What to do if it didn't** — the most common failures and their fixes.

Engineers: there are clearly-marked **For engineers** asides throughout (PATH,
`PYTHONPATH`, multi-client layouts, the credential resolution code). Skim the
narrative and read those.

> **Convention.** Lines that start with `$` are commands you type into the
> terminal. **Don't type the `$` itself** — it's just the standard way to show
> "this is a command." Everything here targets **macOS**, which is what this
> project is built and documented for. The ideas transfer to Linux almost
> unchanged and to Windows with a different terminal (WSL or PowerShell), but the
> exact commands shown are the macOS ones.

---

## Table of contents

- [The shape of what we're about to do](#the-shape-of-what-were-about-to-do)
- [Step 0 — Open the terminal](#step-0--open-the-terminal)
- [Step 1 — Make sure you have Python 3.11+](#step-1--make-sure-you-have-python-311)
- [Step 2 — Install Node.js, npm, and Claude Code](#step-2--install-nodejs-npm-and-claude-code)
- [Step 3 — Get the project (git clone and cd)](#step-3--get-the-project-git-clone-and-cd)
- [Step 4 — Create and activate a virtual environment](#step-4--create-and-activate-a-virtual-environment)
- [Step 5 — Provide your credentials safely](#step-5--provide-your-credentials-safely)
  - [The big idea: secrets live outside the project](#the-big-idea-secrets-live-outside-the-project)
  - [5a — Create the home-directory env file](#5a--create-the-home-directory-env-file)
  - [5b — Lock the file down with chmod 600](#5b--lock-the-file-down-with-chmod-600)
  - [5c — Point this project copy at the file](#5c--point-this-project-copy-at-the-file)
  - [How the tool decides which credentials to use (resolution order)](#how-the-tool-decides-which-credentials-to-use-resolution-order)
  - [Why this design: the multi-client rationale](#why-this-design-the-multi-client-rationale)
  - [The easier alternative: the interactive setup flow](#the-easier-alternative-the-interactive-setup-flow)
- [Step 6 — Confirm health by running the tests](#step-6--confirm-health-by-running-the-tests)
- [Step 7 — Launch the guided menu](#step-7--launch-the-guided-menu)
- [Step 8 — Running an individual command](#step-8--running-an-individual-command)
- [Step 9 — Cleanup after a job](#step-9--cleanup-after-a-job)
- [The complete copy-paste quick start](#the-complete-copy-paste-quick-start)
- [Common errors and fixes](#common-errors-and-fixes)
- [Key takeaways](#key-takeaways)
- [Where to next](#where-to-next)

---

## The shape of what we're about to do

Before the details, here is the whole journey on one page so the individual
steps have somewhere to hang. You don't need to understand it yet — just notice
that there are only nine steps, and most are one or two commands.

| Step | What you do | One-line reason |
|---|---|---|
| 0 | Open the terminal | The text window where commands go |
| 1 | Check / install Python 3.11+ | The language the tool is written in |
| 2 | Install Node.js + Claude Code, sign in | The AI assistant runs on Node |
| 3 | `git clone` the project, `cd` into it | Get a copy on your machine |
| 4 | Create + activate a `.venv` | A private sandbox for this project |
| 5 | Put credentials in a safe home-dir file | Secrets never go in the project folder |
| 6 | Run the tests | Prove the code is healthy before doing anything real |
| 7 | Launch the guided menu | The friendly, walk-you-through way to use the tool |
| 8 | (As needed) run a single command | The power-user way, with `--help` |
| 9 | Clean up after the job | Remove sensitive local data when done |

Steps 0–4 are generic "set up any Python project" steps. Step 5 is the one that
actually matters for safety. Steps 6–9 are how you live with the tool day to
day.

---

## Step 0 — Open the terminal

**What it is.** The **terminal** is a plain text window where you type commands
instead of clicking buttons. It feels intimidating the first time and ordinary
by the third. Everything in this guide happens here.

**What to do.** On a Mac:

1. Press `Cmd` and `Space` together. A search bar appears in the middle of the
   screen (this is Spotlight).
2. Type `Terminal`.
3. Press `Enter`.

A window opens with some text and a **blinking cursor**. The line with the cursor
is called the **prompt** — it's the terminal saying "I'm ready for a command."

**What success looks like.** You see something like this, ending in a `$` or `%`
and a blinking cursor:

```
yourname@MacBook ~ %
```

The `~` means "your home folder" (more on that in Step 5). The `%` (newer Macs)
or `$` (older ones) is where your typing goes. Throughout this guide we write the
prompt as `$` regardless.

**Troubleshooting.**

- *I can't find Terminal.* It also lives in `Applications → Utilities →
  Terminal`. Drag it to your Dock so it's one click away next time.
- *The window looks scary / all black.* That's normal. Nothing you type and run
  here can hurt your Mac unless it's a destructive command — and this guide flags
  the only destructive commands (the cleanup ones in Step 9) clearly.

> **For engineers.** Any terminal works — iTerm2, the VS Code integrated
> terminal, tmux. The project assumes a POSIX-ish shell; the commands here are
> written for `zsh`/`bash`, which is the macOS default.

---

## Step 1 — Make sure you have Python 3.11+

**What it is.** **Python** is the programming language the toolkit is written in.
The project requires **Python 3.11 or newer**, and it's always invoked with the
command `python3` (not bare `python`, which on some machines points at an old
version 2 or at nothing at all). The README and the project's rules file both
insist on `python3` for exactly this reason.

**What to do — check first.**

```
$ python3 --version
```

**What success looks like.** It prints a version that is 3.11 or higher, for
example:

```
Python 3.11.9
```

If the number after the first dot is `11`, `12`, `13`, or higher, you're done
with this step — skip to Step 2.

**What to do if it's missing or too old.** If you see `command not found`, or a
version like `3.9.x`, install a current Python. Two easy options:

- **From python.org (simplest for non-engineers).** Go to
  [python.org/downloads](https://www.python.org/downloads/), download the latest
  macOS installer, and run it like any other app. Then **close and reopen the
  terminal** and run `python3 --version` again.
- **With Homebrew (if you have it, or don't mind installing it).** Homebrew is a
  popular Mac package manager. If you have it:

  ```
  $ brew install python
  ```

  If you don't have Homebrew, the python.org installer is the path of least
  resistance.

**Troubleshooting.**

- *`python3 --version` prints the new version but `python` still prints an old
  one.* That's fine and expected. This project never uses `python` — always
  `python3`. Don't try to "fix" `python`; just use `python3`.
- *I installed it but the terminal still says `command not found`.* You almost
  certainly need to **close and reopen the terminal** so it picks up the new
  installation. If it still fails, see the *For engineers* note below.

> **For engineers.** `command not found` after install is a `PATH` problem. The
> python.org installer adds `/Library/Frameworks/Python.framework/Versions/3.11/bin`
> (and `/usr/local/bin`) to your `PATH` via a snippet in your shell profile
> (`~/.zprofile` / `~/.zshrc`); a new shell is required to load it. Check with
> `which python3` and `echo $PATH`. If you juggle versions, `pyenv` is the
> cleaner long-term answer, but it's overkill for just running this tool.

---

## Step 2 — Install Node.js, npm, and Claude Code

**What it is.** **Claude Code** is Anthropic's official command-line AI
assistant — the thing you talk to in plain English that reads and writes files
and runs commands for you, right inside the terminal. It's distributed through
**npm**, which is the package installer that comes with **Node.js**. So you need
Node.js first, then Claude Code, then a one-time sign-in.

Note that you do **not** need Claude Code merely to *run* the toolkit — the
toolkit is plain Python. You need Claude Code to *develop* it (or your own
project) the way this series describes. Most readers will want it, so we install
it here.

**What to do — get Node.js (and npm) if you don't have it.** Check first:

```
$ npm --version
```

If that prints a number (any recent version is fine), you already have npm and
can skip to the install command below. If it says `command not found`, install
Node.js from [nodejs.org](https://nodejs.org/) — download the macOS installer,
run it, then close and reopen the terminal. npm comes bundled with Node, so
installing Node gives you both.

**What to do — install Claude Code.**

```
$ npm install -g @anthropic-ai/claude-code
```

The `-g` means "global" — install it once for your whole user account so you can
run it from any folder.

**What to do — launch it and sign in.** Move into a project folder (you'll do the
real one in Step 3; for now any folder is fine to test the login) and run:

```
$ claude
```

The first time, it walks you through **signing in** — follow the on-screen
prompt, which opens your browser to authenticate with your Anthropic account.
After that first sign-in, launching `claude` from inside any project folder just
works.

**What success looks like.** After install, `claude --version` prints a version
number. After `claude`, you get an interactive Claude Code session (a prompt
waiting for you to type a request). You can type `/help` to see what it can do,
and exit with `/exit` or `Ctrl+C`.

**Troubleshooting.**

- *`npm install -g` fails with a permissions/`EACCES` error.* This is the classic
  npm-global-permissions issue. The cleanest fix is to use a Node version manager
  (`nvm`) so global installs go in your home directory, or follow npm's official
  guidance for changing the global prefix. Avoid `sudo npm install -g` if you
  can — it tends to cause more permission problems later.
- *`claude: command not found` right after a successful install.* Same `PATH`
  story as Python — close and reopen the terminal. If it persists, run
  `npm bin -g` to see where global binaries live and confirm that folder is on
  your `PATH`.
- *The sign-in browser page doesn't open.* The terminal usually also prints a URL
  you can copy into a browser manually.

> **For engineers.** `npm install -g @anthropic-ai/claude-code` drops the `claude`
> binary into your global npm bin. On nvm setups that's
> `~/.nvm/versions/node/<ver>/bin`; on a Homebrew Node it's under the Homebrew
> prefix. Auth is per-user, not per-project. If your org uses Claude via a
> different access path (e.g., an API key or a gateway), follow your internal
> setup instead of the interactive sign-in — but the install command is the same.

---

## Step 3 — Get the project (git clone and cd)

**What it is.** **Git** is software that tracks every change to a project and
lets you make and download copies. **Cloning** means downloading a complete copy
of the project — all its files and its full history — onto your machine.
**`cd`** stands for "change directory" and means "step into this folder."

**What to do.**

```
$ git clone <the-project-repository-url>
$ cd asana_jira_migration
```

Replace `<the-project-repository-url>` with the actual address of the
repository, which your team or the project's host (e.g., Bitbucket or GitHub)
gives you. It looks like `https://…/asana_jira_migration.git` or an `ssh`
address.

**What each command does.**

- `git clone …` downloads the project into a new folder named
  `asana_jira_migration` inside wherever you currently are. (Run `pwd` —
  "print working directory" — beforehand if you want to know where that is; many
  people `cd ~/Desktop` or `cd ~/code` first so the project lands somewhere tidy.)
- `cd asana_jira_migration` steps you into that new folder. From now on, the
  terminal's "current location" is the project, which is what every later command
  assumes.

**What success looks like.** `git clone` prints progress lines and ends without
an error. After `cd`, your prompt usually shows the folder name, and you can
confirm you're in the right place:

```
$ ls
```

You should see project files and folders like `README.md`, `scripts/`, `src/`,
`tests/`, `docs/`, and `CLAUDE.md`. Seeing `CLAUDE.md` is a good sign — that's
the rules file the AI reads automatically (Part 5).

**Troubleshooting.**

- *`git: command not found`.* Install git. On a Mac, simply running `git` once
  may prompt you to install the Apple "Command Line Tools," which include git.
  Otherwise `brew install git`.
- *`Permission denied (publickey)` or an authentication error.* The repository is
  private and you need access. Use the `https://` clone URL and sign in when
  prompted, or set up an SSH key with your git host. Ask whoever gave you the
  repo URL which method your team uses.
- *`fatal: destination path 'asana_jira_migration' already exists`.* You already
  have a copy in this location. Either `cd` into the existing one, or clone into a
  differently-named folder: `git clone <url> asana_jira_migration_2`.

> **For engineers.** Nothing exotic here. If you'll run several client migrations,
> you may deliberately keep multiple clones (or git worktrees) side by side —
> that's the whole reason the per-copy `.asana_jira_env_path` pointer in Step 5
> exists. Note that the project's working branch is feature-based off `main`;
> clone gives you the default branch, which is fine for setup.

---

## Step 4 — Create and activate a virtual environment

**What it is.** A **virtual environment** (or "venv") is a private, throwaway
sandbox that belongs to just this one project. It's a folder — conventionally
named `.venv` — that holds an isolated copy of Python and any packages the
project might pin. Using one means this project can never accidentally interfere
with your system Python or with other projects, and you can wipe it clean by
deleting one folder.

This particular project uses **only Python's standard library** (no third-party
packages at all), so there is literally nothing extra to install into the
sandbox. We still create one, because it's good hygiene: it keeps the project's
Python invocation consistent and clean, and it's the habit you'll want for every
other project that *does* need packages.

**What to do.** From inside the project folder (you're already there from
Step 3):

```
$ python3 -m venv .venv
$ source .venv/bin/activate
```

**What each command does.**

- `python3 -m venv .venv` creates the sandbox in a new hidden folder called
  `.venv`. (The leading dot just makes it hidden in Finder; it's still there.)
- `source .venv/bin/activate` "enters" the sandbox for this terminal session.

**What success looks like.** Your prompt gains a `(.venv)` prefix, like:

```
(.venv) $
```

That prefix is your at-a-glance proof that the sandbox is active. From now on,
`python3` in this terminal means "the project's sandboxed Python."

**Leaving and re-entering.** To step out of the sandbox, type:

```
$ deactivate
```

The `(.venv)` prefix disappears. **The sandbox is not permanent across terminal
windows** — each time you open a new terminal to work on the project, re-run
`source .venv/bin/activate` (after `cd`-ing into the project). You do **not**
re-run the `python3 -m venv .venv` create step; that's one-time.

**Troubleshooting.**

- *`source: no such file or directory: .venv/bin/activate`.* The create step
  didn't finish, or you're not in the project folder. Run `ls` to confirm you see
  `.venv`, and run `pwd` to confirm you're in `asana_jira_migration`. Re-run
  `python3 -m venv .venv` if `.venv` is missing.
- *I forgot whether it's active.* Look for `(.venv)` in your prompt. No prefix,
  no sandbox — just re-activate.
- *Do I have to delete it later?* Only if you want to. To remove it entirely:
  `deactivate` then `rm -rf .venv`.

> **For engineers.** `source .venv/bin/activate` simply prepends `.venv/bin` to
> `PATH` and sets `VIRTUAL_ENV`. Because the toolkit is stdlib-only, you won't
> `pip install` anything; the venv is purely for isolation and a stable `python3`.
> If you use `direnv` or auto-activation, point it at `.venv`. The `.venv` folder
> is git-ignored.

---

## Step 5 — Provide your credentials safely

This is the step that matters most for safety, so it's the longest. Read it
slowly. The mechanics are only three small commands; the *why* is what protects
you.

### The big idea: secrets live outside the project

The tool needs **API tokens** — secret, password-like strings — to talk to Asana
and Jira on your behalf. The cardinal rule:

> **Never put secrets inside the project folder.**

Why so emphatic? The project folder is tracked by git. Anything you drop in there
can be accidentally committed and pushed to a shared repository, where it lives
in history essentially forever and is visible to everyone with access. A leaked
Asana or Jira token can let a stranger read or modify your company's data. So we
keep the actual secrets in a protected file in your **home directory** (the `~`
folder, outside any project), and we only put a tiny, non-secret *pointer* inside
the project.

The keys this project understands (verified in its config loader) are exactly
these five:

| Env key | What it is | Notes |
|---|---|---|
| `ASANA_TOKEN` | Your Asana personal access token | Required |
| `ASANA_WORKSPACE_GID` | The ID of the Asana workspace to read | Required |
| `SKIP_SSL_VERIFICATION` | Whether to skip TLS checks | Keep `"false"` |
| `ATLASSIAN_USERNAME` | Your Atlassian (Jira) account email | Needed for Jira steps |
| `ATLASSIAN_TOKEN` | Your Jira API token | Needed for Jira steps |

`ASANA_TOKEN` and `ASANA_WORKSPACE_GID` are the two the tool absolutely requires
to start; the Atlassian pair is needed once you do Jira work; and
`SKIP_SSL_VERIFICATION` should stay `"false"` for normal use.

> **Leave `SKIP_SSL_VERIFICATION` set to `"false"`.** Only ever set it to
> `"true"` as a temporary, local workaround for a broken certificate/trust store,
> and switch it back the moment the environment is fixed. Turning off TLS
> verification means your connection can't be trusted to be private — never a
> default, never in a real run.

### 5a — Create the home-directory env file

We'll create a file called `~/.asana_jira_env_client_a`. The `~` is shorthand for
your home directory; the `.` at the front makes it hidden; and the
`_client_a` suffix is a label so that if you ever migrate more than one client,
you can have `_client_b`, `_client_c`, and so on. (For a single client you could
name it anything — the suffix is just a convention.)

Type this exactly (it's one command that spans several lines), then **replace the
placeholder values** with your real tokens:

```
$ cat > ~/.asana_jira_env_client_a <<'EOF'
export ASANA_TOKEN="your_asana_token"
export ASANA_WORKSPACE_GID="your_workspace_gid"
export SKIP_SSL_VERIFICATION="false"
export ATLASSIAN_USERNAME="you@example.com"
export ATLASSIAN_TOKEN="your_jira_api_token"
EOF
```

**What this does.** `cat > ~/.asana_jira_env_client_a <<'EOF' … EOF` is a way to
write everything between the two `EOF` markers into that file. Each `export …`
line sets one of the five credentials. The values are wrapped in quotes so spaces
and special characters are handled safely.

**What success looks like.** The command returns with no output (silence is
success). You can confirm the file exists and has the right lines:

```
$ cat ~/.asana_jira_env_client_a
```

It should print back the five `export` lines with your real values.

**Where do the tokens come from?**

- The **Asana token** is a *personal access token* you generate in Asana's
  developer settings; it needs read access to the workspace you're migrating.
- The **`ASANA_WORKSPACE_GID`** is the numeric ID of that workspace.
- The **Atlassian username** is the email of your Jira account, and the
  **Atlassian token** is an API token you create in your Atlassian account
  settings.

(Generating these is outside this project's scope — they come from Asana and
Atlassian — but they go into the file above once you have them.)

**Troubleshooting.**

- *I pasted the block and the terminal seems "stuck" waiting.* You probably
  didn't include the final `EOF` on its own line. Type `EOF` and press Enter.
- *I want to edit the file later instead of recreating it.* Open it in any text
  editor, e.g. `nano ~/.asana_jira_env_client_a` (save with `Ctrl+O`, Enter,
  exit with `Ctrl+X`), or use the Credentials category in the interactive menu
  (Step 7).

### 5b — Lock the file down with chmod 600

**What it is.** `chmod 600` changes the file's permissions so that **only you**
can read or write it — not other accounts on the machine, not other programs
running as other users. For a file holding secrets, this is essential.

```
$ chmod 600 ~/.asana_jira_env_client_a
```

**What success looks like.** No output. Confirm with:

```
$ ls -l ~/.asana_jira_env_client_a
```

The permission column should read `-rw-------` (read/write for you, nothing for
anyone else).

**Troubleshooting.**

- *`No such file or directory`.* The file from 5a wasn't created — recheck the
  filename and re-run 5a.

> Good news: when the **interactive setup flow** (below) writes this file for
> you, it applies `chmod 600` automatically. Doing it by hand is only needed when
> you created the file by hand.

### 5c — Point this project copy at the file

Now we drop the tiny, non-secret **pointer** inside the project. It's a one-line
file named `.asana_jira_env_path` that simply contains the path to your real
credentials file. This is the *only* credential-related file that lives in the
project — and it contains no secrets, just a path.

```
$ echo "~/.asana_jira_env_client_a" > .asana_jira_env_path
```

**What this does.** `echo "…" > .asana_jira_env_path` writes that one line into
the pointer file. The project now knows: "for *this* copy, use the credentials at
`~/.asana_jira_env_client_a`."

**What success looks like.** No output. Confirm:

```
$ cat .asana_jira_env_path
```

It prints the single line `~/.asana_jira_env_client_a`.

**Troubleshooting.**

- *Did I just commit a secret?* No — the pointer holds a *path*, not a token. And
  `.asana_jira_env_path` is git-ignored by the project on purpose. The actual
  secrets are safely outside the repo.

### How the tool decides which credentials to use (resolution order)

When any script runs, the project's config loader looks for your credentials in a
fixed order and uses the **first** source that has them. Understanding this order
saves a lot of "why isn't it picking up my token?" confusion. From the actual
loader (`src/lib/config.py`):

1. **Process environment variables first.** If `ASANA_TOKEN` (etc.) is already
   set in your terminal session, that wins over everything. (There's also a
   master override: if the environment variable `ASANA_JIRA_ENV_FILE` is set to a
   path, the loader reads *that* file and skips the steps below.)
2. **The file named by `.asana_jira_env_path`.** This is what we just set up — the
   per-project pointer to your home-dir env file. This is the normal path for
   this guide.
3. **The default `~/.asana_jira_env`.** If there's no pointer file, the loader
   falls back to a single default file in your home directory.

So with the setup above, source #1 is usually empty, source #2 (your pointer →
`~/.asana_jira_env_client_a`) is what's used, and source #3 is the safety net.

If the loader can't find a token and workspace in any of these, it stops with a
clear error telling you to set environment variables, create the env file, or add
the pointer — so a misconfiguration fails loudly and early, not silently.

> **For engineers.** The order lives in `_resolve_env_path` /
> `load_credentials` in `src/lib/config.py`: `ASANA_JIRA_ENV_FILE` override →
> per-project `.asana_jira_env_path` (resolved relative to the script's reference
> path and `expanduser()`-ed) → `~/.asana_jira_env`. Individual values are then
> read with `_read_value`, which still lets a *process* env var shadow a file
> value field-by-field (handy for one-off overrides like
> `ASANA_WORKSPACE_GID=… python3 -m scripts.…`). Empty strings count as "unset."
> The loader coerces `SKIP_SSL_VERIFICATION` via a tolerant boolean parser
> (`true/1/yes/y` vs `false/0/no/n`).

### Why this design: the multi-client rationale

Why a separate home-dir file *plus* a pointer, instead of just one file? Because
some teams migrate **several clients at once**, and each client's data must never
bleed into another's. The design lets you keep:

- one **env file per client** in your home directory
  (`~/.asana_jira_env_client_a`, `~/.asana_jira_env_client_b`, …), each with that
  client's tokens and workspace; and
- one **pointer file** inside each project copy aimed at the right client.

So you can have several clones of the project — one per client — each safely
locked onto a different client's credentials and data, with zero risk of running
client A's migration with client B's token. Switching a copy to a different
client is as simple as changing the one line in its `.asana_jira_env_path` (or
using the Credentials menu, which does it for you). For a single-client user this
is invisible overhead; for an agency it's the whole point.

### The easier alternative: the interactive setup flow

You do **not** have to create these files by hand. The project's interactive menu
(Step 7) has a **first-launch setup flow**. If you launch it and it can't find
credentials from the environment or a `.asana_jira_env_path` pointer, it starts a
guided prompt that asks you for:

- the Asana token,
- the workspace GID,
- the SSL verification flag, and
- the optional Atlassian username and token,

and then **writes both files for you** — the home-directory env file (with
`chmod 600` applied) and the project pointer. If you're not comfortable with the
`cat`/`chmod`/`echo` commands above, just skip straight to Step 7 and let the
menu walk you through it. The end result is identical to doing 5a–5c by hand.

---

## Step 6 — Confirm health by running the tests

**What it is.** Before doing anything real, you prove the code works on *your*
machine by running its **test suite** — the project's own automatic checks. This
is the single most important habit in the entire playbook (it's Pillar 3 in
Part 5), and it costs one command.

**What to do.** Make sure your venv is active (`(.venv)` in the prompt), then:

```
$ python3 -m unittest discover tests
```

**What this does.** `unittest` is Python's built-in testing framework;
`discover tests` tells it to find and run every test in the `tests/` folder. The
tests check tricky behaviors (rate-limit retries, pagination, data
replace-logic, format conversions) using fake data and mocked network calls — so
running them is safe and touches nothing live.

**What success looks like.** A stream of dots and timing, ending in `OK`:

```
.................................................
----------------------------------------------------------------------
Ran 50 tests in 0.42s

OK
```

`OK` means the code is healthy on your machine. If you ever see `FAILED` instead,
something is wrong and you should not proceed to a real run until it's resolved.

**Coverage report (optional, for the curious or for releases).** To see how much
of the code the tests exercise:

```
$ python3 -m trace --count --file trace.out -m unittest discover tests
```

This writes a `trace.out` file you can inspect; the project targets **90%+**
statement coverage.

**Troubleshooting.**

- *`No module named scripts` / `No module named src` / import errors.* You're
  almost certainly not running from the project root, or `src/` isn't importable.
  Run from inside the `asana_jira_migration` folder and use the module form shown.
  See the *For engineers* note in Step 8 about `PYTHONPATH`.
- *One or two tests `FAILED` right after a fresh clone.* That's unusual on a clean
  checkout. Re-run with more detail: `python3 -m unittest discover tests -v`. If
  it persists, the cleanest move is to ask Claude Code, from inside the project,
  to explain the failing test's output — that's exactly the kind of thing it's
  good at.
- *It's slow / I want it to stop at the first failure.* Add `--failfast`:
  `python3 -m unittest discover tests --failfast`.

> **Why this is the keystone habit.** Because you didn't write the AI's code line
> by line, the tests are how you *know* it works — and keeps working after the
> next change. Run this after every change you or Claude make. `OK` is your green
> light to continue; anything else means stop and look. Part 5 (Pillar 3) and
> Part 6 (the daily loop) build entirely on this reflex.

---

## Step 7 — Launch the guided menu

**What it is.** The friendliest way to use the toolkit is its **interactive
menu** — a numbered, walk-you-through interface so you don't have to memorize
commands or options. This is Pillar 5 in the series: accessibility as a safety
feature.

**What to do.**

```
$ python3 -m scripts.interactive_cli --log-level INFO
```

**What this does.** It launches the menu wrapper. `--log-level INFO` controls how
chatty it is; `INFO` is the comfortable default (you'll also see `DEBUG`,
`WARNING`, and `ERROR` available — `DEBUG` is useful when tracing API pagination
or rate-limit retries).

**What success looks like.** A menu with numbered **categories**:

1. **Initialization** — the first data pull: teams + memberships, then users,
   adding the service user to every team, projects, and custom fields. (The
   projects step asks whether you want active, archived, or all projects.)
2. **Tasks** — tasks, task extras, personal tasks, and incremental updates.
3. **Atlassian** — Jira key export/import, clearing mappings, updating the
   Atlassian user map, and syncing attachments.
4. **Reports** — project details, team summary, users, team memberships, and
   custom field usage CSVs.
5. **Credentials** — create/update the referenced env file, or switch this
   project copy to a different one.

You pick a number, and it walks you through the rest, including searchable
pickers (like "choose a team by typing part of its name") and the confirmation
prompts that protect the live system.

**First-launch setup.** If you skipped Step 5, this is where the menu notices it
can't find credentials and runs the setup flow described above, writing your env
file and pointer for you.

**Troubleshooting.**

- *`No module named scripts`.* Same as Step 6 — run from the project root using
  the `python3 -m scripts.…` form.
- *It immediately errors about missing credentials and doesn't offer setup.* It
  *should* offer setup when nothing is found; if it instead reports a *partial*
  credential (e.g., a token but no workspace GID), fix that field in your env
  file. Re-read Step 5's resolution order.
- *How do I leave the menu?* Choose the quit/exit option it offers, or press
  `Ctrl+C`.

---

## Step 8 — Running an individual command

**What it is.** The menu wraps a collection of standalone scripts. Once you're
comfortable, you can run any one of them directly — the power-user path. Every
script is documented, accepts standard options, and can show its own help.

**What to do — see what a command does.** Replace `<name>` with the script's
name:

```
$ python3 -m scripts.<name> --help
```

For example, `python3 -m scripts.get_teams --help` shows the options for the
teams sync. The `--help` output lists every flag and what it expects — read it
before running anything that writes data.

**Why the `python3 -m scripts.<name>` form?** Running scripts this way (as a
*module*, with `-m`) keeps the project's `src/` folder importable, so the shared
helpers load correctly. It's the supported way to run any command in this
project. The catalog of scripts and exactly what each one does lives in the
project's `docs/` reference files (`docs/sync-commands.md`, `docs/reports.md`,
`docs/jira-integration.md`) and in `CLAUDE.md`.

**Two options every script accepts.**

- `--log-level` — `DEBUG`, `INFO`, `WARNING`, or `ERROR`. Use `DEBUG` to trace
  what's happening during pagination or rate-limit retries; `INFO` is the default
  for unattended runs.
- `--workers` — an override for how many parallel workers to use, available on the
  scripts that do concurrent work.

**What success looks like.** `--help` prints a usage block and exits without doing
anything. That's the safest possible first interaction with any unfamiliar
command — look before you leap.

**Troubleshooting.**

- *`No module named scripts`.* You're not at the project root, or `src/` isn't on
  the path. Use the `-m` form from inside the project folder.

> **For engineers.** `python3 -m scripts.<name>` works because running a package
> module puts the project root on `sys.path`, which makes `import src.…` resolve.
> If you prefer the file form `python3 scripts/<name>.py`, you must first export
> the project root onto `PYTHONPATH` (`export PYTHONPATH="$PWD"`), otherwise the
> `src/` imports fail. Thin wrappers in `scripts/` keep the historical
> `python3 -m scripts.<name>` invocations working even for the Atlassian scripts
> that physically live under `scripts/atlassian/`. Every entrypoint also accepts
> `--log-level`; concurrent ones expose `--workers`.

---

## Step 9 — Cleanup after a job

**What it is.** This toolkit intentionally writes real, potentially **sensitive**
data to your local disk while it works. When a migration job is finished, you
should clean that data up rather than leaving it sitting around.

**What gets written locally (treat as sensitive):**

- `data/asana.sqlite` — the normalized local snapshot, which **includes user
  emails** and lots of other entity data.
- `exports/` — the generated CSVs and reports (Jira import files, user exports,
  audit reports, link-modification reports, attachment sync reports).
- `exports/tmp_attachments/` — temporary attachment downloads created during Jira
  attachment syncs. These are auto-deleted on a normal, successful run **unless**
  `--keep-downloads` was used, but an interrupted or failed run can leave residue
  behind, so check this folder during cleanup.

**What to do — the suggested post-job cleanup.** These commands **delete**
things, so read them before running:

```
$ rm -rf exports
$ rm -f data/asana.sqlite
$ rm -f .asana_jira_env_path
```

**What each does.**

- `rm -rf exports` removes the whole `exports/` folder and everything in it (CSVs,
  reports, and any leftover temp attachments).
- `rm -f data/asana.sqlite` removes the local data snapshot with its emails.
- `rm -f .asana_jira_env_path` removes this copy's credentials pointer, so the
  project is no longer aimed at that client.

**Then handle the secrets themselves.** The cleanup above removes the *pointer*,
not the home-directory env file. If you no longer need access to that client,
also **remove or rotate** the client-specific env file in your home directory:

```
$ rm -f ~/.asana_jira_env_client_a
```

And if a token was ever committed, shared, or otherwise exposed, **rotate it**
(regenerate it in Asana/Atlassian so the old one stops working) regardless of
cleanup.

**What success looks like.** No output from the `rm` commands (silence is
success). `ls` no longer shows `exports/` or `data/asana.sqlite`, and
`cat .asana_jira_env_path` reports the file is gone.

**Troubleshooting / cautions.**

- *`rm -rf` is irreversible.* There's no Trash for terminal deletes. Double-check
  you're in the right project folder (`pwd`) before running them, and that you've
  exported/archived anything you actually need to keep per the client's retention
  policy first.
- *Leftover temp files.* If a run was interrupted, peek at
  `exports/tmp_attachments/` before the `rm -rf exports` to confirm nothing you
  need is in there (you're deleting it either way).

---

## The complete copy-paste quick start

Here is the entire happy path in one block, for when you've done it once and just
want to repeat it. Replace the two placeholders (`<repo-url>` and your real
token values).

```bash
# --- one-time setup ---
python3 --version                                   # confirm Python 3.11+
npm install -g @anthropic-ai/claude-code            # install Claude Code (needs Node.js)
claude                                              # sign in once, then exit
git clone <repo-url>                                # get the project
cd asana_jira_migration                             # step into it
python3 -m venv .venv && source .venv/bin/activate  # private sandbox; prompt shows (.venv)

# --- credentials (or skip these three and let the interactive menu's setup flow do it) ---
cat > ~/.asana_jira_env_client_a <<'EOF'
export ASANA_TOKEN="your_asana_token"
export ASANA_WORKSPACE_GID="your_workspace_gid"
export SKIP_SSL_VERIFICATION="false"
export ATLASSIAN_USERNAME="you@example.com"
export ATLASSIAN_TOKEN="your_jira_api_token"
EOF
chmod 600 ~/.asana_jira_env_client_a                # readable only by you
echo "~/.asana_jira_env_client_a" > .asana_jira_env_path  # aim this copy at that file

# --- verify health ---
python3 -m unittest discover tests                  # expect it to end with: OK

# --- everyday use ---
python3 -m scripts.interactive_cli --log-level INFO # the guided menu
python3 -m scripts.<name> --help                    # options for any single command
claude                                              # start the AI assistant in this folder

# --- when you're done with a client (these DELETE data) ---
rm -rf exports
rm -f data/asana.sqlite
rm -f .asana_jira_env_path
# also remove/rotate the home-dir secrets file if access is no longer needed:
# rm -f ~/.asana_jira_env_client_a
```

Remember: every new terminal session needs `cd asana_jira_migration` and
`source .venv/bin/activate` again — the create/install/credential steps are
one-time, the activate step is per-session.

---

## Common errors and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `python3: command not found` | Python not installed, or terminal not reopened after install | Install from python.org or `brew install python`; **close and reopen the terminal**; re-run `python3 --version` |
| `python --version` shows an old 2.x | You used bare `python` | Always use `python3` in this project — never `python` |
| `npm: command not found` | Node.js not installed | Install Node from nodejs.org (npm comes with it); reopen terminal |
| `npm install -g …` fails with `EACCES`/permissions | Global npm prefix needs elevated rights | Use `nvm`, or change npm's global prefix per npm docs; avoid `sudo npm install -g` |
| `claude: command not found` after install | New global binary not yet on PATH | Close and reopen the terminal; check `npm bin -g` is on your `PATH` |
| `git: command not found` | Git not installed | Run `git` once to trigger the Command Line Tools install, or `brew install git` |
| `Permission denied (publickey)` on clone | Private repo, no access set up | Use the `https://` URL and sign in, or configure an SSH key with your git host |
| `source: no such file or directory: .venv/bin/activate` | `.venv` wasn't created, or wrong folder | `cd` into `asana_jira_migration`, run `python3 -m venv .venv`, then activate |
| Prompt has no `(.venv)` | Sandbox not active in this terminal | Re-run `source .venv/bin/activate` |
| `No module named scripts` / `No module named src` | Not running from project root, or `src/` not importable | Run from the project folder using `python3 -m scripts.<name>`; engineers: `export PYTHONPATH="$PWD"` |
| `No credentials found …` error | No token/workspace in env, pointer, or default file | Complete Step 5, or launch the menu and use its setup flow |
| `Missing 'token'/'workspace_gid' …` | A required field is blank in the env file | Edit `~/.asana_jira_env_client_a` and fill the missing `export` line |
| `skip_ssl_verification must be a boolean value` | `SKIP_SSL_VERIFICATION` set to something odd | Set it to `"false"` (or `"true"` only as a temporary workaround) |
| Tests end in `FAILED` on a fresh clone | Unexpected on a clean checkout | Re-run with `-v`; ask Claude Code to explain the failure; don't do a live run until green |
| The credentials file is world-readable (`-rw-r--r--`) | Forgot `chmod 600` | Run `chmod 600 ~/.asana_jira_env_client_a` |

---

## Key takeaways

- **Setup is nine steps, most of them one command.** Open the terminal; check
  Python 3.11+; install Node + Claude Code and sign in; clone and `cd`; create and
  activate `.venv`; provide credentials safely; run the tests; launch the menu;
  clean up after. Steps 0–4 are generic; Step 5 is the one that protects you.
- **Secrets never go in the project folder.** Real tokens live in a `chmod 600`
  file in your home directory; only a non-secret *pointer*
  (`.asana_jira_env_path`) goes in the repo. The loader resolves credentials in a
  fixed order — process env (and the `ASANA_JIRA_ENV_FILE` override) → the
  pointer's file → default `~/.asana_jira_env` — and fails loudly if it finds
  nothing.
- **The home-file-plus-pointer design exists for multi-client work.** One env file
  per client, one pointer per project copy, zero risk of mixing clients. For a
  single client it's harmless overhead; for an agency it's the safety mechanism.
- **You can skip the hand-typed credential commands entirely.** The interactive
  menu's first-launch setup flow asks for the values and writes both files (with
  the right permissions) for you.
- **`python3 -m unittest discover tests` ending in `OK` is your green light.**
  Run it after every change. It's the habit the rest of the series is built on.
- **Run things the supported way.** Use the guided menu for everyday work, and
  `python3 -m scripts.<name> --help` for any single command — the `-m` form keeps
  `src/` importable. Every command takes `--log-level`.
- **Clean up sensitive local data when a job is done.** `data/asana.sqlite`
  (includes emails), `exports/`, and `exports/tmp_attachments/` are all sensitive;
  remove them and rotate or delete the client's env file when access is no longer
  needed.

---

## Where to next

- **Previous:** [03 — Before you write any code](03-before-you-write-any-code.md)
- **Up:** [The Claude Code playbook (parent)](../claude-code-playbook.md)
- **Next:** [05 — The five pillars of safe AI-assisted coding](05-five-pillars.md)

Other parts in the series: [01 — What is vibe coding](01-what-is-vibe-coding.md)
· [02 — Speed inside guardrails](02-speed-inside-guardrails.md)
· [06 — The daily loop](06-the-daily-loop.md)
· [07 — Deciding what not to build](07-deciding-what-not-to-build.md)
· [08 — A recipe for your own project](08-recipe-for-your-own-project.md)
· [09 — Pitfalls and honest limits](09-pitfalls-and-limits.md)
· [Series index](README.md)
