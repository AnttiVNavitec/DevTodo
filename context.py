#!/usr/bin/env python3
"""
Work-context storage: a folder per work item, collected artifacts, and dated notes.

Everything lives under one base folder the dashboard supplies. That mirrors how repo
roots work — the setting lives in browser localStorage, the server only validates it —
and nothing here knows about any particular ticket system: a context is named by
whatever key the client hands over, and that name has to survive `safe_segment`.

Layout under <base>:

    TROL-123/                          one folder per work item, created on demand
        builds/2026-09-15_1432/...     whatever a collect rule copied in
    _notes/2026-09-15.md               one Markdown file per local day, append-only

Notes are deliberately **not** also written into the per-item folders. The day file is
the single source of truth and the per-item view is a filter over those files; two
copies would drift the moment anyone edited one of them.

Note dates and times are **local**, unlike the rest of the app, which buckets by UTC
day. A note file is a human artifact meant to be opened in an editor, and a note written
at 23:00 on Tuesday belongs to Tuesday rather than to Wednesday's UTC bucket.
"""
import datetime
import glob as globlib
import os
import re
import shutil
import threading

NOTES_DIR = '_notes'

# A collect run that would move a whole build tree is much more likely to be a bad glob
# than an intention, so it is refused rather than left to finish.
MAX_COLLECT_FILES = 500
MAX_COLLECT_BYTES = 512 * 1024 * 1024

# Counting files in a context folder must never walk a huge build output to the end
MAX_STAT_ENTRIES = 5000

_write_lock = threading.Lock()

_SEGMENT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$')
_RESERVED = {
    'con', 'prn', 'aux', 'nul',
    *(f'com{i}' for i in range(1, 10)),
    *(f'lpt{i}' for i in range(1, 10)),
}
_NOTE_HEAD = re.compile(r'^## (\d{2}:\d{2})(?:\s+·\s+(.*?))?\s*$')


class Invalid(Exception):
    """The request named something we will not write to. The message is shown to the user."""


# ── Paths ────────────────────────────────────────────────────────────────────
def safe_segment(name):
    """One path component, or raise. No separators, no traversal, no Windows landmines."""
    name = (name or '').strip()
    if not _SEGMENT_RE.match(name):
        raise Invalid(f'Not a usable folder name: "{name}"')
    if name.rstrip('. ') != name:
        raise Invalid(f'Folder names cannot end in a dot or space: "{name}"')
    if name.split('.')[0].lower() in _RESERVED:
        raise Invalid(f'"{name}" is a reserved name on Windows')
    return name


def base_dir(base, create=False):
    """
    Resolve the configured base folder.

    `create=True` will make the folder itself but never its parent: a typo in a path
    should fail loudly, not quietly build a directory tree somewhere unexpected.
    """
    base = (base or '').strip()
    if not base:
        raise Invalid('No context folder is configured — set one in Settings.')
    path = os.path.abspath(os.path.expanduser(base))
    if os.path.isdir(path):
        return path
    if not create:
        raise Invalid(f'Context folder does not exist: {path}')
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        raise Invalid(f'Cannot create {path} — its parent folder does not exist.')
    os.makedirs(path, exist_ok=True)
    return path


def _under(root, path):
    """True when `path` is inside `root`. Component-wise, so a sibling named like a prefix
    of `root` cannot pass."""
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:      # different drives
        return False


def _join(root, *segments):
    path = os.path.abspath(os.path.join(root, *segments))
    if not _under(root, path):
        raise Invalid('Refusing to write outside the context folder')
    return path


def context_path(base, name, create=False):
    """Absolute path of one work item's folder. Returns (path, created)."""
    root = base_dir(base, create=create)
    path = _join(root, safe_segment(name))
    if os.path.isdir(path):
        return path, False
    if not create:
        return path, False
    os.makedirs(path, exist_ok=True)
    return path, True


