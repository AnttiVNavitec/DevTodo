#!/usr/bin/env python3
"""
Fetch open (unresolved) review comments from a GitLab merge request and write
them to a Markdown file, including file path, line number, git revision, and
source context lines.

Usage:
    python fetch_mr_comments.py <MR_IID> --project <namespace/project> [options]

Environment variables:
    GITLAB_TOKEN   Personal access token (or use --token)
    GITLAB_URL     GitLab base URL, default https://gitlab.com (or use --gitlab-url)
"""

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


def api_get(url: str, token: str):
    """Perform a single authenticated GET request and return parsed JSON."""
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": token})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"HTTP {exc.code} for {url}: {body}", file=sys.stderr)
        raise


def paginate(base_url: str, path: str, token: str) -> list:
    """Fetch all pages of a paginated GitLab API endpoint."""
    results = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        url = f"{base_url}/api/v4/{path}{sep}page={page}&per_page=100"
        data = api_get(url, token)
        if not data:
            break
        results.extend(data)
        if len(data) < 100:
            break
        page += 1
    return results


def get_file_context(
    base_url: str,
    encoded_project: str,
    file_path: str,
    ref: str,
    line_number: int,
    context_lines: int,
    token: str,
) -> tuple[Optional[list[str]], Optional[int]]:
    """
    Fetch source lines around `line_number` from the repository at `ref`.
    Returns (lines, first_line_number) or (None, None) on failure.
    """
    encoded_path = urllib.parse.quote(file_path, safe="")
    url = (
        f"{base_url}/api/v4/projects/{encoded_project}"
        f"/repository/files/{encoded_path}?ref={ref}"
    )
    try:
        data = api_get(url, token)
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        all_lines = content.splitlines()
        start_idx = max(0, line_number - context_lines - 1)
        end_idx = min(len(all_lines), line_number + context_lines)
        return all_lines[start_idx:end_idx], start_idx + 1
    except Exception as exc:
        # Context is best-effort; don't fail the whole run
        print(f"  Warning: could not fetch context for {file_path}@{ref}: {exc}", file=sys.stderr)
        return None, None


def file_language(file_path: str) -> str:
    """Return a Markdown fence language hint based on file extension."""
    ext_map = {
        "py": "python",
        "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "c": "c",
        "h": "cpp", "hpp": "cpp",
        "cs": "csharp",
        "ts": "typescript", "tsx": "typescript",
        "js": "javascript", "jsx": "javascript",
        "rs": "rust",
        "go": "go",
        "java": "java",
        "yaml": "yaml", "yml": "yaml",
        "json": "json",
        "xml": "xml",
        "sh": "bash", "bash": "bash",
        "cmake": "cmake",
        "md": "markdown",
    }
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    return ext_map.get(ext, ext)


