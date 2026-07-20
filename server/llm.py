"""Single LLM gateway. Every model call in Engram goes through here:
retry-once, graceful failure, and run-log evidence (full assembled context
per call — proof that agent turns never contain full history).
"""
import json
import re
import threading
import time
import urllib.request
import urllib.error

from . import config
from .tokens import estimate_messages


class LLMError(Exception):
    pass


class _Throttle:
    """Global min-interval between calls (free-tier RPM limits)."""
    def __init__(self, interval):
        self.interval = interval
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self):
        if self.interval <= 0:
            return
        with self.lock:
            dt = self.last + self.interval - time.time()
            if dt > 0:
                time.sleep(dt)
            self.last = time.time()


_THROTTLE = _Throttle(config.MIN_CALL_INTERVAL)


class LLM:
    def __init__(self, base_url=None, api_key=None, model=None, extra_body=None, runlog=None):
        self.base_url = (base_url or config.BASE_URL).rstrip("/")
        self.api_key = api_key or config.API_KEY
        self.model = model or config.MODEL
        self.extra_body = extra_body if extra_body is not None else config.EXTRA_BODY
        self.runlog = runlog  # callable(entry_dict) or None

    # ------------------------------------------------------------------
    def chat(self, messages, purpose="generic", temperature=0.0, max_tokens=2048):
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        body.update(self.extra_body)
        last_err = None
        for attempt in (1, 2, 3, 4):
            _THROTTLE.wait()
            t0 = time.time()
            try:
                req = urllib.request.Request(
                    self.base_url + "/chat/completions",
                    data=json.dumps(body).encode(),
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.loads(r.read().decode())
                text = data["choices"][0]["message"].get("content") or ""
                usage = data.get("usage", {})
                self._log(purpose, messages, text, usage, time.time() - t0, attempt)
                return text
            except Exception as e:  # noqa: BLE001 — degrade gracefully upstream
                last_err = e
                detail = ""
                wait_s = 2.0 * attempt
                if isinstance(e, urllib.error.HTTPError):
                    try:
                        detail = e.read().decode()[:600]
                    except Exception:
                        pass
                    if e.code == 429:
                        m = re.search(r"retry in ([\d.]+)s", detail, re.I)
                        wait_s = float(m.group(1)) + 1.5 if m else 15.0 * attempt
                self._log(purpose, messages, f"ERROR attempt {attempt}: {e} {detail}",
                          {}, time.time() - t0, attempt, error=True)
                if attempt < 4:
                    time.sleep(wait_s)
        raise LLMError(f"LLM call failed ({purpose}): {last_err}")

    def chat_json(self, messages, purpose="generic", temperature=0.0, max_tokens=2048):
        text = self.chat(messages, purpose, temperature, max_tokens)
        return parse_json(text)

    # ------------------------------------------------------------------
    def _log(self, purpose, messages, response, usage, dt, attempt, error=False):
        if not self.runlog:
            return
        self.runlog({
            "ts": time.time(),
            "kind": "model_call",
            "purpose": purpose,
            "model": self.model,
            "attempt": attempt,
            "error": error,
            "latency_s": round(dt, 2),
            "context_tokens_est": estimate_messages(messages),
            "usage": usage,
            "messages": messages,   # full assembled context — judge-facing evidence
            "response": response,
        })


def parse_json(text):
    """Parse JSON out of an LLM reply, tolerating code fences and prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last resort: first {...} or [...] block
        m = re.search(r"[\[{].*[\]}]", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise
