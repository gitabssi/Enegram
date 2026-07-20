"""Host agents: deliberately thin showcases. A turn = recall packet + current
session window ONLY — never full history. The run log proves it.
"""
from . import config
from .llm import LLMError
from .tokens import estimate_messages

PERSONA = (
    "You are {name}, a helpful assistant agent. Your ONLY knowledge about the "
    "user and the world of this conversation comes from the MEMORY section below "
    "and the current session's messages. Rules:\n"
    "- Never invent personal facts not present in memory.\n"
    "- Facts are tagged: FACT (currently true), HISTORICAL FACT (was true during "
    "its validity interval — use these for questions about the past), DISPUTED "
    "FACT (conflicting information — if relevant, tell the user you have "
    "conflicting information and ask them to confirm which is true).\n"
    "- LESSON entries are behavioral rules you previously learned; follow them.\n"
    "- If memory doesn't contain the answer, say you don't remember.\n"
    "- Be concise (1-3 sentences unless asked for more).\n"
    "- Speak naturally: NEVER mention memory ids, tags, brackets or the word "
    "'FACT'/'EPISODE'/'LESSON' in replies — the memory receipt is shown to the "
    "user separately.\n\n"
    "MEMORY (recall packet, {tokens} tokens of {budget} budget):\n{memory}"
)


def run_turn(engine, agent_id, message):
    s = engine.store
    sess = s.state["sessions"].setdefault(agent_id, {"session": 1, "turns": []})

    engine.remember(agent_id, message, role="user")
    s.emit("chat", {"agent_id": agent_id, "role": "user", "text": message})

    packet = engine.recall(agent_id, message)
    acfg = config.AGENTS.get(agent_id, {"name": f"Agent {agent_id}"})
    system = PERSONA.format(name=acfg["name"], memory=packet["text"],
                            tokens=packet["tokens"], budget=packet["budget"])

    window = sess["turns"][-config.SESSION_WINDOW_TURNS:]
    messages = ([{"role": "system", "content": system}] + window
                + [{"role": "user", "content": message}])
    try:
        reply = engine.llm.chat(messages, purpose=f"agent_turn:{agent_id}",
                                temperature=0.3, max_tokens=2000)
    except LLMError as e:
        reply = f"(model unavailable: {e})"

    sess["turns"] += [{"role": "user", "content": message},
                      {"role": "assistant", "content": reply}]
    engine.remember(agent_id, reply, role="assistant", extract_facts=False)

    # lessons injected + turn completed → they carried their weight this turn
    for lid in packet["recalled"]["lessons"]:
        l = s.state["lessons"].get(lid)
        if l:
            l["times_helpful"] = l.get("times_helpful", 0) + 1

    receipt = {
        "facts": [engine._render_item("fact", s.state["facts"][i])
                  for i in packet["recalled"]["facts"] if i in s.state["facts"]],
        "episodes": [engine._render_item("episode", s.state["episodes"][i])
                     for i in packet["recalled"]["episodes"] if i in s.state["episodes"]],
        "lessons": [engine._render_item("lesson", s.state["lessons"][i])
                    for i in packet["recalled"]["lessons"] if i in s.state["lessons"]],
        "ids": packet["recalled"], "cut": packet["cut"],
    }
    ctx_tokens = estimate_messages(messages)
    gauge = {
        "packet_tokens": packet["tokens"], "budget": packet["budget"],
        "context_tokens": ctx_tokens,
        # fair comparison: identical turn overhead, recall packet swapped for
        # the full episodic transcript
        "full_history_tokens": ctx_tokens - packet["tokens"]
                               + packet["full_history_tokens"],
        "activation": packet["activation"],
    }
    s.emit("chat", {"agent_id": agent_id, "role": "assistant", "text": reply,
                    "receipt": receipt, "gauge": gauge})
    s.save()
    return {"reply": reply, "receipt": receipt, "gauge": gauge}


def new_session(engine, agent_id):
    s = engine.store
    sess = s.state["sessions"].setdefault(agent_id, {"session": 1, "turns": []})
    sess["session"] += 1
    sess["turns"] = []
    s.emit("session", {"agent_id": agent_id, "session": sess["session"]})
    s.save()
    return sess["session"]