def build_markdown(mr: dict, open_threads: list, mr_iid: str) -> str:
    lines = []

    lines.append(f"# Open Review Comments — MR !{mr_iid}\n")
    lines.append(f"**Title:** {mr.get('title', 'N/A')}  ")
    lines.append(f"**Author:** @{mr.get('author', {}).get('username', 'N/A')}  ")
    lines.append(
        f"**Branch:** `{mr.get('source_branch', 'N/A')}` → `{mr.get('target_branch', 'N/A')}`  "
    )
    lines.append(f"**Open threads:** {len(open_threads)}  ")
    lines.append(f"**MR URL:** {mr.get('web_url', 'N/A')}  ")
    lines.append("\n---\n")

    for i, thread in enumerate(open_threads, 1):
        file_label = thread["file"] or "unknown file"
        line_label = thread["line"] if thread["line"] else "?"
        lines.append(f"## Thread {i}: `{file_label}` — line {line_label}\n")

        lines.append(f"- **File:** `{file_label}`")
        lines.append(f"- **Line:** {line_label}")
        if thread["ref"]:
            lines.append(f"- **Head SHA:** `{thread['ref']}`")
        if thread["base_sha"]:
            lines.append(f"- **Base SHA:** `{thread['base_sha']}`")
        lines.append("")

        if thread["context_lines"] is not None:
            lang = file_language(file_label)
            ctx_start = thread["context_start_line"]
            ctx_end = ctx_start + len(thread["context_lines"]) - 1
            lines.append(f"**Source context** (lines {ctx_start}-{ctx_end}):\n")
            lines.append(f"```{lang}")
            for j, src_line in enumerate(thread["context_lines"]):
                lineno = ctx_start + j
                marker = ">>>" if lineno == thread["line"] else "   "
                lines.append(f"{marker} {lineno:5d} | {src_line}")
            lines.append("```\n")

        lines.append("**Comments:**\n")
        for note in thread["notes"]:
            resolved_tag = " *(resolved)*" if note["resolved"] else ""
            date = note["created_at"][:10]
            lines.append(f"**@{note['author']}** ({date}){resolved_tag}:\n")
            for body_line in note["body"].splitlines():
                lines.append(f"> {body_line}")
            lines.append("")

        lines.append("---\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Download open MR review comments from GitLab to Markdown."
    )
    parser.add_argument("mr_iid", help="Merge request IID (the !N number)")
    parser.add_argument(
        "--project", "-p",
        required=True,
        help='Project ID or path, e.g. "mygroup/myrepo" or 12345',
    )
    parser.add_argument(
        "--gitlab-url", "-g",
        default=os.environ.get("GITLAB_URL", "https://gitlab.com"),
        help="GitLab instance base URL (default: $GITLAB_URL or https://gitlab.com)",
    )
    parser.add_argument(
        "--token", "-t",
        default=os.environ.get("GITLAB_TOKEN"),
        help="GitLab personal access token (default: $GITLAB_TOKEN)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path (default: mr_<IID>_comments.md)",
    )
    parser.add_argument(
        "--context", "-c",
        type=int,
        default=4,
        help="Number of context lines above and below the commented line (default: 4)",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Skip fetching source context lines (faster, fewer API calls)",
    )
    args = parser.parse_args()

    if not args.token:
        print(
            "Error: GitLab token required. Set the GITLAB_TOKEN environment variable or use --token.",
            file=sys.stderr,
        )
        sys.exit(1)

    output_file = args.output or f"mr_{args.mr_iid}_comments.md"
    # URL-encode project path so slashes become %2F for the API
    encoded_project = urllib.parse.quote(str(args.project), safe="")
    base_url = args.gitlab_url.rstrip("/")

    print(f"Fetching MR !{args.mr_iid} from {base_url} …")
    mr = api_get(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{args.mr_iid}",
        args.token,
    )

    print("Fetching discussions …")
    discussions = paginate(
        base_url,
        f"projects/{encoded_project}/merge_requests/{args.mr_iid}/discussions",
        args.token,
    )
    print(f"  {len(discussions)} total discussion(s) found.")

    open_threads = []
    for disc in discussions:
        # Skip individual (non-diff) notes and fully resolved threads
        if disc.get("individual_note"):
            continue
        if disc.get("resolved"):
            continue

        notes = disc.get("notes", [])
        if not notes:
            continue

        first_note = notes[0]
        position = first_note.get("position")
        if not position:
            # Not a diff note (e.g. a general MR comment)
            continue

        # Strip GitLab system notes (e.g. "changed this line in version N of the diff")
        human_notes = [n for n in notes if not n.get("system", False)]
        if not human_notes:
            continue

        # A thread is only truly open if at least one human resolvable note is unresolved.
        # (disc.get("resolved") can be False simply because a system note was added after
        # human notes were resolved, so we re-check here.)
        resolvable = [n for n in human_notes if n.get("resolvable", False)]
        if resolvable and all(n.get("resolved", False) for n in resolvable):
            continue

        file_path = position.get("new_path") or position.get("old_path")
        line_number = position.get("new_line") or position.get("old_line")
        head_sha = position.get("head_sha")
        base_sha = position.get("base_sha")

        context_lines = None
        context_start = None
        if not args.no_context and file_path and line_number and head_sha:
            print(f"  Fetching context for {file_path}:{line_number} …")
            context_lines, context_start = get_file_context(
                base_url,
                encoded_project,
                file_path,
                head_sha,
                line_number,
                args.context,
                args.token,
            )

        open_threads.append(
            {
                "discussion_id": disc["id"],
                "file": file_path,
                "line": line_number,
                "ref": head_sha,
                "base_sha": base_sha,
                "context_lines": context_lines,
                "context_start_line": context_start,
                "notes": [
                    {
                        "author": n["author"]["username"],
                        "created_at": n["created_at"],
                        "body": n["body"],
                        "resolved": n.get("resolved", False),
                    }
                    for n in human_notes
                ],
            }
        )

    print(f"  {len(open_threads)} open thread(s) found.")

    md = build_markdown(mr, open_threads, args.mr_iid)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Written to: {output_file}")


if __name__ == "__main__":
    main()
