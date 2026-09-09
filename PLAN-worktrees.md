# Plan: worktree panel, activity-driven time tracking, agent console

Goal, in priority order:

1. Stop losing hours because I forgot to clock in — both live nudges and retroactive gap filling.
2. See at a glance what my parallel worktrees are doing.
3. Reduce friction when working across worktrees (open a shell there, move a branch around).
4. Let Claude instances run more autonomously without me babysitting terminals.

## Environment facts (verified 2026-09-09)

Nothing repo-specific, product-specific or employer-specific belongs in the code — this
repo is public. Repo paths, the default base branch and any project keys are **settings**,
never constants.

- Multiple repos, each with several worktrees, typically laid out as siblings:
  `<parent>/<repo>`, `<parent>/<repo>-worktree`, `<parent>/<repo>-worktree-2`.
  So: support a list of repo roots, not a single one.
- `git worktree list --porcelain` returns forward-slash paths with a capital drive letter
  (`C:/path/to/repo`). The first entry is always the main checkout.
- Branch names carry ticket keys, with either separator after the key
  (`ABC-1234-some-feature`, `ABC-1234_some_feature`). Regex: `[A-Z][A-Z0-9]+-\d+`.
- Jira is **Cloud** (`/rest/api/3`). Matters for the MCP choice in phase 7.
- Claude Code stores per-directory session transcripts at
  `~/.claude/projects/<mangled-path>/*.jsonl`, where the mangling replaces `:` `/` `\`
  with `-` and lowercases the drive letter. Match case-insensitively — this is an
  unofficial format, degrade gracefully.
- `server.py` is plain `HTTPServer`: **single-threaded, one request at a time.**
  Anything blocking (phase 7 approvals) deadlocks the dashboard until this becomes
  `ThreadingHTTPServer`.

## Architecture decisions

- **Same app, new panel.** The whole value is the wiring to the existing time tracker and
  Jira ticket cache. A separate tool would duplicate Jira auth, the ticket cache and the
  entry store, and leave me looking at two dashboards while still not clocking in.
- **Python gets modules, JS does not.** `server.py` stays the entry point / router;
  worktree logic goes in `worktrees.py`, process spawning in `spawn.py`, approvals in
  `approvals.py`. The JS stays one IIFE per CLAUDE.md, with one new
  `// ── Worktrees` section.
- **SSE, not websockets**, when push is needed. `EventSource` in the browser, ~15 lines of
  `text/event-stream` server-side. Hand-rolling websocket frames on
  `BaseHTTPRequestHandler` is a bad trade for one-directional push.
- **Signals get logged from phase 2 onward, before any UI consumes them.** This data
  cannot be backfilled.

## Phases

Each phase is independently useful and independently shippable.

### Phase 1 — read-only worktree panel — **done**

- Settings: list of repo roots (`settings.repos: string[]`). Auto-seed by scanning a
  configured parent dir for `.git`, but keep the list editable.
- `GET /worktrees` → for each repo root:
  - `git worktree list --porcelain` — path, HEAD, branch, detached/locked/prunable
  - `git -C <wt> status --porcelain=v2 --branch` — dirty file count, ahead/behind
  - `git -C <wt> log -1 --format=%H%x1f%s%x1f%ct` — last commit subject + age
- Panel shows one card per worktree: repo, branch, ticket key (parsed), dirty badge,
  ahead/behind, last commit age, "main checkout" marker.
- Poll every 5–10 s, and only when the tab is visible. Per-second `git status` on these
  repos is not free.

### Phase 2 — signal logger (no UI) — **done**

Implemented in `activity.py`; see CLAUDE.md for the record shape and the reasoning.
Beyond the original sketch, it also records `error` and `gone` events, so a reader can
never mistake "we could not observe this repo" for "nothing happened here" — that
distinction matters for phase 5 and cannot be reconstructed later.

### Phase 3 — play button per worktree — **done**

Implemented. Labels deliberately match the Jira panel's `"KEY: summary"` shape so the two
aggregate into one summary row, and the tracked worktree is marked in the panel — matching
on ticket key, so clocking in from the Jira panel lights up the worktree its branch lives
in. Context-switch counting came for free.