def _measure(path):
    """(file count, total bytes) under `path`, giving up after MAX_STAT_ENTRIES."""
    files = total = seen = 0
    stack = [path]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > MAX_STAT_ENTRIES:
                return files, total
            try:
                if entry.is_dir():
                    stack.append(entry.path)
                else:
                    files += 1
                    total += entry.stat().st_size
            except OSError:
                continue
    return files, total


def list_contexts(base):
    """Every work-item folder under the base, newest first. Missing base is not an error."""
    try:
        root = base_dir(base)
    except Invalid as exc:
        return {'base': (base or '').strip(), 'exists': False, 'contexts': [], 'error': str(exc)}

    contexts = []
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        return {'base': root, 'exists': True, 'contexts': [], 'error': str(exc)}

    for entry in entries:
        if entry.name.startswith(('.', '_')):
            continue
        try:
            if not entry.is_dir():
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        files, size = _measure(entry.path)
        contexts.append({
            'name': entry.name,
            'path': entry.path.replace('\\', '/'),
            'files': files,
            'bytes': size,
            'mtime': mtime,
        })

    contexts.sort(key=lambda c: c['mtime'], reverse=True)
    return {'base': root.replace('\\', '/'), 'exists': True, 'contexts': contexts}


# ── Collecting files out of a worktree ───────────────────────────────────────
def _clean_pattern(pattern):
    pattern = (pattern or '').strip().replace('\\', '/')
    if not pattern:
        raise Invalid('Empty file pattern')
    if os.path.isabs(pattern) or re.match(r'^[A-Za-z]:', pattern):
        raise Invalid(f'File patterns are relative to the worktree: "{pattern}"')
    if any(part == '..' for part in pattern.split('/')):
        raise Invalid(f'File patterns cannot step outside the worktree: "{pattern}"')
    return pattern


def _dest_dir(base, name, dest, stamp):
    path, _ = context_path(base, name, create=True)
    for part in (dest or '').replace('\\', '/').split('/'):
        if part.strip():
            path = _join(path, safe_segment(part))
    if stamp:
        path = _join(path, datetime.datetime.now().strftime('%Y-%m-%d_%H%M'))
    return path


def collect(base, name, source, rule):
    """
    Copy everything a rule's patterns match in `source` into the item's context folder.

    The caller is responsible for checking `source` is a directory we are willing to read
    — see server.py, which only accepts a path git itself reports as a worktree.

    Matches keep their path relative to the worktree, so a rule that catches two files of
    the same name in different folders does not silently drop one.
    """
    if not os.path.isdir(source):
        raise Invalid(f'Source folder does not exist: {source}')

    patterns = [_clean_pattern(p) for p in (rule.get('patterns') or []) if str(p).strip()]
    if not patterns:
        raise Invalid('This rule has no file patterns')

    matches, seen = [], set()
    for pattern in patterns:
        for rel in globlib.glob(pattern, root_dir=source, recursive=True):
            rel = rel.replace('\\', '/')
            if rel in seen:
                continue
            full = _join(os.path.abspath(source), rel)
            if not os.path.isfile(full):
                continue
            seen.add(rel)
            try:
                matches.append((rel, full, os.path.getsize(full)))
            except OSError:
                continue

    if not matches:
        return {'copied': [], 'bytes': 0, 'dest': None,
                'message': f'Nothing matched {", ".join(patterns)}'}

    total = sum(size for _, _, size in matches)
    if len(matches) > MAX_COLLECT_FILES:
        raise Invalid(f'{len(matches)} files matched — narrow the pattern '
                      f'(the limit is {MAX_COLLECT_FILES})')
    if total > MAX_COLLECT_BYTES:
        raise Invalid(f'{total / 1048576:.0f} MB matched — narrow the pattern '
                      f'(the limit is {MAX_COLLECT_BYTES // 1048576} MB)')

    dest = _dest_dir(base, name, rule.get('dest'), bool(rule.get('stamp')))
    os.makedirs(dest, exist_ok=True)

    copied = []
    for rel, full, _ in sorted(matches):
        # `rel` came out of glob, so it names real files; _join is what keeps a symlinked
        # or oddly named match from landing outside `dest`. safe_segment is not used here:
        # it would reject perfectly ordinary build artifacts over a character class.
        target = _join(dest, *rel.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(full, target)
        copied.append(rel)

    return {'copied': copied, 'bytes': total, 'dest': dest.replace('\\', '/')}


# ── Notes ────────────────────────────────────────────────────────────────────
def _notes_dir(base, create=False):
    root = base_dir(base, create=create)
    path = _join(root, NOTES_DIR)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _day_file(base, date, create=False):
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date or ''):
        raise Invalid(f'Not a date: "{date}"')
    return _join(_notes_dir(base, create=create), f'{date}.md')


