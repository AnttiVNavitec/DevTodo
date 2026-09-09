# DevTodo — Claude context

## Run
`serve.bat` → `http://localhost:8080` (Python server in `server.py`)
Jira/GitLab calls are proxied through the server: `/proxy/jira/*`, `/proxy/gitlab/*`
Git worktree state comes from the server too: `/worktrees`, `/worktrees/scan`, `/activity`
Tool launching: `POST /worktree/open` `{tool, path}`
Branch ops: `GET /worktree/branches?path=`, `POST /worktree/git` `{operation, params}`

## File layout
- `index.html` — all HTML + all JS in one IIFE (no build step, no modules)
- `styles.css` — all CSS, separate file
- `server.py` — local proxy server, routing only for anything non-trivial
- `fetch_mr_comments.py` — helper called by server.py
- `jira_downloader.py` — helper called by server.py
- `worktrees.py` — git worktree discovery (shells out to git, no repo-specific knowledge)
- `activity.py` — background poller that logs worktree activity signals to `data/`
- `spawn.py` — launches external tools (Explorer, Git Bash, VS Code, Claude…) in a worktree
- `gitops.py` — mutating git operations with server-enforced guards
- `transcripts.py` — Claude-transcript probe; a leaf module so worktrees.py and activity.py
  can both use it without importing each other
- `PLAN-worktrees.md` — phased plan for the worktree / auto-time-tracking work
- `data/` — gitignored. Activity log + remembered repo paths. **Real work data: never commit.**

**Do not split the JS into modules.** The JS is a single IIFE; all state is shared via closure. Splitting would require globals or ES modules and isn't worth it at the current size. Python *is* split into modules — `server.py` stays a router.

## JS sections in index.html
Grep for `// ── <name>` to jump directly. Sections in order:

| Section | What's there |
|---|---|
| `Storage keys` | All `const KEY_*` and `const POMO_*` / `BREAK_*` constants |
| `Default settings` | `DEFAULT` object shape |
| `State` | All `let` state vars — read this to understand what exists |
| `Helpers` | `loadSettings`, `saveTodos`, `deepClone`, `esc`, `$id`, week number |
| `Time tracking storage` | load/save for entries, active entry, suggestions, pruning, pomo/ctx helpers |
| `Pomodoro timer` | `pomoStart`, `pomoInterrupt`, `pomoSkipBreak`, `buildDayStats`, `renderPomo` |
| `Tracker bar` | `clockIn`, `clockOut`, `checkAutoClockout`, `renderTracker` |
| `Other work modal` | `openOtherWork`, `submitOtherWork` |
| `Time report modal` | `openTimeReport`, `renderTimeReport`, `renderLogView`, `renderSummaryView` |
| `Jira` | `cacheJiraSummaries`, `fetchJiraSummary`, `jiraFetch`, `fetchMyJiraIssues`, `fetchSupportTickets`, `fetchEpicChildren`, `createEpicIssue`, `buildJiraItem`, `loadJira`, `loadEpicPanel` |
| `Jira sorting` | `priorityOrder`, `jiraStatusTier`, `issueInActiveSprint`, sort comparators |
| `GitLab` | `fetchGitLabMRs`, `fetchMrReview`, `buildGitLabItem`, `downloadMrComments`, `loadGitLab` |
| `MR ranking` | `mrRank` |
| `Local Todos` | `renderTodos`, `addTodo` |
| `Worktrees` | `ticketKeyOf`, `fmtAgo`, `worktreeLabel`/`worktreeLabelSync`, `isWorktreeActive`, `buildWorktreeItem`, `renderWorktrees`, `loadWorktrees`, `scanForRepos`, `parseRoots` |
| `Nudges & gaps` | `trackWorktreeChanges`, `pendingNudges`, `renderNudges`, `loadActivity`, `findGaps`, `subtractCovered`, `mergedCoverage`, `acceptGap`, `renderReportGaps` |
| `Branch operations` | `buildBranchCandidates`, `branchPlan`, `worktreeBlockReason`, `openBranchDialog`, `openCreateBranchDialog`, `openAddWorktreeDialog`, `runGitOp`, `performOp` |
| `Contextual actions` | `ACTIONS` registry, `openWorktreeTool`, `trackedContext`, `worktreeContext`, `findTrackedWorktree`, `renderContextActions` |
| `Settings Modal` | `openSettings`, `closeSettings`, `collectSettings` |
| `Event wiring` | All `addEventListener` calls |
| `Init` | Startup sequence |

