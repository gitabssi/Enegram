"""Engram configuration.

Every LLM call goes through an OpenAI-compatible chat-completions endpoint,
pointed at Qwen on Alibaba Cloud Model Studio (DashScope).
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Minimal .env loader (no dependency on python-dotenv).
def _load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env(ROOT / ".env")

PROVIDER = "qwen"

# Qwen served from Alibaba Cloud Model Studio (DashScope), OpenAI-compatible
# mode. The backend itself is deployed on Alibaba Cloud (ECS).
BASE_URL = os.getenv(
    "QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
MODEL = os.getenv("ENGRAM_MODEL", "qwen-plus")
EXTRA_BODY = {}

# Min seconds between LLM calls (rate-limit protection). 0 for a standard
# DashScope key/quota.
MIN_CALL_INTERVAL = float(os.getenv("ENGRAM_MIN_CALL_INTERVAL", "0"))

# Recall packet hard budget (tokens) — judged theme: token efficiency.
RECALL_BUDGET_TOKENS = int(os.getenv("ENGRAM_RECALL_BUDGET", "1200"))
# How many turns of the *current* session ride along with the recall packet.
SESSION_WINDOW_TURNS = int(os.getenv("ENGRAM_SESSION_WINDOW", "8"))

DATA_DIR = Path(os.getenv("ENGRAM_DATA_DIR", str(ROOT / "data")))
RECORDINGS_DIR = ROOT / "recordings"

AGENTS = {
    "A": {"name": "Planner", "color": "#22d3ee"},
    "B": {"name": "Executor", "color": "#fbbf24"},
}
