"""Persistence: one JSON state file (atomic writes), an append-only memory
event log (events.jsonl — drives the visualization, timeline scrubber, and
Demo Mode recordings), and a run log (runlog.jsonl — every model call's full
context). Ingestion is write-ahead: raw content hits disk before any LLM
processing, so an episode can never be lost.
"""
import json
import os
import threading
import time
from pathlib import Path

from . import decay


def _now():
    return time.time()


EMPTY_STATE = {
    "episodes": {},   # id -> episode
    "entities": {},   # id -> entity node
    "facts": {},      # id -> fact edge
    "lessons": {},    # id -> lesson
    "revisions": [],  # revision events
    "sessions": {},   # agent_id -> {"session": n, "turns": [...]}
    "counters": {"ep": 0, "en": 0, "f": 0, "l": 0},
    "sleep_reports": [],
}


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.events_path = self.dir / "events.jsonl"
        self.runlog_path = self.dir / "runlog.jsonl"
        self.wal_path = self.dir / "ingest.wal"
        self.lock = threading.RLock()
        self.events = []
        self._load()

    # ------------------------------------------------------------- load/save
    def _load(self):
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding='utf-8'))
            for k, v in EMPTY_STATE.items():
                self.state.setdefault(k, json.loads(json.dumps(v)))
        else:
            self.state = json.loads(json.dumps(EMPTY_STATE))
        if self.events_path.exists():
            with open(self.events_path, encoding='utf-8') as f:
                self.events = [json.loads(l) for l in f if l.strip()]

    def save(self):
        with self.lock:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=1), encoding='utf-8')
            os.replace(tmp, self.state_path)

    def reset(self):
        with self.lock:
            self.state = json.loads(json.dumps(EMPTY_STATE))
            self.events = []
            for p in (self.state_path, self.events_path, self.runlog_path, self.wal_path):
                if p.exists():
                    p.unlink()
            self.save()

    # ---------------------------------------------------------------- events
    def emit(self, etype, payload):
        with self.lock:
            ev = {"seq": len(self.events) + 1, "ts": _now(), "type": etype, "payload": payload}
            self.events.append(ev)
            with open(self.events_path, "a", encoding='utf-8') as f:
                f.write(json.dumps(ev) + "\n")
            return ev

    def events_since(self, seq):
        return [e for e in self.events if e["seq"] > seq]

    def runlog(self, entry):
        with self.lock:
            with open(self.runlog_path, "a", encoding='utf-8') as f:
                f.write(json.dumps(entry) + "\n")

    def wal(self, record):
        """Write-ahead record of raw ingestion, before any LLM touches it."""
        with self.lock:
            with open(self.wal_path, "a", encoding='utf-8') as f:
                f.write(json.dumps(record) + "\n")

    # ------------------------------------------------------------------- ids
    def next_id(self, kind):
        with self.lock:
            self.state["counters"][kind] += 1
            return f"{kind}_{self.state['counters'][kind]}"

    # ------------------------------------------------------------- salience
    @staticmethod
    def salience_of(item, now=None):
        now = now or _now()
        return decay.effective_salience(
            item["salience_base"], item.get("importance", 2),
            item["last_touch"], now)

    @staticmethod
    def reinforce(item, now=None):
        now = now or _now()
        item["salience_base"], item["last_touch"] = decay.reinforce(
            item["salience_base"], item.get("importance", 2),
            item["last_touch"], now)

    # ------------------------------------------------------------- entities
    @staticmethod
    def _norm(name):
        import re
        return re.sub(r"[_\s\-]+", " ", str(name).lower()).strip()

    def find_entity(self, name):
        needle = self._norm(name)
        for en in self.state["entities"].values():
            if self._norm(en["name"]) == needle or needle in [
                    self._norm(a) for a in en.get("aliases", [])]:
                return en
        return None

    def upsert_entity(self, name, etype="thing", importance=2):
        with self.lock:
            en = self.find_entity(name)
            now = _now()
            if en:
                self.reinforce(en, now)
                return en, False
            en = {
                "id": self.next_id("en"), "name": name.strip(), "type": etype,
                "aliases": [], "importance": importance,
                "salience_base": 0.8, "last_touch": now, "created_at": now,
            }
            self.state["entities"][en["id"]] = en
            self.emit("entity_created", {"entity": en})
            return en, True

    # ------------------------------------------------- time-travel snapshot
    def graph_as_of(self, t=None):
        """Reconstruct visible memory state at time t (validity intervals +
        recorded creation times; salience recomputed at t)."""
        now = _now()
        t = t or now
        out = {"entities": [], "facts": [], "lessons": [], "episodes": [],
               "revisions": [r for r in self.state["revisions"] if r["ts"] <= t],
               "as_of": t, "now": now}
        for en in self.state["entities"].values():
            if en["created_at"] <= t:
                e = dict(en)
                e["salience"] = round(self.salience_of(en, t), 3)
                out["entities"].append(e)
        for f in self.state["facts"].values():
            if f["created_at"] <= t:
                g = dict(f)
                if f.get("valid_to") and f["valid_to"] <= t:
                    g["status"] = "HISTORICAL"
                elif f["status"] == "HISTORICAL" and (not f.get("valid_to") or f["valid_to"] > t):
                    g["status"] = "ACTIVE"   # was still believed at t
                if f["status"] == "DISPUTED" and f.get("disputed_at", 0) > t:
                    g["status"] = "ACTIVE"
                g["salience"] = round(self.salience_of(f, t), 3)
                out["facts"].append(g)
        for l in self.state["lessons"].values():
            if l["created_at"] <= t:
                m = dict(l)
                m["salience"] = round(self.salience_of(l, t), 3)
                out["lessons"].append(m)
        eps = [e for e in self.state["episodes"].values() if e["ts"] <= t]
        eps.sort(key=lambda e: e["ts"])
        for e in eps[-60:]:
            d = dict(e)
            d["salience"] = round(self.salience_of(e, t), 3)
            d["content"] = d["content"][:160]
            out["episodes"].append(d)
        return out
