"""Exercise the installed Docker apps with sample data and a mock clock only."""
import email
import email.policy
import io
import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from PIL import Image
from token_tv.display import STYLES, overview_rows
from token_tv.sample import snapshot
from token_tv.sources import RPC


def check():
    assert os.geteuid() != 0, "service must run as a regular user"
    for command in (["claude", "--version"], ["claude", "auth", "login", "--help"],
                    ["codex", "--version"], ["codex", "login", "--help"], ["token-tv", "--help"]):
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
        print("PASS", " ".join(command), result.stdout.splitlines()[0])

    themes = {"interval": 30, "themes": [{"id": 0, "enabled": True}, {"id": 2, "enabled": False}]}
    photos = {"interval": 20, "files": [{"name": "original.jpg", "enabled": True}]}
    uploads = []
    original_themes, original_photos = json.loads(json.dumps(themes)), json.loads(json.dumps(photos))

    class Clock(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, data):
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/v.json":
                return self.reply({"m": "SD_PRO"})
            if url.path in ("/theme/toggle", "/photo/toggle"):
                collection, key = (themes["themes"], "id") if url.path.startswith("/theme") else (photos["files"], "name")
                for item in collection:
                    if str(item[key]) == query[key][0]:
                        item["enabled"] = query["state"][0] == "1"
            if url.path == "/photo/interval":
                photos["interval"] = int(query["val"][0])
            if url.path == "/theme/interval":
                themes["interval"] = int(query["val"][0])
            self.reply(themes if url.path == "/theme/list" else photos if url.path == "/photo/list" else {"ok": True})

        def do_POST(self):
            assert self.path == "/photo/upload"
            body = self.rfile.read(int(self.headers["Content-Length"]))
            form = email.message_from_bytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body,
                                            policy=email.policy.HTTP)
            part, = form.iter_parts()
            name, image = part.get_filename(), part.get_payload(decode=True)
            assert name in ("tokentv.jpg", "tokentv.gif")
            assert Image.open(io.BytesIO(image)).size == (240, 240)
            if name.endswith("jpg"):
                assert len(image) < 60000
            uploads.append((name, len(image)))
            if not any(item["name"] == name for item in photos["files"]):
                photos["files"].append({"name": name, "enabled": False})
            self.reply({"ok": True})

    clock = ThreadingHTTPServer(("127.0.0.1", 0), Clock)
    thread = threading.Thread(target=clock.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Empty temporary CLI homes ensure this check never uses real logins.
            env = dict(os.environ, CLAUDE_CONFIG_DIR=str(root / "claude"), CODEX_HOME=str(root / "codex"))
            (root / "claude").mkdir()
            (root / "codex").mkdir()
            rpc = RPC(["codex", "app-server"], env)
            try:
                result = rpc.call("initialize", {"clientInfo": {"name": "tokentv_docker_check", "version": "1"},
                                                  "capabilities": {}})
                assert result.get("userAgent"), "Codex app-server did not initialize"
                rpc.process.stdin.write('{"method":"initialized","params":{}}\n')
                rpc.process.stdin.flush()
                assert rpc.call("account/read", {"refreshToken": False}).get("account") is None
                print("PASS codex app-server JSON-RPC initialize/account/read (no login)")
            finally:
                rpc.close()

            subprocess.run(["token-tv", "demo", "--out", str(root / "demo"), "--scale", "1"],
                           env=env, capture_output=True, check=True, timeout=60)
            generated = list((root / "demo").glob("demo-*"))
            assert {path.stem.removeprefix("demo-") for path in generated} == set(STYLES)
            for path in generated:
                assert Image.open(path).size == (240, 240)
            print("PASS token-tv demo renders all faces")

            data = snapshot(time.time())
            sample_path = root / "sample.json"
            sample_path.write_text(json.dumps(data))
            config = {"poll_seconds": 300, "display_style": "digital",
                      "device_url": f"http://127.0.0.1:{clock.server_port}",
                      "accounts": [{"key": key, "alias": data["accounts"][key]["alias"], "provider": provider,
                                    "email": "sample@example.invalid", "snapshot_file": str(sample_path)}
                                   for key, provider in (("claude_a", "claude"), ("codex_a", "codex"), ("codex_b", "codex"))]}
            config_path = root / "sample-config.json"
            config_path.write_text(json.dumps(config))
            args = ["token-tv", "run", "--config", str(config_path)]
            app = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            base = "http://127.0.0.1:8787"
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        with urlopen(base + "/snapshot", timeout=2) as response:
                            current = json.load(response)
                        if current["display"]["status"] == "ok":
                            break
                    except OSError:
                        pass
                    if app.poll() is not None:
                        raise AssertionError("TokenTV exited: " + app.stderr.read().decode())
                    time.sleep(.2)
                else:
                    raise AssertionError("TokenTV did not upload within 30 seconds")
                assert all(row["status"] == "ok" for row in current["accounts"].values())
                assert [row['alias'] for row in overview_rows(current)] == ['CLAUDE A', 'CODEX A', 'CODEX B']
                for path in ("/", "/health", "/frame/0.jpg"):
                    with urlopen(base + path, timeout=3) as response:
                        assert response.status == 200
                        content = response.read()
                        if path.endswith("jpg"):
                            assert Image.open(io.BytesIO(content)).size == (240, 240)
                assert [f["name"] for f in photos["files"] if f["enabled"]] == ["tokentv.jpg"]
                assert [t["id"] for t in themes["themes"] if t["enabled"]] == [2]
                print("PASS HTTP dashboard/health/frame and JPEG upload + photo activation")

                def apply(style):
                    request = Request(base + "/display/style", data=json.dumps({"style": style}).encode(),
                                      headers={"Content-Type": "application/json"})
                    with urlopen(request, timeout=3) as response:
                        assert response.status == 200
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        with urlopen(base + "/display", timeout=3) as response:
                            state = json.load(response)
                        if state["status"] == "ok" and state["applied_style"] == style:
                            return
                        time.sleep(.1)
                    raise AssertionError("Style was not applied")

                before = len(uploads)
                apply("digital")
                assert len(uploads) == before, "identical image was uploaded again"
                apply("space")
                assert uploads[-1][0] == "tokentv.gif"
                assert [f["name"] for f in photos["files"] if f["enabled"]] == ["tokentv.gif"]
                print("PASS duplicate upload suppression and animated GIF upload/activation")
            finally:
                app.terminate()
                try:
                    app.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    app.kill()
                    app.wait()
                app.stderr.close()
            subprocess.run(args + ["--restore-display"], env=env, capture_output=True, check=True, timeout=20)
            assert themes == original_themes
            assert photos["interval"] == original_photos["interval"]
            assert [f for f in photos["files"] if f["enabled"]] == original_photos["files"]
            print("PASS restore preserves original photos/theme/intervals")
            print("All Docker smoke checks passed; no real credentials or clock used.")
    finally:
        clock.shutdown()
        clock.server_close()
        thread.join()


if __name__ == "__main__":
    check()
