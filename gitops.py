#!/usr/bin/env python3
"""
Mutating git operations on worktrees.

Everything here changes state on disk, so the shape mirrors spawn.py: the client names an
operation from a fixed table and supplies parameters, never a command line. Guards are
enforced *here* rather than only in the UI — the dashboard disables buttons as an
affordance, but this module is what actually refuses.

Guards, applied to every worktree an operation touches:
  * uncommitted changes  → refuse (no stashing, no --force)
  * an agent active in the last GUARD_AGENT_SEC → refuse
  * unreadable worktree  → refuse

Git's own stderr is returned verbatim. It is never summarised: a git error usually says
exactly what to do, and paraphrasing it loses that.

Out of scope on purpose: merge, rebase, push, commit. Those have interactive failure
modes and belong in a terminal.
"""
import datetime
import os
import subprocess

import worktrees

GIT_TIMEOUT = 120          # a fetch on a large repo is slow
GUARD_AGENT_SEC = 120      # "Claude just touched this" window

_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class Refused(Exception):
    """A guard said no. The message is meant to be shown to the user."""


class GitFailed(Exception):
    """Git exited non-zero. Carries its stderr verbatim."""

    def __init__(self, command, stderr):
        super().__init__(stderr or 'git failed')
        self.command = command
        self.stderr = stderr


# ── Running git ──────────────────────────────────────────────────────────────
def _run(cwd, *args):
    """Run one git command, raising GitFailed with its verbatim stderr."""
    argv = ('git', '-C', cwd) + args
    pretty = 'git -C ' + cwd + ' ' + ' '.join(args)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=GIT_TIMEOUT, creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        raise GitFailed(pretty, 'git executable not found on PATH')
    except subprocess.TimeoutExpired:
        raise GitFailed(pretty, f'git did not finish within {GIT_TIMEOUT}s')
    except OSError as exc:
        raise GitFailed(pretty, str(exc))

    if proc.returncode != 0:
        raise GitFailed(pretty, (proc.stderr or proc.stdout or '').strip())
    # fetch and switch report what they did on stderr, so fall back to it for the log
    return pretty, ((proc.stdout or '').strip() or (proc.stderr or '').strip())


def _read(cwd, *args):
    """Run git for its output, returning '' instead of raising."""
    try:
        return _run(cwd, *args)[1]
    except GitFailed:
        return ''


# ── Worktree index and guards ────────────────────────────────────────────────
def index(roots):
    """Fresh {path_key: worktree} plus {path_key: repo} across every configured root."""
    data = worktrees.list_worktrees(roots, fresh=True)
    by_path, repo_of = {}, {}
    for repo in data['repos']:
        for wt in repo['worktrees']:
            key = worktrees.path_key(wt['path'])
            by_path[key] = wt
            repo_of[key] = repo
    return by_path, repo_of, data


def _resolve(by_path, path, what='worktree'):
    wt = by_path.get(worktrees.path_key(path or ''))
    if wt is None:
        raise Refused(f'Not a known {what}: {path}')
    return wt


def agent_idle_for(wt):
    """Seconds since an agent last touched this worktree, or None if never/unknown."""
    stamp = wt.get('claudeAt')
    if not stamp:
        return None
    try:
        when = datetime.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError:
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - when).total_seconds()


def guard(wt, action='change'):
    """Refuse if this worktree must not be touched right now."""
    name = wt.get('name') or wt.get('path')

    if wt.get('error'):
        raise Refused(f'{name} cannot be read: {wt["error"]}')
    if wt.get('locked'):
        raise Refused(f'{name} is locked{": " + wt["lockedReason"] if wt.get("lockedReason") else ""}')

    dirty = (wt.get('dirty') or {}).get('total') or 0
    if dirty:
        raise Refused(f'{name} has {dirty} uncommitted change{"s" if dirty != 1 else ""} — '
                      f'commit or stash before you {action} it')

    idle = agent_idle_for(wt)
    if idle is not None and idle < GUARD_AGENT_SEC:
        raise Refused(f'Claude was active in {name} {int(idle)}s ago — '
                      f'wait before you {action} it')


