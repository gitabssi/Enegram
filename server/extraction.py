"""LLM extraction prompts: ingestion analysis, query analysis, contradiction
judgment, consolidation distillation, benchmark grading. Each has a
deterministic fallback so a failed model call degrades gracefully and never
loses an episode.
"""
import json
import re
import time

from .llm import LLMError

ISO_HINT = "Use ISO dates (YYYY-MM-DD) when a time is stated or clearly implied, else null."


def ingest_extract(llm, text, role, known_entities):
    """One call per ingested turn: importance, entities, declarative facts."""
    sys = (
        "You are the ingestion module of a cognitive memory engine. Analyze ONE "
        "conversation turn and return STRICT JSON:\n"
        "{\"importance\": 1-5 (5 = life-changing personal fact, 1 = chitchat),\n"
        " \"summary\": one-line gist,\n"
        " \"entities\": [{\"name\": str, \"type\": \"person|place|org|thing|concept\"}],\n"
        " \"facts\": [{\"subject\": str, \"relation\": snake_case str, \"object\": str, "
        "\"change_time\": ISO date or null}]}\n"
        "Only extract facts from CLEARLY declarative statements (e.g. 'My daughter's "
        "name is Lina', 'Sara moved to Casablanca last month'). "
        "QUESTIONS AND REQUESTS STATE NOTHING: 'What dose should he take?' → "
        "facts: []. 'Set up the call' → facts: []. NEVER infer a fact from the "
        "known-entities list or from context — only from words asserted in THIS "
        "turn. Relations must be "
        "stable snake_case predicates (lives_in, works_at, name_of_daughter, likes, "
        "allergic_to, medication_dose, age...). Use the SAME predicate for the same "
        "kind of fact every time (a dosage is always medication_dose — this is how "
        "belief revision detects contradictions). Objects must be SHORT canonical "
        "values ('Amlodipine 10mg', 'Beijing'), never sentences. "
        "change_time = when the fact BECAME true if stated. "
        "Entity hygiene: REUSE the exact known entity names below whenever the turn "
        "refers to them — never mint a variant spelling. If the speaker's own name "
        "is known, attribute their facts to that name, NEVER to 'user'/'I'/'me'. "
        "ALWAYS resolve pronouns (he/she/they) and nicknames to the known entity: "
        "'he walks…' about grandfather Chen ⇒ subject 'Chen'; 'Grandpa' and 'Chen' "
        "are ONE entity — use the earliest known name. Never create an entity named "
        "'He', 'She', a bare number, or a bare date/time. "
        "Entities are real people/places/orgs/things — never booleans, dates alone, "
        "or generic words like 'user', 'team', 'answers'. Prefer short canonical "
        "names ('Xixi campus', not 'Alibaba_Xixi_campus'). "
        + ISO_HINT + f" Today is {time.strftime('%Y-%m-%d')}. "
        "Known entities (reuse exact names when the turn refers to them): "
        + ", ".join(known_entities[:60])
    )
    try:
        out = llm.chat_json(
            [{"role": "system", "content": sys},
             {"role": "user", "content": f"[{role} turn]\n{text}"}],
            purpose="ingest_extract")
        out.setdefault("importance", 2)
        out.setdefault("entities", [])
        out.setdefault("facts", [])
        out.setdefault("summary", text[:120])
        out["importance"] = min(5, max(1, int(out["importance"])))
        return out
    except (LLMError, json.JSONDecodeError, ValueError, KeyError):
        ents = [{"name": n, "type": "thing"} for n in known_entities
                if n.lower() in text.lower()][:5]
        return {"importance": 2, "summary": text[:120], "entities": ents, "facts": []}


def query_analyze(llm, query, known_entities):
    sys = (
        "You are the recall module of a memory engine. Analyze the user query and "
        "return STRICT JSON:\n"
        "{\"entities\": [names mentioned or implied],\n"
        " \"intent\": one line,\n"
        " \"time\": null | {\"at\": ISO date} — set when the question asks about a "
        "PAST state ('last year', 'before', 'previously', 'in March'),\n"
        " \"wants_history\": true if asking about a past/previous state}\n"
        + ISO_HINT + f" Today is {time.strftime('%Y-%m-%d')}. "
        "Known entities: " + ", ".join(known_entities[:60])
    )
    try:
        out = llm.chat_json(
            [{"role": "system", "content": sys}, {"role": "user", "content": query}],
            purpose="query_analyze")
        out.setdefault("entities", [])
        out.setdefault("wants_history", False)
        out.setdefault("time", None)
        return out
    except (LLMError, json.JSONDecodeError, ValueError):
        ents = [n for n in known_entities if re.search(r"\b" + re.escape(n.lower()) + r"\b",
                                                       query.lower())]
        past = bool(re.search(r"\b(before|previous|used to|last year|back then)\b",
                              query.lower()))
        return {"entities": ents, "intent": query[:80], "time": None,
                "wants_history": past}


