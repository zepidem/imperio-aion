# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only overlay service, bound exclusively to IPv4 loopback.

Worker threads only read immutable serialized views. They never call Tk or the
collector. Each source has its own revocable capability, including its assets.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json
import re
import secrets
import threading

ROOT = Path(__file__).resolve().parent
KINDS = ('hud', 'painel')
TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}', re.ASCII)


def valid_token(value):
    return isinstance(value, str) and TOKEN_PATTERN.fullmatch(value) is not None


def token_matches(candidate, expected):
    # Validate both inputs before compare_digest: str Unicode is unsupported.
    # Public fixed length/encoding checks do not disclose capability contents.
    return (valid_token(candidate) and valid_token(expected)
            and secrets.compare_digest(candidate, expected))


class OverlayServer:
    def __init__(self, port=38472, tokens=None):
        self.port = port if type(port) is int and 1024 <= port <= 65535 else 38472
        saved_tokens = tokens if isinstance(tokens, dict) else {}
        self.tokens = {kind: saved_tokens.get(kind) for kind in KINDS}
        for kind in KINDS:
            if not valid_token(self.tokens[kind]):
                self.tokens[kind] = secrets.token_urlsafe(32)
        self.enabled = {kind: False for kind in KINDS}
        self.payloads = {kind: b'{}' for kind in KINDS}
        self.assets = {}
        self.httpd = None
        self.thread = None
        self.lock = threading.RLock()
        self.html = (ROOT / 'assets/streaming/overlay.html').read_bytes()

    def settings(self):
        with self.lock:
            return dict(port=self.port, tokens=dict(self.tokens))

    def url(self, kind):
        with self.lock:
            return f'http://127.0.0.1:{self.port}/overlay/{kind}/{self.tokens[kind]}/' if self.enabled[kind] else ''

    def activate(self, kind, enabled):
        if kind not in KINDS:
            raise ValueError('Sobreposição desconhecida.')
        if enabled:
            self.start()
        with self.lock:
            self.enabled[kind] = bool(enabled)
            any_enabled = any(self.enabled.values())
        if not any_enabled:
            self.stop()

    def rotate(self, kind):
        with self.lock:
            self.tokens[kind] = secrets.token_urlsafe(32)
        return self.url(kind)

    def publish(self, views, assets):
        # No raw snapshot, session exports, connection identifiers or callbacks.
        payloads = {kind: json.dumps(views.get(kind, {}), ensure_ascii=False,
            allow_nan=False, separators=(',', ':')).encode('utf-8') for kind in KINDS}
        safe_assets = {}
        for key, path in assets.items():
            path = Path(path).resolve()
            if path.is_relative_to((ROOT / 'assets').resolve()) and path.suffix.lower() in ('.png', '.webp') and path.is_file():
                safe_assets[key] = path
        with self.lock:
            self.payloads = payloads
            self.assets = safe_assets

    def start(self):
        if self.httpd is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def log_message(self, *args):
                pass  # Capability URLs and player data must never become logs.

            def reply(self, code, body=b'', mime='text/plain; charset=utf-8', cache='no-store'):
                self.send_response(code)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', cache)
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                if self.command != 'HEAD':
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError, TimeoutError):
                        pass

            def do_GET(self):
                # Reject hostile Host/Origin headers, including DNS rebinding.
                expected = f'127.0.0.1:{owner.port}'
                if self.headers.get('Host') != expected or self.headers.get('Origin') not in (None, 'http://' + expected):
                    return self.reply(403)
                parts = urlsplit(self.path).path.split('/')
                if len(parts) < 5 or parts[1] != 'overlay' or parts[2] not in KINDS:
                    return self.reply(404)
                kind, token = parts[2:4]
                with owner.lock:
                    if not owner.enabled[kind] or not token_matches(token, owner.tokens[kind]):
                        return self.reply(403)
                    route = '/'.join(parts[4:])
                    if not route:
                        body, mime = owner.html, 'text/html; charset=utf-8'
                    elif route == 'state':
                        body, mime = owner.payloads[kind], 'application/json; charset=utf-8'
                    elif route.startswith('asset/') and route[6:] in owner.assets:
                        path = owner.assets[route[6:]]
                        body = path.read_bytes()
                        mime = 'image/png' if path.suffix.lower() == '.png' else 'image/webp'
                    else:
                        return self.reply(404)
                # Artwork has no player data. Keep icons cached across combat
                # refreshes; HTML and live state must always be revalidated.
                self.reply(200, body, mime, 'private, max-age=86400, immutable' if route.startswith('asset/') else 'no-store')

            do_HEAD = do_GET

            def do_POST(self):
                self.reply(405)

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = False

        try:
            self.httpd = Server(('127.0.0.1', self.port), Handler)
        except OSError:
            # Another application can occupy the preferred port. Never bind LAN.
            self.httpd = Server(('127.0.0.1', 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
            kwargs={'poll_interval': .1}, daemon=True, name='AionOverlayLocal')
        self.thread.start()

    def stop(self):
        with self.lock:
            self.enabled = {kind: False for kind in KINDS}
        server, self.httpd = self.httpd, None
        if server:
            server.shutdown()
            server.server_close()
        if self.thread:
            self.thread.join(timeout=1)
            self.thread = None
