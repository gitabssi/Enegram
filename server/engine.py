"""The Engram engine. Narrow interface — remember / recall / sleep / forget /
inspect — so any agent can plug in. Host agents interact with memory
exclusively through this class.
"""
import json
import time

from . import decay
from .store import Store
from .extraction import (ingest_extract, query_analyze, judge_contradiction,
                         consolidate_cluster)
from .tokens import estimate
from . import config


def now():
    return time.time()


def iso(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else None


class Engine:
    def __init__(self, store: Store, llm):
        self.store = store
        self.llm = llm

    # ================================================================ remember
    def remember(self, agent_id, content, role="user", extract_facts=None):
        """Ingest one turn: WAL first, then episode + entity/fact extraction.
        Assistant turns are stored as episodes but don't create semantic facts
        at ingestion (they get distilled at sleep instead)."""
        s = self.store
        s.wal({"ts": now(), "agent_id": agent_id, "role": role, "content": content})
        if extract_facts is None:
            extract_facts = role == "user"

        known = [e["name"] for e in s.state["entities"].values()]
        if role == "user":
            ext = ingest_extract(self.llm, content, role, known)
        else:
            # assistant/summary turns: no LLM spend — link entities by name
            # match; their knowledge gets distilled at sleep, not at ingestion.
            ext = {"importance": 2, "summary": content[:120], "facts": [],
                   "entities": [{"name": n, "type": "thing"} for n in known
                                if n.lower() in content.lower()][:6]}

        ep = {
            "id": s.next_id("ep"), "agent_id": agent_id, "role": role,
            "ts": now(), "content": content, "summary": ext["summary"],
            "entities": [], "importance": ext["importance"],
            "salience_base": 0.4 + 0.12 * ext["importance"],
            "last_touch": now(), "created_at": now(),
            "status": "raw",  # raw | archived | summary
            "session": s.state["sessions"].get(agent_id, {}).get("session", 1),
        }
        for e in ext["entities"]:
            nm = str(e.get("name", "")).strip()
            if (not nm or nm.lower() in ("user", "i", "me", "assistant", "true",
                                         "false", "team", "unknown", "he", "she",
                                         "they", "it", "him", "her")
                    or nm.replace(":", "").replace("-", "").replace(" ", "").isdigit()):
                continue
            en, _ = s.upsert_entity(nm, e.get("type", "thing"), ext["importance"])
            if en["id"] not in ep["entities"]:
                ep["entities"].append(en["id"])
        s.state["episodes"][ep["id"]] = ep
        s.emit("episode_ingested", {"episode": {**ep, "content": ep["content"][:200]}})

        results = {"episode": ep["id"], "facts": [], "revisions": []}
        if extract_facts:
            # hallucination guard: the object's value must actually appear in
            # the turn (extractors love inventing facts from question turns)
            low = content.lower()
            ext["facts"] = [c for c in ext["facts"] if any(
                w in low for w in str(c.get("object", "")).lower().split()
                if len(w) >= 3)]
            for cand in ext["facts"]:
                r = self.assert_fact(cand, provenance=[ep["id"]],
                                     importance=ext["importance"])
                if r:
                    results["facts"].append(r.get("fact", {}).get("id"))
                    if r.get("revision"):
                        results["revisions"].append(r["revision"])
        s.save()
        return results

    # ------------------------------------------------- fact assertion + revision
    JUNK_OBJECTS = {"true", "false", "yes", "no", "unknown", "n/a", "none", ""}

    def assert_fact(self, cand, provenance, importance=3, confidence=0.85):
        """Run a candidate fact through belief revision (§4.3) and store it."""
        s = self.store
        if (str(cand.get("object", "")).strip().lower() in self.JUNK_OBJECTS
                or str(cand.get("subject", "")).strip().lower() in
                ("user", "i", "me", "assistant", "he", "she", "they", "it",
                 "him", "her", "")):
            return None
        subj, _ = s.upsert_entity(cand["subject"], "thing", importance)
        obj, _ = s.upsert_entity(str(cand["object"]), "thing", importance)
        relation = str(cand.get("relation", "related_to")).strip().lower().replace(" ", "_")

        # find ACTIVE/DISPUTED facts with same subject+relation
        existing = [f for f in s.state["facts"].values()
                    if f["subject"] == subj["id"] and f["relation"] == relation
                    and f["status"] in ("ACTIVE", "DISPUTED")]

        for old in existing:
            old_view = {**old,
                        "subject_name": s.state["entities"][old["subject"]]["name"],
                        "object_name": s.state["entities"][old["object"]]["name"],
                        "valid_from_iso": iso(old.get("valid_from"))}
            if old["object"] == obj["id"]:
                # same triple: reinforce, merge provenance
                s.reinforce(old)
                old["confidence"] = min(1.0, old["confidence"] + 0.05)
                old["provenance"] = list(dict.fromkeys(old["provenance"] + provenance))
                s.save()
                return {"fact": old, "revision": None, "duplicate": True}

            verdict = judge_contradiction(self.llm, old_view, cand)
            if verdict["verdict"] == "duplicate":
                s.reinforce(old)
                return {"fact": old, "revision": None, "duplicate": True}
            if verdict["verdict"] == "compatible":
                continue
            if verdict["verdict"] == "contradiction":
                change_ts = _parse_iso(verdict.get("change_time") or cand.get("change_time")) or now()
                old["valid_to"] = change_ts
                old["status"] = "HISTORICAL"
                # single-valued relation: close every other ACTIVE variant too,
                # so no stale sibling belief survives the revision
                for sib in existing:
                    if sib is not old and sib["status"] == "ACTIVE":
                        sib["valid_to"] = change_ts
                        sib["status"] = "HISTORICAL"
                new = self._new_fact(subj, relation, obj, provenance, importance,
                                     confidence, valid_from=change_ts)
                rev = {"ts": now(), "old_fact": old["id"], "new_fact": new["id"],
                       "trigger_episodes": provenance,
                       "rationale": verdict.get("rationale", "")}
                s.state["revisions"].append(rev)
                s.emit("revision", {
                    "old_fact": _fact_view(s, old), "new_fact": _fact_view(s, new),
                    "rationale": rev["rationale"]})
                s.save()
                return {"fact": new, "revision": rev}
            # unclear → DISPUTED, agent must ask the user (§4.3)
            old["status"] = "DISPUTED"
            old["disputed_at"] = now()
            new = self._new_fact(subj, relation, obj, provenance, importance,
                                 confidence, status="DISPUTED")
            new["disputed_at"] = now()
            s.emit("disputed", {"facts": [_fact_view(s, old), _fact_view(s, new)],
                                "rationale": verdict.get("rationale", "")})
            s.save()
            return {"fact": new, "revision": None, "disputed": True}

        new = self._new_fact(subj, relation, obj, provenance, importance, confidence,
                             valid_from=_parse_iso(cand.get("change_time")))
        s.save()
        return {"fact": new, "revision": None}

    def _new_fact(self, subj, relation, obj, provenance, importance, confidence,
                  valid_from=None, status="ACTIVE"):
        s = self.store
        f = {
            "id": s.next_id("f"), "subject": subj["id"], "relation": relation,
            "object": obj["id"], "confidence": confidence,
            # None = the fact was already true when we learned it (unknown
            # start) — critical for answering "before" questions correctly
            "valid_from": valid_from, "valid_to": None,
            "provenance": provenance, "status": status,
            "importance": importance, "salience_base": 0.85,
            "last_touch": now(), "created_at": now(),
        }
        s.state["facts"][f["id"]] = f
        s.emit("fact_created", {"fact": _fact_view(s, f)})
        return f

    # ================================================================== recall
    def recall(self, agent_id, query, budget=None):
        """Build the recall packet under a hard token budget, with real
        spreading activation (the glow the UI shows is this exact set)."""
        s = self.store
        budget = budget or config.RECALL_BUDGET_TOKENS
        known = [e["name"] for e in s.state["entities"].values()]
        qa = query_analyze(self.llm, query, known)

        # --- graph activation: seeds at 1.0, spread over fact edges, depth 2
        activation = {}
        for name in qa["entities"]:
            en = s.find_entity(name)
            if en:
                activation[en["id"]] = 1.0
        frontier = dict(activation)
        for _ in range(2):
            nxt = {}
            for f in s.state["facts"].values():
                w = f["confidence"] * s.salience_of(f)
                for a, b in ((f["subject"], f["object"]), (f["object"], f["subject"])):
                    if a in frontier:
                        spread = frontier[a] * 0.5 * w
                        if spread > activation.get(b, 0) and spread > 0.05:
                            nxt[b] = max(nxt.get(b, 0), spread)
            for k, v in nxt.items():
                activation[k] = max(activation.get(k, 0), v)
            frontier = nxt
            if not frontier:
                break

        time_at = _parse_iso((qa.get("time") or {}).get("at")) if qa.get("time") else None
        wants_history = bool(qa.get("wants_history"))

        # --- candidate facts scored by activation × salience
        cands = []
        for f in s.state["facts"].values():
            act = max(activation.get(f["subject"], 0), activation.get(f["object"], 0))
            if act <= 0:
                continue
            sal = s.salience_of(f)
            ok = False
            if time_at:  # temporal query: interval must cover the asked time
                ok = ((f["valid_from"] is None or f["valid_from"] <= time_at)
                      and (f["valid_to"] is None or f["valid_to"] >= time_at))
            elif wants_history:
                ok = True  # include historical chain so the agent can answer "before"
            else:
                ok = f["status"] in ("ACTIVE", "DISPUTED") and (sal > decay.FLOOR)
            if ok:
                cands.append((act * max(sal, 0.05), "fact", f))

        # --- episode summaries (recent + salient, touching activated entities)
        for e in s.state["episodes"].values():
            if e["status"] == "archived":
                continue
            act = max([activation.get(x, 0) for x in e["entities"]] or [0])
            sal = s.salience_of(e)
            if act > 0.1 and sal > decay.FLOOR:
                cands.append((0.6 * act * sal, "episode", e))

        # --- lessons: entity overlap or trigger keyword overlap
        qwords = set(query.lower().split())
        for l in s.state["lessons"].values():
            if s.salience_of(l) < decay.FLOOR:
                continue
            overlap = len(qwords & set(l["trigger"].lower().split())) / (len(qwords) + 1)
            act = max([activation.get(x, 0) for x in l.get("entities", [])] or [0])
            score = max(overlap * 2.0, act)
            if score > 0.08:
                cands.append((0.9 * max(score, 0.2), "lesson", l))

        cands.sort(key=lambda c: -c[0])

        # --- budget fitting
        packet_lines, recalled, cut = [], {"facts": [], "episodes": [], "lessons": []}, []
        used = 0
        for score, kind, item in cands:
            line = self._render_item(kind, item)
            t = estimate(line)
            if used + t > budget:
                cut.append({"kind": kind, "id": item["id"], "score": round(score, 3)})
                continue
            used += t
            packet_lines.append(line)
            recalled[kind + "s"].append(item["id"])
            s.reinforce(item)  # recall reinforces salience (visible pulse)
            if kind == "lesson":
                item["times_applied"] = item.get("times_applied", 0) + 1

        full_history_tokens = sum(estimate(e["content"]) for e in s.state["episodes"].values()
                                  if e["agent_id"] == agent_id or True)
        packet = {
            "text": "\n".join(packet_lines) if packet_lines else "(no relevant memories)",
            "recalled": recalled, "cut": cut, "tokens": used, "budget": budget,
            "activation": {k: round(v, 3) for k, v in activation.items()},
            "query_analysis": qa, "full_history_tokens": full_history_tokens,
        }
        s.emit("recall", {"agent_id": agent_id, "activation": packet["activation"],
                          "recalled": recalled, "cut": cut, "tokens": used,
                          "budget": budget, "full_history_tokens": full_history_tokens})
        s.save()
        return packet

    def _render_item(self, kind, item):
        s = self.store
        if kind == "fact":
            v = _fact_view(s, item)
            span = (f" (valid {iso(item['valid_from']) or 'earlier'}"
                    f" → {iso(item['valid_to']) or 'now'})")
            tag = {"ACTIVE": "FACT", "HISTORICAL": "HISTORICAL FACT",
                   "DISPUTED": "DISPUTED FACT"}[item["status"]]
            return (f"[{tag} {item['id']} conf={item['confidence']:.2f}]"
                    f" {v['subject_name']} —{item['relation']}→ {v['object_name']}{span}")
        if kind == "episode":
            return (f"[EPISODE {item['id']} {iso(item['ts'])}"
                    f"{' summary' if item['status'] == 'summary' else ''}] {item['summary']}")
        return (f"[LESSON {item['id']} applied {item.get('times_applied', 0)}x, "
                f"helped {item.get('times_helpful', 0)}x] WHEN {item['trigger']} "
                f"THEN {item['guidance']}")

    # =================================================================== sleep
    def sleep(self):
        """Consolidation: cluster → distill facts+lessons → compress+archive →
        decay pass. Returns the sleep report."""
        s = self.store
        t0 = now()
        raw = [e for e in s.state["episodes"].values() if e["status"] == "raw"]
        clusters = _cluster_by_entities(raw)
        report = {"ts": t0, "clusters": len(clusters), "facts_created": [],
                  "facts_revised": [], "lessons": [], "episodes_compressed": 0,
                  "tokens_before": 0, "tokens_after": 0, "decayed": []}

        for cluster in clusters:
            report["tokens_before"] += sum(estimate(e["content"]) for e in cluster)
            out = consolidate_cluster(self.llm, cluster)
            prov = [e["id"] for e in cluster]
            for cand in out["facts"]:
                r = self.assert_fact(cand, provenance=prov, importance=3, confidence=0.8)
                if r and not r.get("duplicate"):
                    (report["facts_revised"] if r.get("revision")
                     else report["facts_created"]).append(r["fact"]["id"])
            for lz in out["lessons"]:
                l = {
                    "id": s.next_id("l"), "trigger": lz["trigger"],
                    "guidance": lz["guidance"], "source_episodes": prov,
                    "entities": list({en for e in cluster for en in e["entities"]}),
                    "confidence": 0.7, "times_applied": 0, "times_helpful": 0,
                    "importance": 4, "salience_base": 0.9,
                    "last_touch": now(), "created_at": now(),
                }
                s.state["lessons"][l["id"]] = l
                s.emit("lesson_created", {"lesson": l})
                report["lessons"].append(l["id"])
            # compress: one summary record replaces the cluster in default recall
            summ = {
                "id": s.next_id("ep"), "agent_id": cluster[0]["agent_id"],
                "role": "summary", "ts": now(), "content": out["summary"],
                "summary": out["summary"],
                "entities": list({en for e in cluster for en in e["entities"]}),
                "importance": max(e["importance"] for e in cluster),
                "salience_base": 0.7, "last_touch": now(), "created_at": now(),
                "status": "summary", "compressed": [e["id"] for e in cluster],
                "session": cluster[0].get("session", 1),
            }
            s.state["episodes"][summ["id"]] = summ
            for e in cluster:
                e["status"] = "archived"
            report["episodes_compressed"] += len(cluster)
            report["tokens_after"] += estimate(out["summary"])

        # facts+lessons tokens count toward "after"
        report["tokens_after"] += sum(
            estimate(self._render_item("fact", f)) for fid in
            report["facts_created"] + report["facts_revised"]
            for f in [s.state["facts"][fid]])

        # decay pass: retire below-floor items
        for coll, kinds in (("episodes", "episode"), ("facts", "fact"), ("lessons", "lesson")):
            for item in s.state[coll].values():
                if item.get("status") in ("archived", "HISTORICAL"):
                    continue
                if decay.is_below_floor(item["salience_base"], item.get("importance", 2),
                                        item["last_touch"], now()):
                    if coll == "episodes" and item["status"] == "raw":
                        item["status"] = "archived"
                        report["decayed"].append(item["id"])
                    elif coll == "lessons":
                        item["status"] = "retired"
                        report["decayed"].append(item["id"])

        s.state["sleep_reports"].append(report)
        s.emit("sleep_report", {"report": report})
        s.save()
        return report

    # ================================================================== forget
    def forget_preview(self, scope_text):
        """Identify the deletion set for an explicit Forget command."""
        s = self.store
        known = [e["name"] for e in s.state["entities"].values()]
        qa = query_analyze(self.llm, f"Forget everything about: {scope_text}", known)
        ents = [s.find_entity(n) for n in qa["entities"]]
        ents = [e for e in ents if e]
        ent_ids = {e["id"] for e in ents}
        facts = [f for f in s.state["facts"].values()
                 if f["subject"] in ent_ids or f["object"] in ent_ids]
        eps = [e for e in s.state["episodes"].values()
               if set(e["entities"]) & ent_ids]
        lessons = [l for l in s.state["lessons"].values()
                   if set(l.get("entities", [])) & ent_ids]
        return {
            "entities": [{"id": e["id"], "name": e["name"]} for e in ents],
            "facts": [_fact_view(s, f) for f in facts],
            "episodes": [{"id": e["id"], "summary": e["summary"]} for e in eps],
            "lessons": [{"id": l["id"], "trigger": l["trigger"]} for l in lessons],
        }

    def forget_confirm(self, preview):
        s = self.store
        for f in preview["facts"]:
            s.state["facts"].pop(f["id"], None)
        for e in preview["episodes"]:
            s.state["episodes"].pop(e["id"], None)
        for l in preview["lessons"]:
            s.state["lessons"].pop(l["id"], None)
        for e in preview["entities"]:
            s.state["entities"].pop(e["id"], None)
        s.emit("forget", {"removed": {k: [x["id"] for x in v]
                                      for k, v in preview.items()}})
        s.save()
        return {"deleted": sum(len(v) for v in preview.values())}

    # ================================================================= inspect
    def inspect(self, item_id):
        s = self.store
        for coll in ("entities", "facts", "lessons", "episodes"):
            if item_id in s.state[coll]:
                item = dict(s.state[coll][item_id])
                item["salience_now"] = round(s.salience_of(s.state[coll][item_id]), 3)
                item["kind"] = coll[:-1]
                if coll == "facts":
                    item.update(_fact_view(s, s.state[coll][item_id]))
                    item["provenance_episodes"] = [
                        {"id": p, "summary": s.state["episodes"][p]["summary"],
                         "ts": iso(s.state["episodes"][p]["ts"])}
                        for p in item.get("provenance", []) if p in s.state["episodes"]]
                if coll == "lessons":
                    item["source"] = [
                        {"id": p, "summary": s.state["episodes"][p]["summary"]}
                        for p in item.get("source_episodes", []) if p in s.state["episodes"]]
                return item
        return None


# ---------------------------------------------------------------- helpers
def _fact_view(store, f):
    ents = store.state["entities"]
    return {**{k: f[k] for k in ("id", "relation", "status", "confidence",
                                 "valid_from", "valid_to", "subject", "object",
                                 "created_at")},
            "subject_name": ents.get(f["subject"], {}).get("name", "?"),
            "object_name": ents.get(f["object"], {}).get("name", "?"),
            "valid_from_iso": iso(f.get("valid_from")),
            "valid_to_iso": iso(f.get("valid_to"))}


def _cluster_by_entities(episodes):
    """Union-find on shared entities → clusters for consolidation."""
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    by_entity = {}
    for e in episodes:
        for en in e["entities"]:
            if en in by_entity:
                union(e["id"], by_entity[en])
            by_entity[en] = e["id"]
        parent.setdefault(e["id"], e["id"])
    groups = {}
    for e in episodes:
        groups.setdefault(find(e["id"]), []).append(e)
    return [sorted(g, key=lambda e: e["ts"]) for g in groups.values()]


def _parse_iso(sv):
    if not sv:
        return None
    sv = str(sv)
    for candidate, fmt in ((sv[:10], "%Y-%m-%d"), (sv[:7], "%Y-%m"), (sv[:4], "%Y")):
        try:
            return time.mktime(time.strptime(candidate, fmt))
        except ValueError:
            continue
    return None