def judge_contradiction(llm, old_fact, new_fact):
    sys = (
        "You are the belief-revision judge of a memory engine. Two facts share a "
        "subject and relation. Decide their relationship. Return STRICT JSON:\n"
        "{\"verdict\": \"contradiction|duplicate|compatible|unclear\",\n"
        " \"rationale\": one sentence,\n"
        " \"change_time\": ISO date when the change happened if inferable, else null}\n"
        "contradiction = both cannot be currently true (the new one supersedes or "
        "disputes the old). duplicate = same information — INCLUDING the same value "
        "at different verbosity ('Amlodipine 10mg' vs 'Amlodipine 10mg daily for "
        "hypertension' are duplicates). compatible = both can hold "
        "(e.g. likes X and likes Y). unclear = cannot tell which is currently true. "
        "IMPORTANT: objects at different granularity of the SAME thing are NOT "
        "contradictions — working at an org and at one of its offices/campuses, "
        "living in a city and in its district: verdict compatible (or duplicate). "
        "Reserve 'unclear' for genuinely irreconcilable same-level claims. "
        + ISO_HINT
    )
    user = (f"OLD (believed since {old_fact.get('valid_from_iso')}): "
            f"{old_fact['subject_name']} {old_fact['relation']} {old_fact['object_name']}\n"
            f"NEW: {new_fact['subject']} {new_fact['relation']} {new_fact['object']} "
            f"(stated change time: {new_fact.get('change_time')})")
    try:
        out = llm.chat_json(
            [{"role": "system", "content": sys}, {"role": "user", "content": user}],
            purpose="judge_contradiction")
        if out.get("verdict") not in ("contradiction", "duplicate", "compatible", "unclear"):
            out["verdict"] = "unclear"
        return out
    except (LLMError, json.JSONDecodeError, ValueError):
        same = (str(new_fact["object"]).strip().lower()
                == str(old_fact["object_name"]).strip().lower())
        return {"verdict": "duplicate" if same else "unclear",
                "rationale": "LLM unavailable; conservative fallback.",
                "change_time": new_fact.get("change_time")}


def consolidate_cluster(llm, episodes):
    """Distill a cluster of episodes into facts + lessons + a summary."""
    sys = (
        "You are the sleep/consolidation module of a cognitive memory engine. Given "
        "raw episodic memories, distill durable knowledge. Return STRICT JSON:\n"
        "{\"summary\": 1-2 sentence summary of the cluster,\n"
        " \"facts\": [{\"subject\": str, \"relation\": snake_case, \"object\": str "
        "(SHORT canonical value, never a sentence), \"change_time\": ISO or null}],\n"
        " \"lessons\": [{\"trigger\": situation description, \"guidance\": what to "
        "do or avoid next time}]}\n"
        "Lessons ONLY from real behavioral patterns in the episodes: a failure "
        "followed by a correction, a repeated user correction, or a strongly stated "
        "preference about HOW the assistant should behave. Otherwise lessons=[]. "
        + ISO_HINT + f" Today is {time.strftime('%Y-%m-%d')}."
    )
    text = "\n".join(f"[{e['id']} {e['agent_id']} {e['role']} "
                     f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(e['ts']))}] "
                     f"{e['content']}" for e in episodes)
    try:
        out = llm.chat_json(
            [{"role": "system", "content": sys}, {"role": "user", "content": text}],
            purpose="consolidate", max_tokens=3000)
        out.setdefault("facts", [])
        out.setdefault("lessons", [])
        out.setdefault("summary", episodes[0]["content"][:100])
        return out
    except (LLMError, json.JSONDecodeError, ValueError):
        return {"summary": " / ".join(e["content"][:60] for e in episodes[:3]),
                "facts": [], "lessons": []}


def grade_answer(llm, question, expected, answer):
    sys = ("You are grading a memory benchmark. Return STRICT JSON: "
           "{\"correct\": true|false, \"reason\": one line}. The answer is correct "
           "if it contains the expected information (paraphrase ok).")
    user = f"Question: {question}\nExpected: {expected}\nAnswer: {answer}"
    try:
        out = llm.chat_json([{"role": "system", "content": sys},
                             {"role": "user", "content": user}], purpose="grade")
        return bool(out.get("correct")), out.get("reason", "")
    except (LLMError, json.JSONDecodeError, ValueError):
        ok = expected.lower() in (answer or "").lower()
        return ok, "string-match fallback"