def _escape_body(text):
    """A note body line that looks like a note heading would split the note on the way
    back in. A leading backslash is markdown's own escape, so it renders identically."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return '\n'.join('\\' + ln if _NOTE_HEAD.match(ln) else ln for ln in lines)


def _unescape_body(line):
    return line[1:] if line.startswith('\\##') else line


def append_note(base, text, tag=None):
    """Append one note to today's file. Returns the stored record."""
    text = (text or '').strip()
    if not text:
        raise Invalid('The note is empty')

    now = datetime.datetime.now()
    date = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M')
    tag = (tag or '').strip().replace('\n', ' ')

    heading = f'## {time_str}' + (f' · {tag}' if tag else '')
    path = _day_file(base, date, create=True)

    with _write_lock:
        fresh = not os.path.exists(path)
        with open(path, 'a', encoding='utf-8', newline='\n') as f:
            if fresh:
                f.write(f'# Notes {date}\n\n')
            f.write(f'{heading}\n{_escape_body(text)}\n\n')

    return {'date': date, 'time': time_str, 'at': f'{date}T{time_str}',
            'tag': tag, 'text': text, 'file': path.replace('\\', '/')}


def _parse_day(path, date):
    try:
        with open(path, encoding='utf-8') as f:
            lines = f.read().split('\n')
    except OSError:
        return []

    notes, cur = [], None
    for line in lines:
        head = _NOTE_HEAD.match(line)
        if head:
            if cur:
                notes.append(cur)
            cur = {'date': date, 'time': head.group(1), 'at': f'{date}T{head.group(1)}',
                   'tag': (head.group(2) or '').strip(), 'text': []}
        elif cur is not None:
            cur['text'].append(_unescape_body(line))
    if cur:
        notes.append(cur)

    for note in notes:
        note['text'] = '\n'.join(note['text']).strip()
    return [n for n in notes if n['text']]


def read_day(base, date):
    """Every note on one local date. A day with no file is empty, not an error."""
    path = _day_file(base, date)
    if not os.path.isfile(path):
        return []
    return _parse_day(path, date)


def note_days(base):
    """Dates that have a notes file, newest first."""
    try:
        path = _notes_dir(base)
    except Invalid:
        return []
    days = []
    try:
        for entry in os.scandir(path):
            stem = entry.name[:-3]
            if entry.name.endswith('.md') and re.match(r'^\d{4}-\d{2}-\d{2}$', stem):
                days.append(stem)
    except OSError:
        return []
    return sorted(days, reverse=True)


def search_notes(base, query=None, tag=None, days=90, limit=300):
    """
    Notes matching a free-text query and/or a tag substring, newest first.

    Both filters are case-insensitive substrings. The tag filter is a substring rather
    than an equality test so that passing a ticket key matches every note tagged
    "KEY: summary", whatever the summary was at the time.
    """
    query = (query or '').strip().lower()
    tag = (tag or '').strip().lower()
    out = []
    for date in note_days(base)[:max(0, days)]:
        for note in _parse_day(_day_file(base, date), date):
            if tag and tag not in note['tag'].lower():
                continue
            if query and query not in note['text'].lower() and query not in note['tag'].lower():
                continue
            out.append(note)
            if len(out) >= limit:
                return out
    return out
