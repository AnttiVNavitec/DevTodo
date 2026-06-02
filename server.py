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

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ('/', '/index.html'):
            self._serve_file('index.html', 'text/html; charset=utf-8')
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
    def _proxy_jira(self, parsed):
        auth = self.headers.get('X-Jira-Auth', '').strip()
        base = self.headers.get('X-Jira-Base', '').strip().rstrip('/')

        if not auth or not base:
            self._json_error(400, 'Missing X-Jira-Auth or X-Jira-Base header')
            return

        upstream_path = parsed.path[len('/proxy/jira'):]  # e.g. /rest/api/3/search
        url = base + upstream_path
        if parsed.query:
            url += '?' + parsed.query

        self._forward(url, {'Authorization': auth, 'Accept': 'application/json'})

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

    # ── HTTP forwarding ───────────────────────────────────────────────────────
    def _forward(self, url, headers):
        # Allow self-signed certs on internal instances (GitLab on-prem etc.)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        try:
            req = urllib.request.Request(url, headers=headers)
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
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'X-Jira-Auth, X-Jira-Base, X-Gitlab-Token, X-Gitlab-Base')

    def _json_error(self, code, message):
        body = json.dumps({'error': message}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', f'http://{BIND}:{PORT}')
        self.end_headers()
        self.wfile.write(body)


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
