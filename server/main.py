"""Engram HTTP server — stdlib only (no pip installs), serves the web UI and
the JSON API. Run:  python3 -m server.main  →  http://localhost:8787
"""
import json
import mimetypes
import os
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from . import config, benchmark
from .agent import run_turn, new_session
from .engine import Engine
from .llm import LLM
from .store import Store

STORE = Store(config.DATA_DIR)
LLM_CLIENT = LLM(runlog=STORE.runlog)
ENGINE = Engine(STORE, LLM_CLIENT)
WEB = config.ROOT / "web"
CHAT_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quieter logs
        if "/api/events" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    # ------------------------------------------------------------- helpers
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode() or "{}")

    def _file(self, path, content_type=None):
        if not path.exists():
            return self._json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         content_type or mimetypes.guess_type(str(path))[0]
                         or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/api/graph":
                as_of = float(q["as_of"][0]) if "as_of" in q else None
                return self._json(STORE.graph_as_of(as_of))
            if u.path == "/api/events":
                after = int(q.get("after", ["0"])[0])
                return self._json({"events": STORE.events_since(after)[-200:]})
            if u.path == "/api/inspect":
                return self._json(ENGINE.inspect(q["id"][0]) or {"error": "not found"})
            if u.path == "/api/benchmark/status":
                return self._json(benchmark.STATUS)
            if u.path == "/api/meta":
                return self._json({
                    "agents": config.AGENTS, "provider": config.PROVIDER,
                    "model": config.MODEL, "budget": config.RECALL_BUDGET_TOKENS,
                    "sessions": {a: s.get("session", 1)
                                 for a, s in STORE.state["sessions"].items()}})
            if u.path == "/api/demo/replay":
                p = config.RECORDINGS_DIR / "default_replay.json"
                if p.exists():
                    return self._file(p, "application/json")
                return self._json({"error": "no recording — run scripts/record_demo.py "
                                            "once against a live server"}, 404)
            if u.path == "/api/runlog":
                return self._file(STORE.runlog_path, "application/json")
            # static frontend
            rel = u.path.lstrip("/") or "index.html"
            target = (WEB / rel).resolve()
            if WEB.resolve() in target.parents or target == (WEB / "index.html").resolve():
                return self._file(target)
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)

    # --------------------------------------------------------------- POST
    def do_POST(self):
        u = urlparse(self.path)
        try:
            b = self._body()
            if u.path == "/api/chat":
                with CHAT_LOCK:
                    out = run_turn(ENGINE, b.get("agent_id", "A"), b["message"])
                return self._json(out)
            if u.path == "/api/session/new":
                return self._json({"session": new_session(ENGINE, b.get("agent_id", "A"))})
            if u.path == "/api/sleep":
                with CHAT_LOCK:
                    return self._json({"report": ENGINE.sleep()})
            if u.path == "/api/forget/preview":
                return self._json({"preview": ENGINE.forget_preview(b["scope"])})
            if u.path == "/api/forget/confirm":
                return self._json(ENGINE.forget_confirm(b["preview"]))
            if u.path == "/api/benchmark/start":
                started = benchmark.start(STORE)
                return self._json({"started": started})
            if u.path == "/api/marker":
                # act/chapter markers for demo recordings & guided live demos
                STORE.emit("marker", b)
                return self._json({"ok": True})
            if u.path == "/api/reset":
                STORE.reset()
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    """Py3.6-compatible equivalent of http.server.ThreadingHTTPServer (3.7+)."""
    daemon_threads = True
    allow_reuse_address = True


def main():
    port = int(os.getenv("ENGRAM_PORT", "8787"))
    host = os.getenv("ENGRAM_HOST", "0.0.0.0")
    print(f"Engram v0 — provider={config.PROVIDER} model={config.MODEL}")
    if not config.API_KEY:
        print("WARNING: no API key configured (.env) — LLM calls will fail gracefully.")
    print(f"→ http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
