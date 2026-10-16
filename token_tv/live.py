"""Live account dashboard and stock-firmware image publisher."""
import argparse
import hashlib
import ipaddress
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from token_tv.device import FILES, BackupMismatch, open_display
from token_tv.catalog import payload as theme_payload
from token_tv.display import STYLES, render_page, watch_payload
from token_tv.web_assets import HTML, ASSETS, asset
from token_tv.sources import load_config
from token_tv.state import UsageStore
from token_tv.web_auth import LoginManager
from token_tv.history import UsageHistory


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_original(device, backup_path):
    """Return the clock's original state; save it first only if no backup exists yet."""
    if backup_path.is_file():
        original = json.loads(backup_path.read_text())
        device.check_backup(original)  # a backup from another kind of clock is kept and nothing is sent
        return original
    original = device.capture()
    write_json(backup_path, original)
    return original


def mismatch_message(error, backup_path):
    return (f"{error} Nothing was changed on the clock and {backup_path} was kept. If the clock was offline "
            "when TokenTV started, start TokenTV again. If you replaced the clock, put the old one back to "
            "restore it, or move that file away so TokenTV saves a new backup.")


class DisplayPreferences:
    def __init__(self, config_path, config):
        self.path = Path(config_path)
        self.config = dict(config)
        self.style = config.get('display_style', 'pixel')
        self.applied_style = None
        self.status = 'queued' if config.get('device_url') else 'preview_only'
        self.lock = threading.Lock()
        self.changed = threading.Event()

    def snapshot(self):
        with self.lock:
            return {'style': self.style, 'applied_style': self.applied_style,
                    'status': self.status, 'styles': list(STYLES)}

    def set_style(self, style):
        if style not in STYLES:
            raise ValueError('Unknown display style')
        with self.lock:
            updated = dict(load_config(self.path), display_style=style)
            write_json(self.path, updated)
            self.config = updated
            self.style = style
            self.status = 'queued' if self.config.get('device_url') else 'preview_only'
        self.changed.set()

    def delivered(self, style, success):
        with self.lock:
            if success:
                self.applied_style = style
            self.status = ('queued' if style != self.style else 'ok' if success else 'error')