def _holder_of(by_path, repo_of, branch, anchor):
    """
    The worktree in the SAME repo as `anchor` that has `branch` checked out, if any.

    Scoping to the repo is essential, not tidiness: the same ticket often has a branch of
    the same name in several repos, and an unscoped search would report a foreign repo's
    worktree as the holder — then a move would detach something unrelated.
    """
    want = worktrees.path_key(repo_of[worktrees.path_key(anchor['path'])]['main'])
    for key, wt in by_path.items():
        if wt.get('branch') != branch:
            continue
        if worktrees.path_key(repo_of[key]['main']) == want:
            return wt
    return None


# ── Branch listing (read-only, used by the pickers) ──────────────────────────
def branches(roots, path):
    """
    Local branches near `path`, newest commit first, noting which worktree holds each.

    Local only, deliberately. The dashboard narrows this list using things only it knows
    — my Jira keys, my merge requests' source branches — so shipping several hundred
    remote refs to the browser would be waste. A branch that exists only on the remote is
    reached by passing `remote` to the checkout operation instead.
    """
    by_path, repo_of, _ = index(roots)
    wt = _resolve(by_path, path)

    fmt = '%(refname:short)' + chr(9) + '%(committerdate:unix)'
    out = _read(wt['path'], 'for-each-ref', '--sort=-committerdate',
                '--format=' + fmt, 'refs/heads')

    local = []
    for line in out.splitlines():
        parts = line.split(chr(9))
        if len(parts) != 2:
            continue
        short, when = parts
        try:
            when = int(when)
        except ValueError:
            when = 0
        holder = _holder_of(by_path, repo_of, short, wt)
        local.append({
            'name': short, 'ts': when,
            'heldBy': holder['name'] if holder else None,
            'heldPath': holder['path'] if holder else None,
        })

    return {'local': local, 'base': default_base(wt['path'])}


def default_base(path):
    """The repo's own default branch, from origin/HEAD. None if it isn't set."""
    return worktrees.default_base(path)


def _validate_branch_name(name):
    name = (name or '').strip()
    if not name:
        raise Refused('Branch name is required')
    # Let git be the authority on what a ref may be called
    try:
        _run(os.getcwd(), 'check-ref-format', '--branch', name)
    except GitFailed:
        raise Refused(f'"{name}" is not a valid branch name')
    return name


# ── Operations ───────────────────────────────────────────────────────────────
def op_checkout(roots, params):
    """Check an existing branch out in a clean, idle worktree."""
    by_path, repo_of, _ = index(roots)
    wt = _resolve(by_path, params.get('path'))
    branch = (params.get('branch') or '').strip()
    if not branch:
        raise Refused('Branch is required')

    guard(wt, 'switch')

    holder = _holder_of(by_path, repo_of, branch, wt)
    if holder and worktrees.path_key(holder['path']) != worktrees.path_key(wt['path']):
        raise Refused(f'{branch} is checked out in {holder["name"]}. '
                      f'Use "move branch here" instead — git will not check the same '
                      f'branch out twice.')

    local_exists = bool(_read(wt['path'], 'rev-parse', '--verify', '--quiet',
                              'refs/heads/' + branch))
    remote = (params.get('remote') or '').strip()

    if local_exists:
        return [_run(wt['path'], 'switch', branch)]

    if not remote:
        raise Refused(branch + ' does not exist locally — create it instead')

    # Reviewing someone else's merge request: fetch, then start a tracking branch
    steps = [_run(wt['path'], 'fetch', '--prune')]
    if not _read(wt['path'], 'rev-parse', '--verify', '--quiet', remote):
        raise Refused(remote + ' does not exist on the remote')
    steps.append(_run(wt['path'], 'switch', '--track', remote))
    return steps


