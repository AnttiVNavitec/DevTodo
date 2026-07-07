#!/usr/bin/env python3
"""
Jira Ticket Downloader
======================
Downloads a Jira ticket and saves it as a local Markdown wiki page.

Usage:
    python jira_downloader.py PROJ-123
    python jira_downloader.py PROJ-123 --config /path/to/config.json
    python jira_downloader.py PROJ-123 --output /my/wiki/PROJ-123.md

Configuration (config.json):
    jira_url   – Base URL of your Jira instance, e.g. https://company.atlassian.net
    email      – Your Atlassian account email
    api_token  – API token from https://id.atlassian.com/manage-profile/security/api-tokens
    wiki_path  – Local folder where Markdown pages are saved
"""

import argparse
import base64
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = Path(__file__).parent / "config.json"


def load_config(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: Config file not found: {path}")
        print("Copy config.example.json to config.json and fill in your details.")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    required = ("jira_url", "email", "api_token", "wiki_path")
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        print(f"ERROR: Missing config keys: {', '.join(missing)}")
        sys.exit(1)
    return cfg


# ---------------------------------------------------------------------------
# Jira REST API
# ---------------------------------------------------------------------------

def api_get_issue(base_url: str, auth_header: str, issue_key: str) -> dict:
    """Fetch a Jira issue (all fields) using a pre-built Authorization header value."""
    query = urllib.parse.urlencode({"fields": "*all"})
    url = f"{base_url.rstrip('/')}/rest/api/3/issue/{issue_key}?{query}"
    req = urllib.request.Request(
        url, headers={"Authorization": auth_header, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_issue(config: dict, issue_key: str) -> dict:
    auth_header = "Basic " + base64.b64encode(
        f"{config['email']}:{config['api_token']}".encode()
    ).decode()
    try:
        return api_get_issue(config["jira_url"], auth_header, issue_key)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print("ERROR: Authentication failed – check email and api_token in config.json")
        elif exc.code == 404:
            print(f"ERROR: Issue {issue_key} not found on {config['jira_url']}")
        else:
            print(f"ERROR: {exc.code} – {exc.read().decode(errors='replace')}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Atlassian Document Format (ADF) → Markdown converter
# ---------------------------------------------------------------------------

def adf_to_md(node, _list_depth: int = 0) -> str:
    """Recursively convert an ADF node (dict) to Markdown text."""
    if not node:
        return ""

    t = node.get("type", "")
    children = node.get("content", [])
    text = node.get("text", "")

    # ---- inline nodes -------------------------------------------------------
    if t == "text":
        marks = {m["type"]: m.get("attrs", {}) for m in node.get("marks", [])}
        result = text
        if "code" in marks:
            result = f"`{result}`"
        if "strong" in marks:
            result = f"**{result}**"
        if "em" in marks:
            result = f"*{result}*"
        if "strike" in marks:
            result = f"~~{result}~~"
        if "underline" in marks:
            result = f"<u>{result}</u>"
        if "link" in marks:
            href = marks["link"].get("href", "")
            result = f"[{result}]({href})"
        if "subsup" in marks:
            tag = "sup" if marks["subsup"].get("type") == "sup" else "sub"
            result = f"<{tag}>{result}</{tag}>"
        return result

    if t == "hardBreak":
        return "  \n"

    if t == "mention":
        name = node.get("attrs", {}).get("text", node.get("attrs", {}).get("id", "?"))
        return f"@{name.lstrip('@')}"

    if t in ("inlineCard", "blockCard", "embedCard"):
        url = node.get("attrs", {}).get("url", "")
        return f"[{url}]({url})"

    if t == "emoji":
        return node.get("attrs", {}).get("text", "")

    if t == "date":
        ts = node.get("attrs", {}).get("timestamp", "")
        try:
            return datetime.utcfromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
        except Exception:
            return ts

    # ---- block nodes --------------------------------------------------------
    if t == "doc":
        parts = [adf_to_md(c) for c in children]
        return "\n\n".join(p for p in parts if p.strip())

    if t == "paragraph":
        inner = "".join(adf_to_md(c) for c in children)
        return inner

    if t == "heading":
        level = node.get("attrs", {}).get("level", 1)
        inner = "".join(adf_to_md(c) for c in children)
        # Offset by 2 so top-level h1 in description becomes h3 under page h2
        hashes = "#" * min(level + 2, 6)
        return f"{hashes} {inner}"

    if t == "rule":
        return "---"

    if t == "blockquote":
        parts = [adf_to_md(c) for c in children]
        inner = "\n\n".join(p for p in parts if p)
        return "\n".join(f"> {line}" for line in inner.splitlines())

    if t == "codeBlock":
        lang = node.get("attrs", {}).get("language", "")
        inner = "".join(adf_to_md(c) for c in children)
        return f"```{lang}\n{inner}\n```"

    if t == "bulletList":
        items = []
        for item in children:
            item_md = _render_list_item(item, _list_depth)
            items.append(f"{'  ' * _list_depth}- {item_md}")
        return "\n".join(items)

    if t == "orderedList":
        items = []
        start = node.get("attrs", {}).get("order", 1)
        for i, item in enumerate(children, start):
            item_md = _render_list_item(item, _list_depth)
            items.append(f"{'  ' * _list_depth}{i}. {item_md}")
        return "\n".join(items)

    if t == "panel":
        panel_type = node.get("attrs", {}).get("panelType", "info").upper()
        parts = [adf_to_md(c) for c in children]
        inner = "\n\n".join(p for p in parts if p)
        header = f"> **{panel_type}**"
        body = "\n".join(f"> {line}" for line in inner.splitlines())
        return f"{header}\n{body}"

    if t == "table":
        return _render_table(node)

    if t == "mediaSingle" or t == "media":
        alt = node.get("attrs", {}).get("alt", "image")
        url = node.get("attrs", {}).get("url", "")
        if url:
            return f"![{alt}]({url})"
        return f"*[{alt}]*"

    if t == "expand":
        title = node.get("attrs", {}).get("title", "Details")
        parts = [adf_to_md(c) for c in children]
        inner = "\n\n".join(p for p in parts if p)
        return f"<details><summary>{title}</summary>\n\n{inner}\n\n</details>"

    # Fallback: recurse into children
    parts = [adf_to_md(c) for c in children]
    return "".join(parts) + text


def _render_list_item(node, depth: int) -> str:
    """Render a listItem node, supporting nested lists."""
    parts = []
    for child in node.get("content", []):
        if child.get("type") in ("bulletList", "orderedList"):
            nested = adf_to_md(child, _list_depth=depth + 1)
            parts.append("\n" + nested)
        else:
            parts.append(adf_to_md(child))
    return "".join(parts).strip()


def _render_table(node) -> str:
    rows_data = []
    for row in node.get("content", []):
        if row.get("type") != "tableRow":
            continue
        cells = []
        for cell in row.get("content", []):
            cell_parts = [adf_to_md(c) for c in cell.get("content", [])]
            cells.append(" ".join(p.strip() for p in cell_parts if p.strip()))
        rows_data.append(cells)

    if not rows_data:
        return ""

    col_count = max(len(r) for r in rows_data)
    # Pad rows
    rows_data = [r + [""] * (col_count - len(r)) for r in rows_data]

    lines = []
    lines.append("| " + " | ".join(rows_data[0]) + " |")
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in rows_data[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def body_to_md(body) -> str:
    """Convert a Jira issue body field (ADF dict or legacy string) to Markdown."""
    if not body:
        return "_No content._"
    if isinstance(body, dict):
        result = adf_to_md(body)
        return result.strip() or "_No content._"
    # Jira Server / plain text / wiki markup – return as-is
    return str(body).strip() or "_No content._"


# ---------------------------------------------------------------------------
# Markdown page builder
# ---------------------------------------------------------------------------

def _fmt_date(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso


def _wiki_link(key: str) -> str:
    """Return a relative Markdown link pointing to the local wiki page for key."""
    return f"[{key}]({key}.md)"


def build_markdown(issue: dict, jira_base: str) -> str:
    fields = issue["fields"]
    key = issue["key"]
    jira_base = jira_base.rstrip("/")

    summary      = fields.get("summary") or "No Summary"
    issue_type   = (fields.get("issuetype") or {}).get("name", "Issue")
    status       = (fields.get("status") or {}).get("name", "Unknown")
    priority_obj = fields.get("priority")
    priority     = priority_obj.get("name", "None") if priority_obj else "None"
    assignee_obj = fields.get("assignee")
    assignee     = assignee_obj.get("displayName", "Unassigned") if assignee_obj else "Unassigned"
    reporter_obj = fields.get("reporter")
    reporter     = reporter_obj.get("displayName", "Unknown") if reporter_obj else "Unknown"
    created      = _fmt_date(fields.get("created", ""))
    updated      = _fmt_date(fields.get("updated", ""))
    labels       = fields.get("labels") or []
    components   = [c["name"] for c in (fields.get("components") or [])]
    fix_versions = [v["name"] for v in (fields.get("fixVersions") or [])]

    out = []

    # ------------------------------------------------------------------
    # Title
    # ------------------------------------------------------------------
    out.append(f"# [{key}] {summary}")
    out.append("")

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    out.append(f"| Field | Value |")
    out.append(f"|---|---|")
    out.append(f"| **Jira** | [{key}]({jira_base}/browse/{key}) |")
    out.append(f"| **Type** | {issue_type} |")
    out.append(f"| **Status** | {status} |")
    out.append(f"| **Priority** | {priority} |")
    out.append(f"| **Assignee** | {assignee} |")
    out.append(f"| **Reporter** | {reporter} |")
    out.append(f"| **Created** | {created} |")
    out.append(f"| **Updated** | {updated} |")
    if labels:
        out.append(f"| **Labels** | {', '.join(labels)} |")
    if components:
        out.append(f"| **Components** | {', '.join(components)} |")
    if fix_versions:
        out.append(f"| **Fix Versions** | {', '.join(fix_versions)} |")

    out.append("")
    out.append("---")
    out.append("")

    # ------------------------------------------------------------------
    # Parent
    # ------------------------------------------------------------------
    parent = fields.get("parent")
    if parent:
        p_key     = parent.get("key", "")
        p_summary = (parent.get("fields") or {}).get("summary", "")
        p_type    = ((parent.get("fields") or {}).get("issuetype") or {}).get("name", "Parent")
        out.append(f"**{p_type}:** {_wiki_link(p_key)} — {p_summary}")
        out.append("")
        out.append("---")
        out.append("")

    # ------------------------------------------------------------------
    # Description
    # ------------------------------------------------------------------
    out.append("## Description")
    out.append("")
    out.append(body_to_md(fields.get("description")))
    out.append("")
    out.append("---")
    out.append("")

    # ------------------------------------------------------------------
    # Linked Issues
    # ------------------------------------------------------------------
    links = fields.get("issuelinks") or []
    if links:
        out.append("## Linked Issues")
        out.append("")
        for link in links:
            link_type_obj = link.get("type") or {}
            if "outwardIssue" in link:
                direction  = link_type_obj.get("outward", "links to")
                linked_obj = link["outwardIssue"]
            elif "inwardIssue" in link:
                direction  = link_type_obj.get("inward", "linked from")
                linked_obj = link["inwardIssue"]
            else:
                continue

            lk          = linked_obj.get("key", "")
            l_summary   = (linked_obj.get("fields") or {}).get("summary", "")
            l_status    = ((linked_obj.get("fields") or {}).get("status") or {}).get("name", "")
            out.append(f"- **{direction}:** {_wiki_link(lk)} — {l_summary} *(Status: {l_status})*")

        out.append("")
        out.append("---")
        out.append("")

    # ------------------------------------------------------------------
    # Sub-tasks
    # ------------------------------------------------------------------
    subtasks = fields.get("subtasks") or []
    if subtasks:
        out.append("## Sub-tasks")
        out.append("")
        for sub in subtasks:
            s_key     = sub.get("key", "")
            s_summary = (sub.get("fields") or {}).get("summary", "")
            s_status  = ((sub.get("fields") or {}).get("status") or {}).get("name", "")
            out.append(f"- {_wiki_link(s_key)} — {s_summary} *(Status: {s_status})*")

        out.append("")
        out.append("---")
        out.append("")

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------
    comments_data = fields.get("comment") or {}
    comments = comments_data.get("comments", []) if isinstance(comments_data, dict) else []

    if comments:
        out.append("## Comments")
        out.append("")
        for i, comment in enumerate(comments, 1):
            author      = (comment.get("author") or {}).get("displayName", "Unknown")
            c_created   = _fmt_date(comment.get("created", ""))
            c_updated   = _fmt_date(comment.get("updated", ""))
            body        = comment.get("body", "")

            out.append(f"### Comment {i} — {author} ({c_created})")
            if c_updated and c_updated != c_created:
                out.append(f"*Edited: {c_updated}*")
            out.append("")
            out.append(body_to_md(body))
            out.append("")

        out.append("---")
        out.append("")

    # ------------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------------
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    out.append(f"*Page generated by jira_downloader.py on {now}*")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Download a Jira ticket and save it as a local Markdown wiki page."
    )
    parser.add_argument("ticket", help="Jira issue key, e.g. PROJ-123")
    parser.add_argument(
        "--config", "-c",
        default=str(DEFAULT_CONFIG),
        help=f"Path to JSON config file (default: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path (overrides wiki_path from config)",
    )
    args = parser.parse_args()

    ticket = args.ticket.upper().strip()
    config = load_config(Path(args.config))

    print(f"Fetching {ticket} from {config['jira_url']} …")
    issue = fetch_issue(config, ticket)

    markdown = build_markdown(issue, config["jira_url"])

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = Path(config["wiki_path"]) / f"{ticket}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")

    print(f"Saved → {out_path}")


if __name__ == "__main__":
    main()