def handler(store, preferences=None, logins=None, history=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path == '/history':
                if history is None:
                    self.reply(503, b'{"error":"history_unavailable"}')
                    return
                query = parse_qs(url.query, keep_blank_values=True)
                if set(query) - {'account', 'start', 'end'} or any(len(v) != 1 for v in query.values()):
                    self.reply(400, b'{"error":"invalid_history_query"}')
                    return
                try:
                    data = history.query(key=query.get('account', [None])[0],
                                         start=query.get('start', [None])[0], end=query.get('end', [None])[0])
                except (ValueError, OverflowError):
                    self.reply(400, b'{"error":"invalid_history_range"}')
                    return
                except KeyError:
                    self.reply(404, b'{"error":"unknown_history_account"}')
                    return
                except (sqlite3.Error, OSError):
                    self.reply(503, b'{"error":"history_unavailable"}')
                    return
                self.reply(200, json.dumps(data, ensure_ascii=False).encode())
                return
            if path.startswith('/auth/'):
                if not self.auth_origin():
                    return
                key = path.removeprefix('/auth/')
                try:
                    if logins is None:
                        raise KeyError(key)
                    data = logins.snapshot(key)
                except KeyError:
                    self.send_error(404, 'Unknown login account')
                    return
                self.reply(200, json.dumps(data).encode())
                return
            snapshot = store.snapshot()
            if logins:
                for key, row in snapshot['accounts'].items():
                    row['login_available'] = logins.available(key)
            display = preferences.snapshot() if preferences else {'style': 'pixel', 'status': 'preview_only', 'styles': list(STYLES)}
            snapshot['display'] = display
            content_type = "application/json; charset=utf-8"
            if path == "/":
                body = HTML.encode()
                content_type = "text/html; charset=utf-8"
            elif path in ASSETS:
                body, content_type = asset(path)
            elif path == "/snapshot":
                body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
            elif path.startswith("/snapshot/") and path[10:] in snapshot["accounts"]:
                body = json.dumps(snapshot["accounts"][path[10:]], ensure_ascii=False, separators=(",", ":")).encode()
            elif path in ("/frame/0.jpg", "/frame/1.jpg"):
                style = parse_qs(url.query).get('style', [display['style']])[0]
                if style not in STYLES:
                    self.send_error(400, 'Unknown display style')
                    return
                body = render_page(snapshot, int(path[7]), style)
                content_type = "image/gif" if body[:4] == b"GIF8" else "image/jpeg"
            elif path == '/themes':
                body = json.dumps(theme_payload()).encode()
            elif path == '/display':
                body = json.dumps(display).encode()
            elif path == "/watch.json":
                body = json.dumps(watch_payload(snapshot), ensure_ascii=False, separators=(",", ":")).encode()
            elif path == "/health":
                body = json.dumps({"status": "ok", "updated_at": snapshot["updated_at"]}).encode()
            else:
                self.send_error(404)
                return
            self.reply(200, body, content_type)

        def reply(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if urlparse(self.path).path.startswith('/auth/'):
                self.auth_post()
                return
            if urlparse(self.path).path != '/display/style':
                self.send_error(404)
                return
            if preferences is None:
                self.send_error(409, 'Display settings unavailable')
                return
            origin = self.headers.get('Origin')
            if origin and (urlparse(origin).scheme not in ('http', 'https') or urlparse(origin).netloc != self.headers.get('Host')):
                self.send_error(403, 'Origin mismatch')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 1024 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('Invalid JSON request')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict) or set(data) != {'style'} or data['style'] not in STYLES:
                    raise ValueError('Unknown display style')
                preferences.set_style(data['style'])
            except (ValueError, UnicodeError):
                self.send_error(400, 'Choose a supported display style')
                return
            except OSError:
                self.send_error(500, 'Could not save display choice')
                return
            self.reply(200, json.dumps(preferences.snapshot()).encode())

        def auth_origin(self):
            # An unauthenticated LAN UI still rejects foreign browser origins and DNS rebinding.
            host = self.headers.get('Host', '')
            try:
                hostname = urlparse('http://' + host).hostname or ''
                local = hostname in {'localhost', *filter(None, os.environ.get('TOKEN_TV_AUTH_HOSTS', '').split(','))}
                local = local or hostname.endswith(('.local', '.lan'))
                try:
                    address = ipaddress.ip_address(hostname)
                    local = local or address.is_private or address.is_loopback or address.is_link_local
                except ValueError:
                    pass
                origin = self.headers.get('Origin')
                if not local or (origin and (urlparse(origin).scheme not in ('http', 'https') or urlparse(origin).netloc != host)):
                    raise ValueError()
                if self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
                    raise ValueError()
            except ValueError:
                self.send_error(403, 'Use the dashboard on this server')
                return False
            return True

        def auth_post(self):
            if not self.auth_origin():
                return
            parts = urlparse(self.path).path.split('/')
            if logins is None or len(parts) != 4 or parts[3] not in ('start', 'code', 'cancel'):
                self.send_error(404)
                return
            key, action = parts[2:]
            try:
                logins.snapshot(key)
            except KeyError:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if (not 0 < length <= 4096 or self.headers.get('Transfer-Encoding')
                        or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'
                        or self.headers.get('X-TokenTV-Request') != '1'):
                    raise ValueError('잘못된 로그인 요청입니다.')
                self.connection.settimeout(10)
                data = json.loads(self.rfile.read(length))
                expected = {'start': set(), 'cancel': {'id'}, 'code': {'id', 'code'}}[action]
                if not isinstance(data, dict) or set(data) != expected:
                    raise ValueError('잘못된 로그인 요청입니다.')
                if action == 'start':
                    result = logins.start(key)
                elif action == 'cancel':
                    result = logins.cancel(key, data['id'])
                else:
                    result = logins.submit(key, data['id'], data['code'])
            except (ValueError, UnicodeError, OSError):
                # Do not echo JSON, codes, URLs, subprocess output or exception text.
                self.reply(400, json.dumps({'message': '요청을 처리할 수 없습니다. 로그인 상태를 확인하고 다시 시도해 주세요.'}).encode())
                return
            self.reply(200, json.dumps(result).encode())
    return Handler


def main():
    parser = argparse.ArgumentParser(description="TokenTV live account display")
    parser.add_argument("--config", required=True, help="Credential-free account metadata")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--output", help="Write a normalized snapshot rather than stdout")
    parser.add_argument("--state-dir", default=".runtime")
    parser.add_argument("--restore-display", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if config.get("font"):
        os.environ["TOKEN_TV_FONT"] = config["font"]
    state_dir = Path(args.state_dir)
    device = open_display(config["device_url"]) if config.get("device_url") else None
    backup_path = state_dir / "display-original.json"
    if args.restore_display:
        if not device or not backup_path.is_file():
            parser.error("A display and its original state backup are required")
        original = json.loads(backup_path.read_text())
        try:
            device.check_backup(original)
        except BackupMismatch as error:
            raise SystemExit(mismatch_message(error, backup_path))
        device.restore(original)
        print("Original display selection restored.")
        return
    store = UsageStore(config["accounts"])
    preferences = DisplayPreferences(args.config, config)
    interval = max(60, int(config.get("poll_seconds", 300)))
    try:
        history = UsageHistory(state_dir / 'usage-history.sqlite3', config['accounts'], interval)
    except (sqlite3.Error, OSError, ValueError):
        history = None
        print('TokenTV history unavailable; live readings remain enabled.', flush=True)
    stopping = threading.Event()
    refresh_requested = threading.Event()

    def logged_in():
        refresh_requested.set()
        preferences.changed.set()

    logins = LoginManager(config['accounts'], on_success=logged_in)
    original = None
    active_file = None
    last_upload = None

    def cycle(refresh=True):
        nonlocal original, active_file, last_upload
        if refresh:
            store.refresh(skip_keys=logins.active_keys())
        snapshot = store.snapshot()
        if refresh and history:
            try:
                history.record(snapshot)
            except (sqlite3.Error, OSError):
                print('TokenTV history write failed; live readings remain enabled.', flush=True)
        style = preferences.snapshot()['style']
        if not args.once and device:
            receipts = []
            phase = "backup"
            try:
                if original is None:
                    original = load_original(device, backup_path)
                phase = "upload"
                image = render_page(snapshot, 0, style)
                name = FILES[1] if image[:4] == b"GIF8" else FILES[0]
                digest = hashlib.sha256(image).hexdigest()
                if (name, digest) != last_upload:  # identical frames are not rewritten to flash
                    (state_dir / name).write_bytes(image)
                    receipts.append(dict(device.upload(name, image), sha256=digest))
                    last_upload = (name, digest)
                if active_file != name:
                    phase = "activate"
                    device.activate(original, name)
                    active_file = name
                write_json(state_dir / "display-receipt.json", {"at": int(time.time()), "style": style, "uploads": receipts})
                preferences.delivered(style, True)
            except (OSError, ValueError, KeyError) as error:
                if isinstance(error, BackupMismatch):
                    print(mismatch_message(error, backup_path), flush=True)
                write_json(state_dir / "display-receipt.json", {"at": int(time.time()), "style": style, "status": "error", "phase": phase,
                           "uploads": receipts, "http_status": getattr(error, "code", None)})
                preferences.delivered(style, False)
        snapshot['display'] = preferences.snapshot()
        write_json(state_dir / "snapshot.json", snapshot)
        if args.output:
            write_json(args.output, snapshot)

    if args.once:
        cycle()
        if not args.output:
            print(json.dumps(store.snapshot(), ensure_ascii=False, separators=(",", ":")))
        return

    def poll():
        next_refresh = 0
        while not stopping.is_set():
            preferences.changed.clear()
            requested = refresh_requested.is_set()
            if requested:
                refresh_requested.clear()
            refresh = requested or time.monotonic() >= next_refresh
            try:
                cycle(refresh)
            except Exception:
                # Never serialize exception text, which may include auth material.
                print("TokenTV poll failed; retained previous snapshot.", flush=True)
            if refresh:
                next_refresh = time.monotonic() + interval
            preferences.changed.wait(max(0, next_refresh - time.monotonic()))

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        with ThreadingHTTPServer((args.host, args.port), handler(store, preferences, logins, history)) as server:
            print(f"TokenTV live listening at http://{args.host}:{args.port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        logins.close()
        stopping.set()
        preferences.changed.set()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
