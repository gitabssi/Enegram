"""One-click benchmark: the same scripted scenario through three memory
strategies — (a) full history, (b) naive RAG, (c) Engram — then LLM-graded
against the bundled answer key. Honest by construction: the Engram run uses
the real engine end-to-end in an isolated data dir.
"""
import json
import re
import threading
import time
from pathlib import Path

from . import config
from .agent import run_turn, new_session
from .engine import Engine
from .extraction import grade_answer
from .llm import LLM, LLMError
from .store import Store
from .tokens import estimate, estimate_messages

SCENARIO = json.loads((Path(__file__).parent / "scenario_default.json").read_text(encoding='utf-8'))

STATUS = {"state": "idle", "progress": "", "result": None, "error": None}
_lock = threading.Lock()


def start(main_store):
    with _lock:
        if STATUS["state"] == "running":
            return False
        STATUS.update({"state": "running", "progress": "starting", "result": None,
                       "error": None})
    threading.Thread(target=_run, args=(main_store,), daemon=True).start()
    return True


def _answer(llm, context_msgs, question):
    msgs = context_msgs + [{"role": "user", "content": question}]
    try:
        reply = llm.chat(msgs, purpose="benchmark_answer", temperature=0.2,
                         max_tokens=1500)
    except LLMError as e:
        reply = f"(error: {e})"
    return reply, estimate_messages(msgs)


def _run(main_store):
    try:
        llm = LLM(runlog=main_store.runlog)
        turns = [t for sess in SCENARIO["sessions"] for t in sess]
        questions = SCENARIO["questions"]
        result = {"strategies": {}, "scenario": SCENARIO["name"],
                  "questions": [q["q"] for q in questions]}

        # ---------------- (c) Engram: real engine, isolated data dir ---------
        STATUS["progress"] = "engram: replaying scenario through the real engine"
        bench_store = Store(config.DATA_DIR / "bench")
        bench_store.reset()
        eng = Engine(bench_store, llm)
        transcript = []
        for si, sess in enumerate(SCENARIO["sessions"]):
            if si > 0:
                eng.sleep()
                new_session(eng, "A")
            for ti, turn in enumerate(sess):
                STATUS["progress"] = f"engram: session {si+1} turn {ti+1}"
                out = run_turn(eng, "A", turn)
                transcript += [{"role": "user", "content": turn},
                               {"role": "assistant", "content": out["reply"]}]
        eng.sleep()
        new_session(eng, "A")

        engram_answers, engram_tokens = [], 0
        for q in questions:
            STATUS["progress"] = f"engram: answering '{q['q'][:40]}'"
            out = run_turn(eng, "A", q["q"])
            engram_answers.append(out["reply"])
            engram_tokens += out["gauge"]["context_tokens"]
            new_session(eng, "A")

        # ---------------- (a) full history ----------------------------------
        full_answers, full_tokens = [], 0
        base = [{"role": "system",
                 "content": "You are a helpful assistant. The full conversation "
                            "history follows."}] + transcript
        for q in questions:
            STATUS["progress"] = f"full-history: '{q['q'][:40]}'"
            a, t = _answer(llm, base, q["q"])
            full_answers.append(a)
            full_tokens += t

        # ---------------- (b) naive RAG: flat chunks, top-k overlap ----------
        chunks = [t for t in turns]
        rag_answers, rag_tokens = [], 0
        for q in questions:
            STATUS["progress"] = f"naive-rag: '{q['q'][:40]}'"
            qw = set(re.findall(r"\w+", q["q"].lower()))
            scored = sorted(chunks,
                            key=lambda c: -len(qw & set(re.findall(r"\w+", c.lower()))))
            ctx = [{"role": "system",
                    "content": "You are a helpful assistant. Retrieved memory "
                               "chunks (top-k, most relevant first):\n"
                               + "\n".join(f"- {c}" for c in scored[:6])}]
            a, t = _answer(llm, ctx, q["q"])
            rag_answers.append(a)
            rag_tokens += t

        # ---------------- grading -------------------------------------------
        STATUS["progress"] = "grading answers"
        for name, answers, tok in (("full_history", full_answers, full_tokens),
                                   ("naive_rag", rag_answers, rag_tokens),
                                   ("engram", engram_answers, engram_tokens)):
            rows = []
            for q, a in zip(questions, answers):
                ok, why = grade_answer(llm, q["q"], q["expect"], a)
                rows.append({"type": q["type"], "q": q["q"], "answer": a,
                             "correct": ok, "why": why})
            result["strategies"][name] = {
                "rows": rows,
                "accuracy": round(sum(r["correct"] for r in rows) / len(rows), 2),
                "tokens": tok,
            }

        e = result["strategies"]["engram"]
        f = result["strategies"]["full_history"]
        r = result["strategies"]["naive_rag"]
        result["verdict"] = (
            f"Engram: {int(e['accuracy']*100)}% accuracy at {e['tokens']:,} context "
            f"tokens — vs full history {int(f['accuracy']*100)}% at {f['tokens']:,} "
            f"and naive RAG {int(r['accuracy']*100)}% at {r['tokens']:,}.")
        STATUS.update({"state": "done", "result": result, "progress": "done"})
        main_store.emit("benchmark_result", {"result": result})
    except Exception as e:  # noqa: BLE001
        STATUS.update({"state": "error", "error": str(e)})