### Phase 4 — spawn tools in a worktree

- `POST /worktree/open` with `{path, tool}`, `tool` a **fixed enum**:
  - `gitbash` → `C:\Program Files\Git\git-bash.exe --cd=<path>`
  - `terminal` → `wt.exe -d <path>`
  - `vscode` → `code <path>`
  - `claude` → `wt.exe -d <path> cmd /c claude`
  - `explorer` → `explorer <path>`
- Security: POST only, never accept a command string from the client, and validate `path`
  against the discovered worktree list (membership, not prefix). A CSRF-able localhost
  process-spawn endpoint is a different risk class from the read-only Jira proxy.

### Phase 5 — the forgetting problem

- **Live nudge:** non-modal chip in the tracker bar when a worktree's HEAD changes, or
  when files change in a worktree while `activeEntry` is null or its label matches no
  active worktree. `<worktree> → ABC-1234 … [Clock in] [Dismiss]`. Non-modal on purpose;
  a dialog mid-thought gets dismissed reflexively.
- **Retroactive fill:** the time report grows a "gaps" affordance — "17:40–18:25 activity
  in <worktree> on ABC-1234, nothing clocked in — add it?" Derived entirely
  from the phase-2 signal log.
- The timer stays **single-valued**. When three agents run at once, "which task am I on"
  has no single machine answer; worktree activity is evidence for suggestions, not truth.

### Phase 6 — branch operations

- Create branch on an idle+clean worktree: `git -C <wt> switch -c <name> <configured base branch>`.
- Check out an existing branch on an idle+clean worktree.
- **"Move branch here"** — the case that's genuinely fiddly by hand, because git refuses to
  check out a branch that is checked out elsewhere:
  `git -C <holder> switch --detach` then `git -C <target> switch <branch>`.
- `git worktree add` (new worktree from a name pool) and `git worktree prune`.
- Guards, non-negotiable:
  - Refuse if the worktree is dirty.
  - Refuse if Claude was active there in the last ~2 minutes. Yanking a branch out from
    under a running agent produces confident nonsense.
  - Show buttons **disabled with the reason**, don't hide them.
  - Return git's stderr verbatim. Never summarize a git error.
- Out of scope: merge, rebase, push. Interactive failure modes, wrong tool.

### Phase 7 — Jira for Claude, and the approval console

- **Config only, do first, independent of everything above:** add the official Atlassian
  MCP server (remote, OAuth — works because Jira is Cloud). Allow-list the read tools in
  `.claude/settings.json`, leave writes prompting. This is ~90% of "give Claude Jira" for
  zero code. Do **not** build a custom Jira CLI.
- The remaining real gap is that each worktree's Claude blocks in a terminal I'm not
  looking at. Fix with a `PreToolUse` hook, not a bespoke API:
  1. Hook script POSTs the pending tool call to `/approvals` and blocks on the response.
  2. Dashboard renders a queue: *<worktree> wants to transition ABC-1234 → In Review
     · [Approve] [Deny]*.
  3. Hook returns the decision (`permissionDecision`, or exit 2 to block with feedback).
- Prerequisites: `ThreadingHTTPServer` + SSE.
- A `Notification`/`Stop` hook posting to the same endpoint gives an
  "**<worktree> is idle, waiting on you**" badge — probably worth more than the Jira approvals.
- Generalizes beyond Jira: the same queue can gate `git push`, writes outside a worktree,
  anything.
- Verify the exact hook JSON contract against current docs before building.

## Risks and gotchas

- Windows paths: git emits `C:/...`, the shell reports `c:\...`, transcript dirs lowercase
  the drive. Normalize on one internal form and compare case-insensitively.
- Never let the dashboard mutate git in a worktree with a live agent — hence the busy lock.
- `index.html` is ~2000 lines. This is roughly the last panel that fits comfortably in the
  single-IIFE arrangement; revisit if two more arrive.
- The transcript-dir layout is unofficial. Every read of it needs a graceful fallback.
