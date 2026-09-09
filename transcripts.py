#!/usr/bin/env python3
"""
Claude Code transcript probe.

Claude Code keeps a session transcript per working directory under
`~/.claude/projects/<mangled-path>/*.jsonl`. The newest mtime in that directory is a good
proxy for "an agent was doing something here", which is what lets the dashboard both
attribute activity and refuse to move a branch out from under a running agent.

This layout is not a documented interface, so every failure path here degrades to
"no signal" rather than raising.

Deliberately imports nothing from the rest of the app: both worktrees.py and activity.py
need it, and a shared leaf module keeps them from importing each other.
"""
import glob
import os

CLAUDE_PROJECTS = os.path.join(os.path.expanduser('~'), '.claude', 'projects')


def mangle(path):
    """Mangle a filesystem path the way Claude Code names its project directories."""
    norm = os.path.normpath(str(path)).replace('\\', '-').replace('/', '-').replace(':', '-')
    # The drive letter is lowercased; the rest of the path keeps its case
    return (norm[:1].lower() + norm[1:]) if norm else norm


def claude_activity(paths):
    """
    Map each given path → epoch seconds of the newest Claude transcript write, or None.
    Keys are the exact strings passed in, so callers can index by whatever they already hold.

    A session started in a subdirectory of a worktree gets a longer mangled name, so each
    project directory is attributed to the *longest* matching path. Without that,
    `<repo>-worktree` activity would be credited to `<repo>`.
    """
    result = {p: None for p in paths}
    if not paths or not os.path.isdir(CLAUDE_PROJECTS):
        return result

    candidates = [(p, mangle(p).casefold()) for p in paths]

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
        best_path, best_len = None, -1
        for path, mangled in candidates:
            if name == mangled or name.startswith(mangled + '-'):
                if len(mangled) > best_len:
                    best_path, best_len = path, len(mangled)
        if best_path is None:
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

        if newest is not None and (result[best_path] is None or newest > result[best_path]):
            result[best_path] = newest

    return result
