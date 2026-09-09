#!/usr/bin/env python3
"""
Activity signal logger.

Records what each git worktree was doing over time, so the dashboard can later (a) nudge
you to clock in when work starts somewhere and (b) reconstruct time you worked but never
logged.

This runs on a background thread inside server.py rather than in the browser, so it keeps
collecting whether or not the dashboard is open in a tab. It writes before anything reads:
the history it builds cannot be backfilled, so collection has to start first.

The log is append-only JSONL and deliberately sparse — a record is written only when a
worktree's state actually changes, plus a periodic heartbeat so that "nothing happened"
is distinguishable from "the poller was not running".

Nothing here knows anything about specific repos; roots come from the dashboard.
"""
import glob
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import worktrees

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
LOG_PATH = os.path.join(DATA_DIR, 'activity.jsonl')
ROOTS_PATH = os.path.join(DATA_DIR, 'roots.json')

POLL_SEC = 60
HEARTBEAT_SEC = 900          # force a record this often even when nothing changed
RETENTION_DAYS = 14          # matches the dashboard's own time-entry pruning
CLAUDE_PROJECTS = os.path.join(os.path.expanduser('~'), '.claude', 'projects')

_lock = threading.Lock()
_last = {}                   # worktree path key → {'fp': tuple, 'ts': float, 'claudeAt': float}
_last_errors = {}            # repo root key → last logged error string
_pruned_on = None            # date of the last prune


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec='seconds').replace('+00:00', 'Z')


def _iso_epoch(epoch):
    if not epoch:
        return None
    return _iso(datetime.fromtimestamp(epoch, timezone.utc))


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


# ── Remembered roots ─────────────────────────────────────────────────────────
# The dashboard owns the repo list (it lives in browser localStorage), but the poller
# has to keep working with no browser attached. So the server remembers whatever roots
# it was last asked about and polls those.
def remember_roots(roots):
    roots = [r.strip() for r in (roots or []) if r and r.strip()]
    if not roots:
        return
    with _lock:
        if roots == _read_roots():
            return
        _ensure_dir()
        tmp = ROOTS_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'roots': roots, 'updatedAt': _iso(_now())}, f, indent=2)
        os.replace(tmp, ROOTS_PATH)


def _read_roots():
    try:
        with open(ROOTS_PATH, encoding='utf-8') as f:
            return json.load(f).get('roots') or []
    except (OSError, ValueError):
        return []


def load_roots():
    with _lock:
        return _read_roots()


# ── Claude Code transcript probe ─────────────────────────────────────────────
# Claude Code keeps a session transcript per working directory under
# ~/.claude/projects/<mangled-path>/*.jsonl. The newest mtime in that directory is a good
# proxy for "an agent was doing something here". This layout is not a documented
# interface, so every failure path here degrades to "no signal" rather than raising.
def _mangle(path):
    """Mangle a filesystem path the way Claude Code names its project directories."""
    norm = os.path.normpath(str(path)).replace('\\', '-').replace('/', '-').replace(':', '-')
    # The drive letter is lowercased; the rest of the path keeps its case
    return (norm[:1].lower() + norm[1:]) if norm else norm


def claude_activity(worktree_paths):
    """
    Map each worktree path → epoch seconds of the newest Claude transcript write, or None.

    A session started in a subdirectory of a worktree gets a longer mangled name, so each
    project directory is attributed to the *longest* matching worktree. Without that,
    `<repo>-worktree` activity would be credited to `<repo>`.
    """
    result = {worktrees.path_key(p): None for p in worktree_paths}
    if not os.path.isdir(CLAUDE_PROJECTS):
        return result

    candidates = [(worktrees.path_key(p), _mangle(p).casefold()) for p in worktree_paths]

    try:
        entries = list(os.scandir(CLAUDE_PROJECTS))
    except OSError:
        return result

    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue

        name = entry.name.casefold()
        best_key, best_len = None, -1
        for key, mangled in candidates:
            if name == mangled or name.startswith(mangled + '-'):
                if len(mangled) > best_len:
                    best_key, best_len = key, len(mangled)
        if best_key is None:
            continue

        newest = None
        try:
            for f in glob.iglob(os.path.join(glob.escape(entry.path), '*.jsonl')):
                try:
                    mtime = os.stat(f).st_mtime
                except OSError:
                    continue
                if newest is None or mtime > newest:
                    newest = mtime
        except OSError:
            continue

        if newest is not None and (result[best_key] is None or newest > result[best_key]):
            result[best_key] = newest

    return result


# ── Log writing ──────────────────────────────────────────────────────────────
def _fingerprint(wt):
    d = wt.get('dirty') or {}
    return (
        wt.get('branch'),
        wt.get('head'),
        d.get('staged'), d.get('unstaged'), d.get('untracked'), d.get('conflicted'),
        wt.get('error'),
    )


