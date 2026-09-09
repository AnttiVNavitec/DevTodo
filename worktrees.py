#!/usr/bin/env python3
"""
Git worktree discovery for the DevTodo dashboard.

Given a list of repo roots (any worktree of a repo will do — git reports the whole
set from any of them), reports every worktree with its branch, working-tree state,
upstream divergence and last commit.

Nothing here is repo- or project-specific: roots come from the caller.
"""
import concurrent.futures
import datetime
import os
import subprocess
import time

import transcripts

GIT_TIMEOUT = 20
CACHE_TTL = 3.0
MAX_PARALLEL = 8

# Keep git from flashing a console window on Windows
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# Single immutable (key, timestamp, data) snapshot. The dashboard and the activity
# poller call in from different threads, so this is swapped in one assignment rather
# than field by field — a reader must never see a new key alongside stale data.
_cache = None


def _git(cwd, *args):
    """Run git in cwd. Returns (ok, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ('git', '-C', cwd) + args,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        return False, '', 'git executable not found on PATH'
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, '', str(exc)
    return proc.returncode == 0, proc.stdout, (proc.stderr or '').strip()


def _iso(epoch):
    """Epoch seconds → UTC ISO string, matching what the rest of the app stores."""
    if not epoch:
        return None
    return (datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
            .isoformat(timespec='seconds').replace('+00:00', 'Z'))


def default_base(path):
    """The repo's own default branch, e.g. 'origin/Develop'. None if origin/HEAD is unset."""
    ok, out, _ = _git(path, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD')
    ref = (out or '').strip()
    if ok and ref.startswith('refs/remotes/'):
        return ref[len('refs/remotes/'):]
    return None


def path_key(path):
    """Comparable form of a path — case-insensitive, one separator, no trailing slash."""
    return os.path.normpath(str(path)).replace('\\', '/').rstrip('/').casefold()


# ── Parsing ──────────────────────────────────────────────────────────────────
def _parse_worktree_list(out):
    """Parse `git worktree list --porcelain` into a list of dicts."""
    records, cur = [], {}

    def flush():
        if cur.get('path'):
            records.append(dict(cur))
        cur.clear()

    for line in out.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, val = line.partition(' ')
        val = val.strip()
        if key == 'worktree':
            flush()
            cur['path'] = val
        elif key == 'HEAD':
            cur['head'] = val
        elif key == 'branch':
            # refs/heads/some-branch → some-branch
            cur['branch'] = val[len('refs/heads/'):] if val.startswith('refs/heads/') else val
        elif key == 'bare':
            cur['bare'] = True
        elif key == 'detached':
            cur['detached'] = True
        elif key == 'locked':
            cur['locked'] = True
            cur['lockedReason'] = val
        elif key == 'prunable':
            cur['prunable'] = True
            cur['prunableReason'] = val
    flush()
    return records


def _parse_status(out):
    """Parse `git status --porcelain=v2 --branch` into counts and upstream info."""
    st = {
        'staged': 0, 'unstaged': 0, 'untracked': 0, 'conflicted': 0, 'total': 0,
        'upstream': None, 'ahead': 0, 'behind': 0,
    }
    for line in out.splitlines():
        if line.startswith('# branch.'):
            parts = line.split(' ', 2)
            if len(parts) < 3:
                continue
            field, val = parts[1], parts[2].strip()
            if field == 'branch.upstream':
                st['upstream'] = val
            elif field == 'branch.ab':
                for tok in val.split():
                    try:
                        n = int(tok[1:])
                    except ValueError:
                        continue
                    if tok[0] == '+':
                        st['ahead'] = n
                    elif tok[0] == '-':
                        st['behind'] = n
        elif line[:2] in ('1 ', '2 '):
            xy = line[2:4]
            if len(xy) == 2:
                if xy[0] != '.':
                    st['staged'] += 1
                if xy[1] != '.':
                    st['unstaged'] += 1
        elif line.startswith('u '):
            st['conflicted'] += 1
        elif line.startswith('? '):
            st['untracked'] += 1

    st['total'] = st['staged'] + st['unstaged'] + st['conflicted'] + st['untracked']
    return st


# ── Per-worktree detail ──────────────────────────────────────────────────────
def _describe(rec):
    """Fill in working-tree state and last commit for one worktree record."""
    wt = {
        'path': rec.get('path', ''),
        'name': os.path.basename(os.path.normpath(rec.get('path', ''))),
        'branch': rec.get('branch'),
        'head': rec.get('head'),
        'bare': bool(rec.get('bare')),
        'detached': bool(rec.get('detached')),
        'locked': bool(rec.get('locked')),
        'lockedReason': rec.get('lockedReason') or '',
        'prunable': bool(rec.get('prunable')),
        'prunableReason': rec.get('prunableReason') or '',
        'dirty': None,
        'lastCommit': None,
        'error': None,
    }

    if wt['bare']:
        return wt

    if not os.path.isdir(wt['path']):
        wt['error'] = 'worktree directory is missing'
        return wt

    ok, out, err = _git(wt['path'], 'status', '--porcelain=v2', '--branch')
    if ok:
        wt['dirty'] = _parse_status(out)
    else:
        wt['error'] = err or 'git status failed'

    ok, out, err = _git(wt['path'], 'log', '-1', '--format=%H%x1f%s%x1f%ct')
    if ok and out.strip():
        parts = out.strip().split('\x1f')
        if len(parts) == 3:
            try:
                ts = int(parts[2])
            except ValueError:
                ts = None
            wt['lastCommit'] = {'sha': parts[0], 'subject': parts[1], 'ts': ts}

    return wt


def _collect_repo(root):
    """Discover all worktrees of the repo containing `root`."""
    if not os.path.isdir(root):
        return None, {'root': root, 'error': 'directory does not exist'}

    ok, out, err = _git(root, 'worktree', 'list', '--porcelain')
    if not ok:
        return None, {'root': root, 'error': err or 'not a git repository'}

    records = _parse_worktree_list(out)
    if not records:
        return None, {'root': root, 'error': 'no worktrees reported'}

    # git lists the main worktree first
    main_path = records[0].get('path', root)
    return {
        'name': os.path.basename(os.path.normpath(main_path)),
        'main': main_path,
        'base': default_base(main_path),
        'records': records,
    }, None


def list_worktrees(roots, fresh=False):
    """
    Discover worktrees for every configured root.

    Returns {'repos': [...], 'errors': [...], 'cached': bool}. Repos are deduplicated
    by main-worktree path, so listing both a repo and one of its worktrees is harmless.

    `fresh=True` bypasses the read cache — required before acting on a guard, since a
    three-second-old dirty count is not something to change branches on.
    """
    global _cache

    roots = [r.strip() for r in (roots or []) if r and r.strip()]
    cache_key = tuple(path_key(r) for r in roots)
    now = time.monotonic()

    snapshot = None if fresh else _cache
    if snapshot is not None:
        key, ts, cached = snapshot
        if key == cache_key and now - ts < CACHE_TTL:
            data = dict(cached)
            data['cached'] = True
            return data

    repos, errors, seen = [], [], set()

    if roots:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            for repo, error in pool.map(_collect_repo, roots):
                if error:
                    errors.append(error)
                    continue
                key = path_key(repo['main'])
                if key in seen:
                    continue
                seen.add(key)
                repos.append(repo)

        # Detail every worktree of every repo in one flat parallel pass
        flat = [(repo, rec) for repo in repos for rec in repo['records']]
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            details = list(pool.map(_describe, [rec for _, rec in flat]))

        # One scan of the transcript directory covers every worktree at once
        claude = transcripts.claude_activity([rec.get('path', '') for _, rec in flat])

        for repo in repos:
            repo['worktrees'] = []
        for (repo, _), wt in zip(flat, details):
            wt['isMain'] = path_key(wt['path']) == path_key(repo['main'])
            wt['repo'] = repo['name']
            wt['claudeAt'] = _iso(claude.get(wt['path']))
            repo['worktrees'].append(wt)
        for repo in repos:
            repo.pop('records', None)

    repos.sort(key=lambda r: r['name'].casefold())
    data = {'repos': repos, 'errors': errors, 'cached': False}
    _cache = (cache_key, now, data)
    return data


def scan_for_repos(parent):
    """List immediate subdirectories of `parent` that look like git checkouts."""
    if not parent or not os.path.isdir(parent):
        return []
    found = []
    try:
        entries = sorted(os.scandir(parent), key=lambda e: e.name.casefold())
    except OSError:
        return []
    for entry in entries:
        try:
            if not entry.is_dir():
                continue
        except OSError:
            continue
        # A linked worktree has a .git *file*, a normal checkout a .git directory
        if os.path.exists(os.path.join(entry.path, '.git')):
            found.append(entry.path.replace('\\', '/'))
    return found