def op_create(roots, params):
    """Create a branch from a base ref in a clean, idle worktree."""
    by_path, repo_of, _ = index(roots)
    wt = _resolve(by_path, params.get('path'))
    name = _validate_branch_name(params.get('name'))

    guard(wt, 'create a branch in')

    if _holder_of(by_path, repo_of, name, wt):
        raise Refused(f'{name} already exists and is checked out')
    if _read(wt['path'], 'rev-parse', '--verify', '--quiet', f'refs/heads/{name}'):
        raise Refused(f'{name} already exists — check it out instead of creating it')

    steps = []
    # Fetch first so the new branch does not start from a stale base
    if params.get('fetch', True):
        steps.append(_run(wt['path'], 'fetch', '--prune'))

    base = (params.get('base') or '').strip() or default_base(wt['path'])
    if not base:
        raise Refused('No base branch: origin/HEAD is not set for this repo, so pick one '
                      'explicitly or set it in Settings')
    if not _read(wt['path'], 'rev-parse', '--verify', '--quiet', base):
        raise Refused(f'Base ref "{base}" does not exist')

    # --no-track on purpose: tracking the base would point `git push` at the base branch
    steps.append(_run(wt['path'], 'switch', '-c', name, '--no-track', base))
    return steps


def op_move(roots, params):
    """
    Move a branch into a target worktree, detaching whoever holds it.

    This is the operation git refuses outright, and the reason the dashboard has branch
    controls at all. If the second step fails the first is rolled back, so a failure
    never leaves the branch checked out nowhere.
    """
    by_path, repo_of, _ = index(roots)
    target = _resolve(by_path, params.get('path'))
    branch = (params.get('branch') or '').strip()
    if not branch:
        raise Refused('Branch is required')

    guard(target, 'move a branch into')

    holder = _holder_of(by_path, repo_of, branch, target)
    if holder is None:
        raise Refused(f'{branch} is not checked out anywhere — check it out instead')
    if worktrees.path_key(holder['path']) == worktrees.path_key(target['path']):
        raise Refused(f'{branch} is already checked out in {target["name"]}')

    guard(holder, 'release the branch from')

    steps = [_run(holder['path'], 'switch', '--detach')]
    try:
        steps.append(_run(target['path'], 'switch', branch))
    except GitFailed:
        # Put the holder back rather than leaving the branch homeless
        try:
            steps.append(_run(holder['path'], 'switch', branch))
        except GitFailed:
            pass
        raise
    return steps


def op_detach(roots, params):
    """Detach a worktree's HEAD, freeing its branch for use elsewhere."""
    by_path, _, _ = index(roots)
    wt = _resolve(by_path, params.get('path'))
    guard(wt, 'detach')
    if wt.get('detached'):
        raise Refused(f'{wt["name"]} is already detached')
    return [_run(wt['path'], 'switch', '--detach')]


def op_worktree_add(roots, params):
    """
    Create a new worktree beside the main checkout.

    The name must be a plain directory name: it is joined to the main checkout's parent,
    so accepting a path would let the client write anywhere on disk.
    """
    by_path, repo_of, _ = index(roots)
    anchor = _resolve(by_path, params.get('path'))
    repo = repo_of[worktrees.path_key(anchor['path'])]

    name = (params.get('name') or '').strip().replace('\\', '/')
    if not name or '/' in name or name in ('.', '..'):
        raise Refused('Worktree folder must be a plain name, with no path separators')

    parent = os.path.dirname(os.path.normpath(repo['main']))
    dest = os.path.join(parent, name)
    if os.path.exists(dest):
        raise Refused(f'{dest} already exists')

    branch = (params.get('branch') or '').strip()
    new_branch = (params.get('newBranch') or '').strip()

    if new_branch:
        new_branch = _validate_branch_name(new_branch)
        base = (params.get('base') or '').strip() or default_base(repo['main'])
        if not base:
            raise Refused('No base branch: origin/HEAD is not set for this repo')
        return [_run(repo['main'], 'worktree', 'add', '-b', new_branch, dest, base)]

    if branch:
        holder = _holder_of(by_path, repo_of, branch, anchor)
        if holder:
            raise Refused(f'{branch} is checked out in {holder["name"]} — '
                          f'git will not check it out twice')
        return [_run(repo['main'], 'worktree', 'add', dest, branch)]

    # No branch given: detached at the current HEAD, ready to be pointed somewhere
    return [_run(repo['main'], 'worktree', 'add', '--detach', dest)]


