#!/usr/bin/env python3
"""
Local proxy server for DevTodo dashboard.

Serves index.html at http://localhost:8080 and proxies Jira / GitLab API
requests server-side so the browser never hits CORS restrictions.

Endpoints proxied:
  GET /proxy/jira/<path>?<query>   — forwards to Jira with X-Jira-Auth / X-Jira-Base headers
  GET /proxy/gitlab/<path>?<query> — forwards to GitLab with X-Gitlab-Token / X-Gitlab-Base headers
"""
import http.server
import urllib.request
import urllib.error
import urllib.parse
import json
import os
import ssl
import sys

from fetch_mr_comments import build_markdown, get_file_context, paginate, api_get

PORT = 8080
BIND = "127.0.0.1"


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

    # ── Proxy: Jira ───────────────────────────────────────────────────────────
    def _proxy_jira(self, parsed, method='GET'):
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
        if method == 'POST':
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length) if length else b''
            headers['Content-Type'] = self.headers.get('Content-Type', 'application/json')

        self._forward(url, headers, method=method, body=body)

    # ── Proxy: GitLab ─────────────────────────────────────────────────────────
    def _proxy_gitlab(self, parsed):
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
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'X-Jira-Auth, X-Jira-Base, X-Gitlab-Token, X-Gitlab-Base, Content-Type')

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

    server = http.server.HTTPServer((BIND, PORT), ProxyHandler)
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
