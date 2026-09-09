#!/usr/bin/env python3
"""
Local proxy server for DevTodo dashboard.

Serves index.html at http://localhost:8080 and proxies Jira / GitLab API
requests server-side so the browser never hits CORS restrictions.

Endpoints proxied:
  GET /proxy/jira/<path>?<query>   — forwards to Jira with X-Jira-Auth / X-Jira-Base headers
  GET /proxy/gitlab/<path>?<query> — forwards to GitLab with X-Gitlab-Token / X-Gitlab-Base headers
  GET /jira/ticket.md?key=<key>    — downloads a Jira ticket as a Markdown wiki page
  GET /gitlab/mr-comments.md       — downloads open GitLab MR review comments as Markdown
  GET /worktrees?root=<p>&root=... — git worktree status for the given repo roots
  GET /worktrees/scan?parent=<p>   — subdirectories of <p> that look like git checkouts
  GET /activity?days=<n>           — logged worktree activity signals
  POST /worktree/open              — {tool, path}: open a tool in a known worktree
  GET  /worktree/branches?path=<p>  — local/remote branches and the repo's base branch
  POST /worktree/git               — {operation, params}: branch and worktree operations
"""
import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import re
import ssl
import sys

from fetch_mr_comments import build_markdown, get_file_context, paginate, api_get
import jira_downloader
import worktrees
import activity
import spawn
import gitops

PORT = 8080
BIND = "127.0.0.1"
ALLOWED_ORIGINS = {f'http://localhost:{PORT}', f'http://127.0.0.1:{PORT}'}


class ProxyHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        # Keep the terminal clean; only print errors
        pass

    # ── CORS preflight ────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self._cors_headers(200)
        self.end_headers()

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/proxy/jira/'):
            self._proxy_jira(parsed, method='POST')
        elif parsed.path == '/worktree/open':
            self._open_worktree_tool()
        elif parsed.path == '/worktree/git':
            self._worktree_git()
        else:
            self.send_error(404)

    # ── PUT ───────────────────────────────────────────────────────────────────
    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/proxy/jira/'):
            self._proxy_jira(parsed, method='PUT')
        else:
            self.send_error(404)

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ('/', '/index.html'):
            self._serve_file('index.html', 'text/html; charset=utf-8')
        elif path == '/gitlab/mr-comments.md':
            self._download_gitlab_mr_comments(parsed)
        elif path == '/jira/ticket.md':
            self._download_jira_ticket(parsed)
        elif path == '/worktrees':
            self._list_worktrees(parsed)
        elif path == '/worktrees/scan':
            self._scan_repos(parsed)
        elif path == '/activity':
            self._activity_log(parsed)
        elif path == '/worktree/branches':
            self._worktree_branches(parsed)
        elif path.startswith('/proxy/jira/'):
            self._proxy_jira(parsed)
        elif path.startswith('/proxy/gitlab/'):
            self._proxy_gitlab(parsed)
        else:
            # Serve other static files from the same directory
            file_path = path.lstrip('/')
            if file_path and os.path.isfile(file_path):
                self._serve_file(file_path)
            else:
                self.send_error(404)

    # ── Origin guard (applies to all proxy routes) ────────────────────────────
    def _check_origin(self):
        origin = self.headers.get('Origin', '').strip()
        referer = self.headers.get('Referer', '').strip()
        # Allow same-origin fetches (no Origin header on same-origin GET) and
        # explicit browser requests from our own page only.
        if not origin and not referer:
            return True  # non-browser client (curl, server health-check)
        if origin and origin not in ALLOWED_ORIGINS:
            self._json_error(403, 'Forbidden: cross-origin request rejected')
            return False
        if not origin and referer:
            # Referer includes path; check prefix
            if not any(referer.startswith(o) for o in ALLOWED_ORIGINS):
                self._json_error(403, 'Forbidden: cross-origin request rejected')
                return False
        return True

    # ── Proxy: Jira ───────────────────────────────────────────────────────────
    def _proxy_jira(self, parsed, method='GET'):
        if not self._check_origin():
            return

        auth = self.headers.get('X-Jira-Auth', '').strip()
        base = self.headers.get('X-Jira-Base', '').strip().rstrip('/')

        if not auth or not base:
            self._json_error(400, 'Missing X-Jira-Auth or X-Jira-Base header')
            return

        upstream_path = parsed.path[len('/proxy/jira'):]  # e.g. /rest/api/3/search
        url = base + upstream_path
        if parsed.query:
            url += '?' + parsed.query

        headers = {'Authorization': auth, 'Accept': 'application/json'}

        body = None
        if method in ('POST', 'PUT'):
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            headers['Content-Type'] = self.headers.get('Content-Type', 'application/json')

        self._forward(url, headers, method=method, body=body)

    # ── Proxy: GitLab ─────────────────────────────────────────────────────────
    def _proxy_gitlab(self, parsed):
        if not self._check_origin():
            return

        token = self.headers.get('X-Gitlab-Token', '').strip()
        base  = self.headers.get('X-Gitlab-Base', '').strip().rstrip('/')

        if not token or not base:
            self._json_error(400, 'Missing X-Gitlab-Token or X-Gitlab-Base header')
            return

        upstream_path = parsed.path[len('/proxy/gitlab'):]  # e.g. /api/v4/merge_requests
        url = base + upstream_path
        if parsed.query:
            url += '?' + parsed.query

        self._forward(url, {'PRIVATE-TOKEN': token, 'Accept': 'application/json'})

    def _download_gitlab_mr_comments(self, parsed):
        if not self._check_origin():
            return

        token = self.headers.get('X-Gitlab-Token', '').strip()
        base = self.headers.get('X-Gitlab-Base', '').strip().rstrip('/')

        if not token or not base:
            self._json_error(400, 'Missing X-Gitlab-Token or X-Gitlab-Base header')
            return

        query = urllib.parse.parse_qs(parsed.query)
        project = (query.get('project') or [''])[0].strip()
        mr_iid = (query.get('mr_iid') or [''])[0].strip()
        context_raw = (query.get('context') or ['4'])[0].strip()
        no_context = (query.get('no_context') or [''])[0].strip().lower() in ('1', 'true', 'yes')

        if not project or not mr_iid:
            self._json_error(400, 'Missing required query parameters: project and mr_iid')
            return

        try:
            context_lines = max(0, int(context_raw))
        except ValueError:
            self._json_error(400, 'Invalid context parameter')
            return

        encoded_project = urllib.parse.quote(project, safe='')

        try:
            mr = api_get(
                f'{base}/api/v4/projects/{encoded_project}/merge_requests/{urllib.parse.quote(mr_iid, safe="")}',
                token,
            )
            discussions = paginate(
                base,
                f'projects/{encoded_project}/merge_requests/{urllib.parse.quote(mr_iid, safe="")}/discussions',
                token,
            )
            open_threads = []
            for disc in discussions:
                if disc.get('individual_note') or disc.get('resolved'):
                    continue

                notes = disc.get('notes', [])
                if not notes:
                    continue

                first_note = notes[0]
                position = first_note.get('position')
                if not position:
                    continue

                human_notes = [note for note in notes if not note.get('system', False)]
                if not human_notes:
                    continue

                resolvable = [note for note in human_notes if note.get('resolvable', False)]
                if resolvable and all(note.get('resolved', False) for note in resolvable):
                    continue

                file_path = position.get('new_path') or position.get('old_path')
                line_number = position.get('new_line') or position.get('old_line')
                head_sha = position.get('head_sha')
                base_sha = position.get('base_sha')

                snippet_lines = None
                snippet_start = None
                if not no_context and file_path and line_number and head_sha:
                    snippet_lines, snippet_start = get_file_context(
                        base,
                        encoded_project,
                        file_path,
                        head_sha,
                        line_number,
                        context_lines,
                        token,
                    )

                open_threads.append(
                    {
                        'discussion_id': disc['id'],
                        'file': file_path,
                        'line': line_number,
                        'ref': head_sha,
                        'base_sha': base_sha,
                        'context_lines': snippet_lines,
                        'context_start_line': snippet_start,
                        'notes': [
                            {
                                'author': note['author']['username'],
                                'created_at': note['created_at'],
                                'body': note['body'],
                                'resolved': note.get('resolved', False),
                            }
                            for note in human_notes
                        ],
                    }
                )

            markdown = build_markdown(mr, open_threads, mr_iid)
            filename = self._safe_download_name(mr_iid, mr.get('references', {}).get('full') or mr.get('title') or 'merge-request')
            data = markdown.encode('utf-8')

            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', f'http://{BIND}:{PORT}')
            self.send_header('Content-Type', 'text/markdown; charset=utf-8')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}.md"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors='replace')
            self._json_error(exc.code, body)
        except Exception as exc:
            self._json_error(502, str(exc))

    def _download_jira_ticket(self, parsed):
        if not self._check_origin():
            return

        auth = self.headers.get('X-Jira-Auth', '').strip()
        base = self.headers.get('X-Jira-Base', '').strip().rstrip('/')

        if not auth or not base:
            self._json_error(400, 'Missing X-Jira-Auth or X-Jira-Base header')
            return

        query = urllib.parse.parse_qs(parsed.query)
        key = (query.get('key') or [''])[0].strip().upper()
        if not re.match(r'^[A-Z][A-Z0-9]*-\d+$', key):
            self._json_error(400, 'Missing or invalid required query parameter: key')
            return

        try:
            issue = jira_downloader.api_get_issue(base, auth, key)
            markdown = jira_downloader.build_markdown(issue, base)
            data = markdown.encode('utf-8')

            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', f'http://{BIND}:{PORT}')
            self.send_header('Content-Type', 'text/markdown; charset=utf-8')
            self.send_header('Content-Disposition', f'attachment; filename="{key}.md"')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors='replace')
            self._json_error(exc.code, body)
        except Exception as exc:
            self._json_error(502, str(exc))

    # ── Worktrees ─────────────────────────────────────────────────────────────
    def _list_worktrees(self, parsed):
        if not self._check_origin():
            return

        roots = urllib.parse.parse_qs(parsed.query).get('root') or []
        try:
            data = worktrees.list_worktrees(roots)
        except Exception as exc:
            self._json_error(500, str(exc))
            return

        # Remember the roots so the activity poller keeps working with no browser attached
        activity.remember_roots(roots)
        self._json_ok(data)

    def _scan_repos(self, parsed):
        if not self._check_origin():
            return

        parent = (urllib.parse.parse_qs(parsed.query).get('parent') or [''])[0].strip()
        if not parent:
            self._json_error(400, 'Missing required query parameter: parent')
            return
        try:
            self._json_ok({'repos': worktrees.scan_for_repos(parent)})
        except Exception as exc:
            self._json_error(500, str(exc))

    def _activity_log(self, parsed):
        if not self._check_origin():
            return

        raw = (urllib.parse.parse_qs(parsed.query).get('days') or ['1'])[0].strip()
        try:
            days = max(0, min(activity.RETENTION_DAYS, int(raw)))
        except ValueError:
            self._json_error(400, 'Invalid days parameter')
            return
        try:
            self._json_ok({'records': activity.read_log(days), 'days': days})
        except Exception as exc:
            self._json_error(500, str(exc))

    # ── Launch a tool in a worktree ───────────────────────────────────────────
    def _require_dashboard_origin(self):
        """
        Stricter than _check_origin: the Origin header must be present and ours.

        Browsers always send Origin on POST, so demanding it costs nothing and keeps this
        endpoint — which starts processes — from being driven by anything but our page.
        """
        origin = self.headers.get('Origin', '').strip()
        if origin not in ALLOWED_ORIGINS:
            self._json_error(403, 'Forbidden: this endpoint only accepts dashboard requests')
            return False
        return True

    def _open_worktree_tool(self):
        if not self._require_dashboard_origin():
            return

        length = int(self.headers.get('Content-Length', 0) or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            self._json_error(400, 'Body must be JSON')
            return

        tool = str(payload.get('tool') or '').strip()
        path = str(payload.get('path') or '').strip()
        if tool not in spawn.TOOLS:
            self._json_error(400, f'Unknown tool: {tool}')
            return
        if not path:
            self._json_error(400, 'Missing path')
            return

        # The path has to be one git itself reports as a worktree of a remembered root.
        # Membership, not a prefix test — a prefix would let any subdirectory through.
        try:
            known = worktrees.list_worktrees(activity.load_roots())
        except Exception as exc:
            self._json_error(500, str(exc))
            return

        allowed = {
            worktrees.path_key(wt['path'])
            for repo in known['repos'] for wt in repo['worktrees']
        }
        if worktrees.path_key(path) not in allowed:
            self._json_error(403, 'Not a known worktree directory')
            return

        try:
            name = spawn.open_in(tool, path)
        except spawn.ToolMissing as exc:
            self._json_error(404, str(exc))
            return
        except ValueError as exc:
            self._json_error(400, str(exc))
            return
        except Exception as exc:
            self._json_error(500, f'Could not launch: {exc}')
            return

        self._json_ok({'ok': True, 'tool': tool, 'name': name, 'path': path})

    def _worktree_branches(self, parsed):
        if not self._check_origin():
            return
        path = (urllib.parse.parse_qs(parsed.query).get('path') or [''])[0].strip()
        if not path:
            self._json_error(400, 'Missing required query parameter: path')
            return
        try:
            self._json_ok(gitops.branches(activity.load_roots(), path))
        except gitops.Refused as exc:
            self._json_error(403, str(exc))
        except Exception as exc:
            self._json_error(500, str(exc))

    def _worktree_git(self):
        # Mutating, so the same strict origin rule as process spawning applies
        if not self._require_dashboard_origin():
            return

        length = int(self.headers.get('Content-Length', 0) or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            self._json_error(400, 'Body must be JSON')
            return

        operation = str(payload.get('operation') or '').strip()
        params = payload.get('params') or {}
        if not isinstance(params, dict):
            self._json_error(400, 'params must be an object')
            return
        if operation not in gitops.OPERATIONS:
            self._json_error(400, f'Unknown operation: {operation}')
            return

        try:
            self._json_ok(gitops.run(operation, activity.load_roots(), params))
        except gitops.Refused as exc:
            # 409: the request was well formed, the worktree just isn't in a fit state
            self._json_error(409, str(exc))
        except gitops.GitFailed as exc:
            self._json_error(422, exc.stderr or 'git failed')
        except Exception as exc:
            self._json_error(500, str(exc))

    # ── HTTP forwarding ───────────────────────────────────────────────────────
    def _forward(self, url, headers, method='GET', body=None):
        # Allow self-signed certs on internal instances (GitLab on-prem etc.)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            req = urllib.request.Request(url, headers=headers, data=body, method=method)
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                data = resp.read()
                self._cors_headers(resp.status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            data = exc.read()
            self._cors_headers(exc.code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            self._json_error(502, str(exc))

    # ── Static file serving ───────────────────────────────────────────────────
    def _serve_file(self, path, content_type=None):
        if content_type is None:
            if path.endswith('.html'):  content_type = 'text/html; charset=utf-8'
            elif path.endswith('.js'):  content_type = 'application/javascript'
            elif path.endswith('.css'): content_type = 'text/css'
            else:                       content_type = 'application/octet-stream'
        try:
            with open(path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self.send_error(404)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _cors_headers(self, status):
        self.send_response(status)
        self.send_header('Access-Control-Allow-Origin', f'http://{BIND}:{PORT}')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'X-Jira-Auth, X-Jira-Base, X-Gitlab-Token, X-Gitlab-Base, Content-Type')

    def _json_ok(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', f'http://{BIND}:{PORT}')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, code, message):
        body = json.dumps({'error': message}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', f'http://{BIND}:{PORT}')
        self.end_headers()
        self.wfile.write(body)

    def _safe_download_name(self, mr_iid, label):
        cleaned = ''.join(ch if ch.isalnum() or ch in ('-', '_') else '-' for ch in str(label))
        cleaned = '-'.join(part for part in cleaned.split('-') if part)[:80]
        return f'mr-{mr_iid}-comments' if not cleaned else f'mr-{mr_iid}-{cleaned}-comments'


if __name__ == '__main__':
    # Always run from the directory that contains index.html
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Threaded: a worktree scan shells out to git and must not block the dashboard
    server = http.server.ThreadingHTTPServer((BIND, PORT), ProxyHandler)

    # Collect worktree activity whether or not the dashboard is open in a browser
    activity.start()
    url = f'http://localhost:{PORT}'
    print(f'DevTodo  →  {url}')
    print('Press Ctrl+C to stop.\n')

    # Open browser
    import threading, webbrowser
    threading.Timer(0.4, webbrowser.open, args=[url]).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