## CSS sections in styles.css
Same pattern, grep for `/* ── <name>`. Key sections: `Pomodoro bar`, `Tracker bar`, `Time report`, `Summary view`, `Day stats in time report`, `Worktrees`.

## Key shared state vars
```
settings          loaded from localStorage, shape: { jira, gitlab, pomo, worktrees }
activeEntry       null | { id, label, type, startedAt, endedAt:null } — the running timer
timeEntries       completed entries array
timeSuggestions   autocomplete history for "Other work"
pomoState         null | { phase:'work'|'break', startedAt, durationMs }
pomoCycles        [{ date:'YYYY-MM-DD', type:'completed'|'interrupted' }]
ctxSwitches       [{ date:'YYYY-MM-DD' }] — one record per context switch
reportDate        YYYY-MM-DD string driving the time report modal
reportTab         'log' | 'summary'
summarySelection  Set<label> of checked rows in summary view
jiraSummaries     Map<issueKey, summary> — warmed by loadJira/loadEpicPanel
worktreeChanges   Map<path, {fp, at}> — when each worktree last changed (drives nudges)
activityRecords   last /activity response, read by the gap finder
worktreeData      last /worktrees response; renderWorktrees() reads it without refetching
```

## Storage keys (all `devtodo_*`)
`settings`, `todos`, `show_unassigned`, `time_entries`, `time_active`, `time_suggestions`, `epic`, `pomo_state`, `pomo_cycles`, `ctx_switches`, `nudge_dismissed`, `gap_dismissed`

## Activity logging (`activity.py`)
Runs on a daemon thread started by `server.py`, independent of the browser, so it keeps
collecting with no tab open. Phase 5 of `PLAN-worktrees.md` consumes it; the history
cannot be backfilled, which is why it collects before anything reads it.

- Polls every 60s. Appends to `data/activity.jsonl` **only when something changed**, plus a
  heartbeat every 15 min so "nothing happened" is distinguishable from "poller was down".
- Records carry a `why` list: `start`, `branch`, `commit`, `files`, `claude`, `heartbeat`,
  `error` (a repo/worktree became unreadable), `gone` (stopped being reported).
  `error`/`gone` exist so absence of records never has to mean "we couldn't look".
- `files` fingerprints the staged/unstaged/untracked/conflicted **breakdown**, not just the
  total — otherwise a bare `git add` would go unnoticed.
- Claude activity comes from the newest `*.jsonl` mtime under
  `~/.claude/projects/<mangled-path>/`, where mangling replaces `:` `/` `\` with `-` and
  lowercases the drive letter. Each transcript dir is attributed to the **longest** matching
  worktree, or `<repo>-worktree` sessions would be credited to `<repo>`. Undocumented
  format — every failure path degrades to "no signal".
- The repo list lives in browser localStorage, so the server remembers whatever roots it was
  last asked about in `data/roots.json` and polls those. An empty list never overwrites it.
- Retention is 14 days, pruned once a day, matching the dashboard's time entries.

## Contextual actions
One registry (`ACTIONS`) drives every action button, rendered onto two surfaces:
the **tracker bar** (acting on whatever is being tracked) and each **worktree row**
(acting on that worktree). Add an action once and pick its surfaces; don't hand-write
buttons into either place.

```
{ id, icon, label, title, surfaces: ['tracker'|'worktree'], applies(ctx), run(ctx) }
```

- `applies(ctx)` → `true` show enabled · `'reason'` show disabled with the reason as
  tooltip · falsy omit entirely. The three-way return is what lets an action explain why
  it is unavailable instead of silently vanishing.
- Context is `{ label, type, jiraKey, worktree }`; any field may be null, and each action
  declares what it needs. `trackedContext()` resolves the worktree via
  `isWorktreeActive`, so it is found by ticket key, not by path.
- `run(ctx)` may do anything — **these are not all program launchers.** Current actions
  download a file, open a URL, and start processes. Branch checkout / branch creation are
  the next ones and will open a dialog from `run`.
- The tracker bar renders `icon + label`; worktree rows render `icon` only, since row
  actions are hover-revealed and space is tight.
- `renderTracker()` runs every second and rebuilds these buttons each time, so actions
  must stay cheap and stateless.

Server side, `spawn.py` holds a **fixed table** of launchable tools. The client sends a
tool id and a path, never a command line, and `server.py` checks the path is one git
itself reports as a worktree (membership, not prefix — a prefix test would let any
subdirectory through). `POST /worktree/open` additionally requires a present, matching
`Origin` header, which is stricter than the read-only routes: it starts processes.

## Nudges & gaps — the "I forgot to clock in" problem
Two halves, deliberately using different data sources:

**Live nudge** (`renderNudges`) is client-side. It diffs consecutive `/worktrees` polls, so
it reacts in ~10s rather than waiting on the server's 60s activity poll. It only runs while
the tab is visible — which is fine, since a nudge you can't see is useless, and the
fingerprint diff fires the moment you come back. A worktree with no previous sample gets a
silent baseline, or every page load would nudge for everything.
Shown when: changed within `NUDGE_WINDOW_MS` (10 min), `isWorktreeActive()` is false, and
not dismissed. A dismissal lasts `NUDGE_DISMISS_MS` (60 min) **or until the branch changes**,
since a new branch is a new task.

**Gap finder** (`findGaps`) is retroactive and reads the server's activity log, which keeps
collecting with no browser open. That's the half that recovers a whole afternoon. Rules
that matter, all learned from running it against real data:

- **`start` and `heartbeat` are not work.** `start` fires on every server restart and
  `heartbeat` on a timer; counting either invents activity. Only
  `WORK_REASONS = {branch, commit, files, claude}` count.
- **Sessions consolidate across worktrees, not per branch.** Per-branch grouping turned one
  morning of branch-hopping into 15 five-minute slivers that *overlapped each other* — so
  they could not all be accepted without double-counting. One session per stretch of
  activity anywhere, labelled by whatever was touched most in it, with the other branches
  listed via `others` so a mixed stretch reads as mixed.
- **Sessions widen backwards**, by `POLL_LEAD_MS`. Work happens *before* the poll that
  notices it; padding forwards would claim time after the user had stopped.
- **Any time entry counts as coverage, whatever its label.** If you were clocked in on
  something, the hour is accounted for. Mislabelled time is a different problem and
  surfacing it here would bury the real gaps.
- Gaps are rendered **before** the report's empty-day bail-out: a day with no entries at
  all is exactly the day where untracked work matters most.
- Suggestions are guaranteed non-overlapping. Keep it that way — accepting two overlapping
  rows would double-count.

Both `days` bucketing and coverage use `ts.slice(0, 10)`, i.e. **UTC** days, matching
`entriesForDate()` and `todayStr()`. That's a pre-existing app-wide quirk; the gap finder
matches it on purpose so gaps and entries line up.

## MR review state — "has the reviewer said something new?"
`fetchMrReview()` reads `/discussions` per MR and returns
`{ unresolved, newCount, newAt, authors, isNew }`, stored on `mr._review`.

- **The signal is turn-based, not a read-marker.** `isNew` means someone else's newest note
  is newer than *my* newest note. Reading a comment does not answer it, so a badge that
  cleared on visit would clear the thing you still owe a reply to. It clears when you reply
  or the thread is resolved — nothing is persisted client-side.
- **No note of my own → not new, unless the MR is mine** (author or assignee). Otherwise
  every MR I merely review would light up, which the "Needs My Review" tier already says.
- Skipped when `user_notes_count` is 0 — that's most MRs, one request each. Any failure
  returns null and every consumer treats null as "no signal", so the panel never breaks
  because discussions were unreadable.
- System notes are filtered out; a thread counts as `unresolved` only if it has resolvable
  notes and not all are resolved (same rule as `fetch_mr_comments.py`).
- Surfaces as tier 2 **New Comments** (below Ready to Merge, which is a ten-second action)
  plus a `💬 N new` badge and a `🧵 N` unresolved-thread count. Within that tier, newest
  unanswered comment sorts first.

## Branch operations
`gitops.py` holds a fixed operation table (`checkout`, `create`, `move`, `detach`,
`worktree-add`, `prune`, `fetch`, `sync-base`). Same shape as `spawn.py`: the client names an operation,
never a command line. **Guards are enforced server-side** — the UI's disabled buttons are
only an affordance. `worktreeBlockReason()` mirrors them client-side so a button never
lies about being available.

Things that must not regress:

- **`_holder_of` is scoped to the repo.** The same ticket routinely has a same-named branch
  in several repos; an unscoped lookup reported a *foreign* repo's worktree as the holder,
  and a move would then have detached something unrelated.
- **`move` rolls back.** If the second step fails, the holder is put back, so a failure
  never leaves a branch checked out nowhere.
- **`create` uses `--no-track`.** Branching from `origin/<base>` with git's default sets
  upstream to the base, pointing a later `git push` at the shared branch.
- **The base branch is detected from `origin/HEAD`, never hardcoded** — one repo here uses
  `origin/Develop` and another `origin/develop`.
- `check-ref-format --branch` validates names; `worktree-add` takes a plain folder name
  joined to the main checkout's parent, so a path cannot escape.
- **`sync-base` never force-updates.** It brings the default branch up to date *without
  checking it out* — `git fetch origin X:X` when nothing holds it, `merge --ff-only` in the
  holder when something does, because git refuses a refspec fetch into a checked-out
  branch. No `+refspec`, no `--force`: a diverged local branch is a real situation and
  both paths report it rather than flattening it. `repo.base` is in the `/worktrees`
  payload so the button can be labelled without an extra round trip.

### The branch picker
Deliberately does **not** offer every branch — hundreds exist and the command line is the
escape hatch. Candidates are a union of five sources, each labelled with why it is there:
my Jira tickets (`jiraSummaries`), my merge requests (`mrBranches`, from
`mr.source_branch`), whatever is checked out now, long-lived branches (settings glob,
default `release/*`), and the 10 most recent. That is ~13–17 rows out of 110. The filter
box pre-fills with the tracked ticket key, which usually narrows to one.

`ticketKeyLoose()` matches branches case-insensitively — unlike `ticketKeyOf()`, which
gates labels. A false positive here is harmless (it just won't be in `jiraSummaries`),
and real branches are sometimes lower-cased.

## Conventions
- XSS: always wrap user/external strings with `esc()` before innerHTML
- No comments in code unless the why is non-obvious
- No TypeScript, no build tooling
- `fmtDuration(ms)` → `"1h 05m"` / `"45m"` — use for all time display
- `todayStr()` → `"YYYY-MM-DD"` — use for date comparisons
- Context switch = `clockIn` called with a different label than `activeEntry.label`
- **Track labels are `"KEY: summary"`.** The Jira panel, the epic panel and the worktree
  panel all build labels this way on purpose, so time clocked on the same ticket from any
  of them aggregates into one row in the summary view. `jiraKeyFromLabel()` parses the key
  back out, which is what gates the tracker bar's "Save ticket" button — so that button
  works for worktree entries too. Do not invent a different label shape.
- The worktree panel's ▶ resolves its label through `jiraSummaries`, fetching a single
  issue summary on demand for branches whose ticket no panel loaded (someone else's
  ticket, or a closed one). No summary available falls back to the branch remainder.
- `clockIn`/`clockOut` call `renderWorktrees()` so the ▶/Tracking marker updates at once
  instead of waiting for the 10s poll.
- Entries older than 14 days are pruned on startup (`pruneOldEntries`)
- The 1-second interval calls both `renderTracker()` and `renderPomo()`
- `worktrees._cache` is a single immutable `(key, ts, data)` tuple swapped in one
  assignment. The server is threaded and the poller and dashboard both read it, so it must
  never be updated field by field.
- The worktree panel polls every 10s while the tab is visible. Its render order is fixed
  (repo name → main checkout first → worktree name) so a refresh never moves an item
  under the cursor.
- **Nothing repo-, product- or employer-specific in the code.** This repo is public.
  Repo paths, project keys and base branches are settings, never constants.
- No raw control characters in source. Use `JSON.stringify([...])` for composite map keys
  rather than a separator byte — one crept in as a literal `0x1f` and was invisible.
- `index.html` is **CRLF**. Scripted edits must preserve that.