def _append(records):
    if not records:
        return
    _ensure_dir()
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def poll_once(roots=None):
    """Take one sample of every worktree and append whatever changed. Returns records."""
    roots = roots if roots is not None else load_roots()
    if not roots:
        return []

    data = worktrees.list_worktrees(roots)
    flat = [wt for repo in data['repos'] for wt in repo['worktrees'] if not wt.get('bare')]

    claude = claude_activity([wt['path'] for wt in flat])
    now = time.time()
    now_iso = _iso(_now())
    records = []

    with _lock:
        # An unobservable repo has to be recorded, otherwise a reader cannot tell
        # "nothing happened here" from "we could not look". Change-triggered, so a
        # permanently broken root costs one record, not one per minute.
        seen_roots = set()
        for err in data.get('errors') or []:
            root_key = worktrees.path_key(err['root'])
            seen_roots.add(root_key)
            if _last_errors.get(root_key) != err['error']:
                _last_errors[root_key] = err['error']
                records.append({
                    'ts': now_iso,
                    'why': ['error'],
                    'path': err['root'],
                    'name': os.path.basename(os.path.normpath(err['root'])),
                    'error': err['error'],
                })
        for root_key in list(_last_errors):
            if root_key not in seen_roots:
                del _last_errors[root_key]

        # A worktree that stops being reported ends its series explicitly, so a reader
        # never extends the last known state forward forever.
        live = {worktrees.path_key(wt['path']) for wt in flat}
        for key in list(_last):
            if key not in live:
                gone = _last.pop(key)
                records.append({
                    'ts': now_iso,
                    'why': ['gone'],
                    'path': gone['path'],
                    'name': gone['name'],
                    'branch': gone['fp'][0],
                })

        for wt in flat:
            key = worktrees.path_key(wt['path'])
            fp = _fingerprint(wt)
            claude_at = claude.get(key)
            prev = _last.get(key)

            why = []
            if prev is None:
                why.append('start')
            else:
                if fp[0] != prev['fp'][0]:
                    why.append('branch')
                if fp[1] != prev['fp'][1]:
                    why.append('commit')
                if fp[6] != prev['fp'][6]:
                    # Counts go null when a worktree becomes unreadable; reporting that
                    # as a file change would be misleading
                    why.append('error')
                elif fp[2:6] != prev['fp'][2:6]:
                    why.append('files')
                if claude_at and claude_at != prev.get('claudeAt'):
                    why.append('claude')
                if not why and now - prev['ts'] >= HEARTBEAT_SEC:
                    why.append('heartbeat')

            if not why:
                continue

            d = wt.get('dirty') or {}
            records.append({
                'ts': now_iso,
                'why': why,
                'repo': wt.get('repo'),
                'path': wt['path'],
                'name': wt.get('name'),
                'branch': wt.get('branch'),
                'head': (wt.get('head') or '')[:12],
                'main': bool(wt.get('isMain')),
                'dirty': d.get('total'),
                'split': [d.get('staged'), d.get('unstaged'), d.get('untracked'), d.get('conflicted')],
                'claudeAt': _iso_epoch(claude_at),
            })
            if wt.get('error'):
                records[-1]['error'] = wt['error']
            _last[key] = {
                'fp': fp, 'ts': now, 'claudeAt': claude_at,
                'path': wt['path'], 'name': wt.get('name'),
            }

    _append(records)
    return records


# ── Log reading and pruning ──────────────────────────────────────────────────
def read_log(days=1):
    """Return records from the last `days` days, oldest first. Bad lines are skipped."""
    cutoff = _iso(_now() - timedelta(days=max(0, days)))
    out = []
    try:
        with open(LOG_PATH, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get('ts', '') >= cutoff:
                    out.append(rec)
    except OSError:
        return []
    return out


def prune(days=RETENTION_DAYS):
    """Drop records older than `days`. Rewrites the log in place."""
    if not os.path.isfile(LOG_PATH):
        return 0
    cutoff = _iso(_now() - timedelta(days=days))
    kept, dropped = [], 0
    with open(LOG_PATH, encoding='utf-8') as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                ts = json.loads(stripped).get('ts', '')
            except ValueError:
                dropped += 1
                continue
            if ts >= cutoff:
                kept.append(stripped)
            else:
                dropped += 1

    if dropped:
        tmp = LOG_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            for line in kept:
                f.write(line + '\n')
        os.replace(tmp, LOG_PATH)
    return dropped


# ── Background poller ────────────────────────────────────────────────────────
def _loop(interval):
    global _pruned_on
    while True:
        try:
            today = _now().date()
            if _pruned_on != today:
                _pruned_on = today
                prune()
            poll_once()
        except Exception as exc:
            # A logger must never take the dashboard down with it
            print(f'activity: poll failed: {exc}')
        time.sleep(interval)


def start(interval=POLL_SEC):
    threading.Thread(target=_loop, args=(interval,), daemon=True).start()
