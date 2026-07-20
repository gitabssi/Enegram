"""Record the demo scenario against a LIVE server (real model calls), then
save the event log as the Demo Mode replay.

Scenario: an elder-care memory companion — where memory is not a feature but
a safety requirement. Grandpa Chen's medication dose changes; a stale memory
is a dangerous memory. Only the USER lines below are scripted; every memory
event, extraction, revision, lesson and reply is genuinely produced by the
engine (spec §11 honesty rule).

Usage:  python3 -m server.main   (terminal 1)
        python3 scripts/record_demo.py [--benchmark]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost:8787"
ROOT = Path(__file__).resolve().parent.parent


def post(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body or {}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode())


def chat(agent, text):
    print(f"  [{agent}] user: {text[:70]}")
    out = post("/api/chat", {"agent_id": agent, "message": text})
    print(f"  [{agent}] agent: {out.get('reply', out)[:70]}")
    return out


def marker(act, title, cn, sub):
    post("/api/marker", {"act": act, "title": title, "cn": cn, "sub": sub})


ACT1 = [  # Learning — the family teaches the companion (incl. a real failure)
    "Hi, I'm Yue. I look after my grandfather Chen — he's 78 and lives with "
    "us in Hangzhou.",
    "Grandpa takes Amlodipine 10mg every morning for his blood pressure. And "
    "critical: he is severely allergic to penicillin.",
    "He walks in the Xixi wetlands every morning from 6 to 7:30 — that walk "
    "is sacred to him.",
    "Can you schedule his health checkup call for 7am tomorrow?",
    "No!! He's on his morning walk at 7am — I just told you. Never schedule "
    "anything for grandpa before 8am. Please remember that permanently.",
]
ACT2 = [  # The update — safety-critical belief revision + the doctor's question
    "Important update: the cardiologist changed grandpa's prescription "
    "yesterday. Amlodipine is now 5mg, not 10mg.",
    "So what dose should he take tomorrow morning?",
    "And what dose was he on last month, when the dizziness episodes started?",
]
ACT3_A = [  # the lesson changes behavior
    "Grandpa needs a follow-up call with the cardiologist this week — set it up.",
]
ACT3_B = [  # collective memory — the caregiver agent prepares the pills
    "Caregiver here — I'm preparing Grandpa Chen's morning medication tray. "
    "What exactly should I prepare, and anything I must never give him?",
]


def main():
    print("Resetting memory…")
    post("/api/reset")

    marker("1", "Learning", "学习", "A family teaches the companion — watch episodes become beliefs")
    print("ACT 1 — learning (Agent A)")
    for t in ACT1:
        chat("A", t)
    print("  sleep #1 (consolidation)")
    rep = post("/api/sleep")["report"]
    print(f"  sleep: {rep['episodes_compressed']} eps, {len(rep['lessons'])} lessons, "
          f"{rep['tokens_before']}→{rep['tokens_after']} tok")

    marker("2", "The update", "修正", "New session. The prescription changed — a stale memory is a dangerous memory")
    print("ACT 2 — revision (Agent A, new session)")
    post("/api/session/new", {"agent_id": "A"})
    for t in ACT2:
        chat("A", t)

    marker("3", "One mind, two agents", "共享记忆", "The caregiver agent prepares the pills — from memory only the family agent was given")
    print("ACT 3 — lesson applied + collective memory (Agent B)")
    for t in ACT3_A:
        chat("A", t)
    post("/api/session/new", {"agent_id": "B"})
    for t in ACT3_B:
        chat("B", t)

    if "--benchmark" in sys.argv:
        print("Benchmark (several minutes)…")
        post("/api/benchmark/start")
        while True:
            st = json.loads(urllib.request.urlopen(BASE + "/api/benchmark/status")
                            .read().decode())
            print("  ", st["state"], st.get("progress", ""))
            if st["state"] in ("done", "error"):
                break
            time.sleep(5)

    events = [json.loads(l) for l in
              (ROOT / "data" / "events.jsonl").read_text(encoding='utf-8').splitlines() if l.strip()]
    out = ROOT / "recordings" / "default_replay.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "recorded_at": time.time(),
        "note": "Genuine recording of a live run; only user lines were scripted.",
        "events": events}, indent=1), encoding='utf-8')
    print(f"\nSaved replay: {out}  ({len(events)} events)")


if __name__ == "__main__":
    main()
