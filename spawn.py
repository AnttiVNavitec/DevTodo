#!/usr/bin/env python3
"""
Launch external tools in a worktree directory.

The client picks a tool by id from a fixed table and names a directory; it never supplies
a command line. The caller is responsible for checking the directory is one git actually
reports as a worktree — see server.py. Between those two rules, this endpoint cannot be
talked into running something arbitrary.

Tool locations are discovered rather than hardcoded, and a missing tool produces a message
meant for the dashboard rather than a traceback.
"""
import os
import shutil
import subprocess

# Don't let a Ctrl+C in the server's console tear down terminals it spawned
_FLAGS = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)


class ToolMissing(Exception):
    """A tool is not installed, or not where we can find it."""


def _wrap(argv):
    """CreateProcess cannot run .cmd/.bat directly — those need a shell in front."""
    exe = (argv[0] or '').lower()
    if exe.endswith(('.cmd', '.bat')):
        return ['cmd', '/c', *argv]
    return argv


def _find_git_bash():
    direct = shutil.which('git-bash')
    if direct:
        return direct

    candidates = [
        os.path.join(os.environ.get('ProgramFiles', ''), 'Git', 'git-bash.exe'),
        os.path.join(os.environ.get('ProgramFiles(x86)', ''), 'Git', 'git-bash.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'Git', 'git-bash.exe'),
    ]
    git = shutil.which('git')
    if git:
        # <install>/cmd/git.exe or <install>/bin/git.exe → <install>/git-bash.exe
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(git)), 'git-bash.exe'))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def _explorer(path):
    return ['explorer', os.path.normpath(path)]


def _gitbash(path):
    exe = _find_git_bash()
    if not exe:
        raise ToolMissing('Git Bash not found — looked on PATH and in the standard '
                          'Git for Windows install locations.')
    return [exe, f'--cd={path}']


def _terminal(path):
    exe = shutil.which('wt')
    if not exe:
        raise ToolMissing('Windows Terminal (wt.exe) not found on PATH.')
    return [exe, '-d', path]


def _vscode(path):
    exe = shutil.which('code')
    if not exe:
        raise ToolMissing("VS Code's 'code' command not found on PATH. In VS Code, run "
                          "\"Shell Command: Install 'code' command in PATH\".")
    return [exe, path]


def _claude(path):
    exe = shutil.which('wt')
    if exe:
        return [exe, '-d', path, 'cmd', '/k', 'claude']
    # No Windows Terminal: settle for a plain console window in the worktree
    return ['cmd', '/c', 'start', '', 'cmd', '/k', 'claude']


TOOLS = {
    'explorer': ('File Explorer',     _explorer),
    'gitbash':  ('Git Bash',          _gitbash),
    'terminal': ('Windows Terminal',  _terminal),
    'vscode':   ('VS Code',           _vscode),
    'claude':   ('Claude Code',       _claude),
}


def open_in(tool, path):
    """Launch `tool` in `path`. Returns the tool's display name. Raises on failure."""
    entry = TOOLS.get(tool)
    if entry is None:
        raise ValueError(f'Unknown tool: {tool}')
    if not os.path.isdir(path):
        raise ValueError('Directory does not exist')

    name, build = entry
    argv = _wrap(build(path))

    # Explorer reports failure exit codes even when it worked, so nothing here waits on
    # or inspects the child — these are all fire-and-forget window openers.
    subprocess.Popen(argv, cwd=path, close_fds=True, creationflags=_FLAGS)
    return name