def op_prune(roots, params):
    """Drop administrative records for worktrees whose directories are gone."""
    by_path, repo_of, _ = index(roots)
    anchor = _resolve(by_path, params.get('path'))
    repo = repo_of[worktrees.path_key(anchor['path'])]
    return [_run(repo['main'], 'worktree', 'prune', '-v')]


def op_fetch(roots, params):
    """Update remote-tracking refs. Read-only as far as the working tree is concerned."""
    by_path, _, _ = index(roots)
    wt = _resolve(by_path, params.get('path'))
    return [_run(wt['path'], 'fetch', '--prune')]


def op_sync_base(roots, params):
    """
    Bring the repo's default branch up to date **without checking it out**.

    This is the awkward one to do by hand. `git fetch origin X:X` updates a local branch
    in place, but git refuses it when X is checked out anywhere — so when some worktree
    does hold it, fast-forward that worktree instead. Neither path is allowed to move a
    branch that has diverged: no `+refspec`, no `--force`, `--ff-only` on the merge. If
    the local branch has commits the remote does not, that is a real situation and it
    should be reported rather than silently flattened.
    """
    by_path, repo_of, _ = index(roots)
    anchor = _resolve(by_path, params.get('path'))
    repo = repo_of[worktrees.path_key(anchor['path'])]

    base = (params.get('base') or '').strip() or repo.get('base') or default_base(repo['main'])
    if not base:
        raise Refused('No default branch: origin/HEAD is not set for this repository')
    if '/' not in base:
        raise Refused(f'Expected a remote-tracking ref like "origin/main", got "{base}"')

    remote, branch = base.split('/', 1)
    steps = [_run(repo['main'], 'fetch', '--prune', remote)]

    holder = _holder_of(by_path, repo_of, branch, anchor)
    if holder is not None:
        # Checked out somewhere, so update it there. Requires a clean, idle worktree.
        guard(holder, f'fast-forward {branch} in')
        steps.append(_run(holder['path'], 'merge', '--ff-only', base))
    else:
        # Nothing holds it: write the local branch straight from the remote
        steps.append(_run(repo['main'], 'fetch', remote, f'{branch}:{branch}'))

    return steps


OPERATIONS = {
    'checkout':     ('Check out branch',  op_checkout),
    'create':       ('Create branch',     op_create),
    'move':         ('Move branch',       op_move),
    'detach':       ('Detach worktree',   op_detach),
    'worktree-add': ('Add worktree',      op_worktree_add),
    'prune':        ('Prune worktrees',   op_prune),
    'fetch':        ('Fetch',             op_fetch),
    'sync-base':    ('Update base branch', op_sync_base),
}


def run(operation, roots, params):
    """
    Perform one operation from the table.

    Returns {'ok', 'operation', 'name', 'steps': [{command, output}]}. Raises Refused for
    a guard and GitFailed for git itself.
    """
    entry = OPERATIONS.get(operation)
    if entry is None:
        raise Refused(f'Unknown operation: {operation}')
    label, fn = entry
    steps = fn(roots, params or {})
    return {
        'ok': True,
        'operation': operation,
        'name': label,
        'steps': [{'command': cmd, 'output': out} for cmd, out in steps],
    }
