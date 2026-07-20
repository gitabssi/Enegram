# 🧠 Engram 忆枢 — a mind, not a database

Engram is a cognitive memory engine that gives any agent (or team of agents) real memory: experiences become beliefs, beliefs get **revised** when contradicted (with temporal truth — it knows what's true *now* AND what was true *before*), and a **sleep cycle** distills episodes into facts and behavioral lessons while stale memories decay away. The whole process is **watchable live** on a cinematic animated memory graph — every glow, fade, and snapped edge renders the engine's true internal state.

> **Hackathon**: Global AI Hackathon Series with Qwen Cloud — Track 1: MemoryAgent.
> Built entirely on **Qwen** (via Alibaba Cloud Model Studio / DashScope) and deployed on **Alibaba Cloud**.

## Why it's memory, not retrieval

| Capability | Naive RAG | Engram |
|---|---|---|
| Recall planted facts | ✅ | ✅ |
| Detect contradiction, demote old belief to HISTORICAL with a validity interval | ❌ | ✅ |
| Answer "what was true **before**?" | ❌ | ✅ |
| Distill failures into lessons that change future behavior | ❌ | ✅ |
| Compress 30+ episodes into a handful of facts (token budget enforced per turn) | ❌ | ✅ |
| Forget on command, with provenance-traced preview | ❌ | ✅ |

## Architecture

```
┌───────────── web/ (vanilla JS canvas) ─────────────┐
│  chat ⇄ living memory graph · timeline · receipts  │
└───────────────┬────────────────────────────────────┘
                │ JSON API (stdlib http server)
┌───────────────▼────────────────────────────────────┐
│ server/engine.py — the Engram interface            │
│   remember() recall() sleep() forget() inspect()   │
│ ┌──────────┐ ┌──────────┐ ┌───────────┐            │
│ │ episodic │ │ semantic │ │procedural │  + decay   │
│ │ stream   │ │ graph    │ │ lessons   │  + budget  │
│ └──────────┘ └──────────┘ └───────────┘            │
│  belief revision · consolidation · activation      │
└───────────────┬────────────────────────────────────┘
                │ server/llm.py (OpenAI-compatible)
                │ Qwen @ Alibaba Cloud Model Studio (DashScope)
```

Memory model: **episodes** (timestamped, write-ahead-logged, never lost) → consolidated during **sleep** into **facts** (subject–relation–object edges with confidence, validity interval `[from, to|open]`, status ACTIVE/HISTORICAL/DISPUTED, provenance) and **lessons** (trigger → guidance, with an applied/helped scoreboard). Everything carries a deterministic, unit-tested salience decay (`server/decay.py`).

## Quickstart (zero pip dependencies)

```bash
cp .env.example .env          # add your DashScope API key
python3 -m server.main        # → http://localhost:8787
```

Talk to the agent. Watch the graph grow. Then:
- **☾ sleep** — consolidation report with the compression ratio.
- Tell it something that contradicts an earlier fact — watch the revision animation and ask about the past.
- **▤ benchmark** — full-history vs naive-RAG vs Engram, LLM-graded, with token costs.
- **⌫ forget** — preview + hard delete.
- Drag the **timeline** to time-travel through memory states.

## Demo Mode (offline replay)

Record once against a live server, then replay forever with **zero API dependency**:

```bash
python3 -m server.main                 # terminal 1
python3 scripts/record_demo.py         # terminal 2 — scripted user lines, real engine
```

The bundled scenario (`server/scenario_default.json`) is an elder-care memory companion: a family teaches it about a grandparent's medication and routines, a prescription dose changes (belief revision — a stale memory here would be dangerous, not just wrong), and a second agent correctly prepares medication from memory only the first agent was given (shared memory).

Then click **▶ Watch it think** on the hero screen (or **▶ demo** in the tools). The replay is labeled 真实录制回放 — "replay of recorded session": every event in it was genuinely produced by the engine on Qwen; only the user's lines were scripted.

URL shortcuts for judging/filming: `/?demo` autostarts the replay, `/?stage` skips the hero screen.

## Benchmark

Click **▤ benchmark** (or POST `/api/benchmark/start`). It runs the bundled scenario through three strategies and LLM-grades the answers against a bundled answer key: full history (accurate but token-brutal), naive RAG (cheap but fails contradiction/temporal questions), and Engram (near-full-history accuracy at near-RAG cost).

## Alibaba Cloud deployment

All model calls go through `server/llm.py` (OpenAI-compatible), pointed at Qwen. In `.env`:

```bash
DASHSCOPE_API_KEY=sk-...
ENGRAM_MODEL=qwen-plus
```

`server/config.py` is the deployment-proof file identifying the Alibaba Cloud Model Studio endpoint (`dashscope-intl.aliyuncs.com`). The backend itself runs on Alibaba Cloud ECS — no external dependencies to install, so deployment is `git clone`, drop `.env`, `python3 -m server.main` (or run it as a systemd service), open the port in the instance's security group.

## Observability & honesty

- `data/runlog.jsonl` — every model call's **full assembled context**, proving agent turns contain only the recall packet + current session window, never full history. Download via `/api/runlog`.
- `data/events.jsonl` — every memory event (drives the visualization 1:1).
- `data/ingest.wal` — write-ahead log; ingestion never loses an episode.
- Per-turn context gauge in the UI: recall packet vs budget vs what full history would have cost.

## Tests

```bash
python3 -m unittest discover tests
```

## License

MIT — see [LICENSE](LICENSE).
